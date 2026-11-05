#!/usr/bin/env python3
"""
Nifty Exit Odds - live scanner for your Groww account (READ-ONLY)

What it does
  * Signs in to the official Groww Trade API with your TOTP key.
  * Fetches the live Nifty option chain for one expiry.
  * For every call and put near the current price it estimates:
      - the chance the premium reaches your profit target (e.g. +30%)
        BEFORE it falls to your stop-loss (e.g. -20%), within your holding time
      - the chance of the trade being in profit if held to expiry
      - the average result per trade after charges
  * Prints a table and saves a JSON file you can paste into the
    "Nifty Exit Odds" web page for a clickable view.

What it never does
  * It never places, modifies or cancels any order. There is no order code in it.

How the odds are worked out
  The option's own price tells us how much movement the market expects
  (implied volatility). The script simulates thousands of possible Nifty paths
  with that volatility, re-prices the option every 5 minutes along each path,
  and counts which exit happens first. With "--view 0" (the default) it assumes
  no direction: these are the market's odds, not a forecast. Put your own
  expected move in points with --view to see how much it changes the result.

Setup (once)
  1. Active Groww Trading API subscription (needed for live data):
     https://groww.in/user/profile/trading-apis
  2. On https://groww.in/trade-api/api-keys choose "Generate TOTP token".
     Copy the TOTP token and the TOTP secret.
  3. pip install growwapi pyotp numpy
  4. Put the two values in a file named groww_keys.txt next to this script:
         GROWW_TOTP_TOKEN=paste_token_here
         GROWW_TOTP_SECRET=paste_secret_here
     (or set them as environment variables). Never share this file.

Run
  python nifty_exit_odds.py                       # next weekly expiry, +30% / -20%, 60 min hold
  python nifty_exit_odds.py --target 40 --stop 25 --hold 30
  python nifty_exit_odds.py --expiry 2026-11-10 --loop 5   # refresh every 5 minutes
  python nifty_exit_odds.py --demo                # try it without Groww (made-up prices)
  python nifty_exit_odds.py --out docs/data/latest.json   # what the GitHub workflow runs
"""
import argparse
import datetime as dt
import json
import math
import os
import sys
import time

import numpy as np

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SESSION_MIN = 375          # 09:15 to 15:30
YEAR_DAYS = 252            # trading days per year (volatility is annualised on this basis)
STEP_MIN = 5               # re-price the option every 5 minutes


# ---------------------------------------------------------------- maths
def ncdf(x):
    """Standard normal CDF (vectorised, max error ~1e-7)."""
    x = np.asarray(x, dtype=float)
    z = np.abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * z)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                - 0.284496736) * t + 0.254829592) * t * np.exp(-z * z)
    return 0.5 * (1.0 + np.sign(x) * y)


def bs_price(call, S, K, T, r, sig):
    S = np.asarray(S, dtype=float)
    if T <= 1e-9:
        return np.maximum(S - K, 0.0) if call else np.maximum(K - S, 0.0)
    sq = sig * math.sqrt(T)
    d1 = (np.log(S / K) + (r + 0.5 * sig * sig) * T) / sq
    d2 = d1 - sq
    df = math.exp(-r * T)
    if call:
        return S * ncdf(d1) - K * df * ncdf(d2)
    return K * df * ncdf(-d2) - S * ncdf(-d1)


def implied_vol(call, S, K, T, r, P):
    lo, hi = 0.005, 3.0
    if float(bs_price(call, S, K, T, r, lo)) > P or float(bs_price(call, S, K, T, r, hi)) < P:
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if float(bs_price(call, S, K, T, r, mid)) > P:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def prob_profit_at_expiry(call, S, K, P, T, r, sig):
    be = K + P if call else K - P
    if be <= 0:
        return 1.0
    d2 = (math.log(S / be) + (r - 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
    return float(ncdf(d2)) if call else float(ncdf(-d2))


def exit_odds(call, S0, K, P, T0, r, sig, hold_min, Z, view_pts, tgt, stop,
              lot_units, charges):
    """Simulate holding the option for hold_min trading minutes with a target and a stop."""
    n, steps = Z.shape
    hold_years = min(hold_min / SESSION_MIN / YEAR_DAYS, T0)
    dt_ = hold_years / steps
    mu = math.log(max(1e-6, 1.0 + view_pts / S0)) / hold_years
    incr = (mu - 0.5 * sig * sig) * dt_ + sig * math.sqrt(dt_) * Z
    S = S0 * np.exp(np.cumsum(incr, axis=1))

    up, dn = P * (1.0 + tgt), P * (1.0 - stop)
    exit_px = np.empty(n)
    outcome = np.zeros(n, dtype=int)            # 1 = target, -1 = stop, 0 = time exit
    alive = np.ones(n, dtype=bool)
    for j in range(steps):
        idx = np.nonzero(alive)[0]
        if idx.size == 0:
            break
        Tj = max(T0 - (j + 1) * dt_, 0.0)
        px = bs_price(call, S[idx, j], K, Tj, r, sig)
        hit_up = px >= up
        hit_dn = (px <= dn) & ~hit_up
        exit_px[idx[hit_up]] = up               # limit order at the target
        outcome[idx[hit_up]] = 1
        exit_px[idx[hit_dn]] = px[hit_dn]       # stop fills at market (can be worse than the stop)
        outcome[idx[hit_dn]] = -1
        alive[idx[hit_up | hit_dn]] = False
        if j == steps - 1:
            rest = np.nonzero(alive)[0]
            exit_px[rest] = bs_price(call, S[rest, j], K, Tj, r, sig)

    pnl = (exit_px - P) * lot_units - charges
    return {
        "p_target": float(np.mean(outcome == 1)),
        "p_stop": float(np.mean(outcome == -1)),
        "p_time": float(np.mean(outcome == 0)),
        "avg_pnl": float(np.mean(pnl)),
        "p_profit": float(np.mean(pnl > 0)),
    }


# ---------------------------------------------------------------- time
def trading_days_left(now, expiry_date):
    """Trading sessions (fractional) from now until 15:30 on expiry day. Ignores exchange holidays."""
    open_t = now.replace(hour=9, minute=15, second=0, microsecond=0)
    close_t = now.replace(hour=15, minute=30, second=0, microsecond=0)
    today = 0.0
    if now.weekday() < 5 and now < close_t:
        today = (close_t - max(now, open_t)).total_seconds() / 60.0 / SESSION_MIN
    if expiry_date < now.date():
        return 0.0
    if expiry_date == now.date():
        return max(today, 1.0 / SESSION_MIN)
    d, days = now.date() + dt.timedelta(days=1), 0
    while d <= expiry_date:
        if d.weekday() < 5:
            days += 1
        d += dt.timedelta(days=1)
    return today + days


def next_tuesday(now):
    d = now.date()
    ahead = (1 - d.weekday()) % 7
    if ahead == 0 and now.time() >= dt.time(15, 30):
        ahead = 7
    return d + dt.timedelta(days=ahead)


# ---------------------------------------------------------------- data
def load_keys():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "groww_keys.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    token, secret = os.environ.get("GROWW_TOTP_TOKEN"), os.environ.get("GROWW_TOTP_SECRET")
    if not token or not secret:
        sys.exit("Missing GROWW_TOTP_TOKEN / GROWW_TOTP_SECRET. See the setup notes at the top of this file.")
    return token, secret


def groww_client():
    try:
        import pyotp
        from growwapi import GrowwAPI
    except ImportError:
        sys.exit("Install the packages first:  pip install growwapi pyotp numpy")
    token, secret = load_keys()
    access = GrowwAPI.get_access_token(api_key=token, totp=pyotp.TOTP(secret).now())
    return GrowwAPI(access)


def fetch_chain(groww, expiry):
    raw = groww.get_option_chain(exchange=groww.EXCHANGE_NSE, underlying="NIFTY",
                                 expiry_date=expiry.isoformat())
    spot = float(raw["underlying_ltp"])
    rows = []
    for k, side in raw.get("strikes", {}).items():
        ce, pe = side.get("CE") or {}, side.get("PE") or {}
        rows.append({"k": float(k),
                     "ce": ce.get("ltp"), "pe": pe.get("ltp"),
                     "ceOI": ce.get("open_interest"), "peOI": pe.get("open_interest")})
    return spot, sorted(rows, key=lambda r: r["k"])


def demo_chain(days_left):
    """Made-up but realistic chain, for trying the script without Groww."""
    spot, T = 25850.0, days_left / YEAR_DAYS
    rows = []
    for k in range(25000, 26750, 50):
        skew = 0.13 + 0.00004 * max(spot - k, 0) + 0.00001 * max(k - spot, 0)
        ce = round(float(bs_price(True, spot, k, T, 0.0, skew)) * 20) / 20
        pe = round(float(bs_price(False, spot, k, T, 0.0, skew)) * 20) / 20
        rows.append({"k": float(k), "ce": max(ce, 0.05), "pe": max(pe, 0.05), "ceOI": None, "peOI": None})
    return spot, rows


# ---------------------------------------------------------------- report
def analyse(spot, rows, days_left, a):
    T0 = days_left / YEAR_DAYS
    hold = min(a.hold, days_left * SESSION_MIN)
    steps = max(3, min(150, round(hold / STEP_MIN))) 
    Z = np.random.default_rng(7).standard_normal((a.paths, steps))  # same paths for every option
    for row in rows:
         # Same paths for every option, in mirrored pairs (z and -z) so up and down moves balance exactly.
    half = np.random.default_rng(7).standard_normal((max(1, a.paths // 2), steps))
    Z = np.vstack([half, -half])
        if abs(row["k"] - spot) > a.range:
            continue
        rec = {"k": row["k"]}
        for side, call in (("ce", True), ("pe", False)):
            P = row.get(side)
            if not P or P < a.min_premium:
                continue
            sig = implied_vol(call, spot, row["k"], T0, a.rate / 100, P)
            if not sig:
                continue
            res = exit_odds(call, spot, row["k"], P, T0, a.rate / 100, sig, hold, Z, a.view,
                            a.target / 100, a.stop / 100, a.lot * a.lots, a.charges)
            res["ltp"] = P
            res["iv"] = sig
            res["p_expiry"] = prob_profit_at_expiry(call, spot, row["k"], P, T0, a.rate / 100, sig)
            rec[side] = res
        if "ce" in rec or "pe" in rec:
            out.append(rec)
    return out


def fmt_side(r):
    if not r:
        return " " * 34
    return f"{r['ltp']:>8.2f} {r['p_target']*100:>5.0f}% {r['p_stop']*100:>5.0f}% {r['avg_pnl']:>+8.0f}  "


def print_table(spot, expiry, days_left, results, a):
    now = dt.datetime.now(IST).strftime("%d %b %Y %H:%M IST")
    print(f"\nNIFTY {spot:,.2f}   expiry {expiry}   {days_left:.2f} trading days left   ({now})")
    print(f"Plan: exit at +{a.target:g}% or -{a.stop:g}% or after {a.hold:g} min | "
          f"view {a.view:+g} pts | {a.lots} lot x {a.lot} | charges Rs {a.charges:g}")
    print("-" * 82)
    print(f"{'CALL (CE)':^34}|{'STRIKE':^9}|{'PUT (PE)':^34}")
    print(f"{'LTP':>8} {'Tgt%':>6} {'Stop%':>6} {'Avg Rs':>8}  |{'':^9}|{'LTP':>8} {'Tgt%':>6} {'Stop%':>6} {'Avg Rs':>8}")
    print("-" * 82)
    for rec in results:
        atm = " *" if abs(rec["k"] - spot) < 25 else "  "
        print(f"{fmt_side(rec.get('ce'))}|{rec['k']:>7.0f}{atm}|{fmt_side(rec.get('pe'))}")
    print("-" * 82)
    print("Tgt% = chance the target is hit before the stop. Stop% = chance the stop is hit first.")
    print("Avg Rs = average result per trade after charges over all simulated paths.")
    print("These are the market's own odds (from implied volatility), not a prediction.")
    print("A negative Avg Rs with --view 0 is normal: it is the cost of buying options with no edge.\n")


def save_json(spot, expiry, days_left, rows, a):
    payload = {
        "source": "nifty_exit_odds.py",
        "underlying": "NIFTY",
        "spot": spot,
        "expiry": expiry.isoformat(),
        "trading_days_left": round(days_left, 4),
        "fetched": dt.datetime.now(IST).isoformat(timespec="seconds"),
        "demo": bool(a.demo),
        "strikes": [r for r in rows if abs(r["k"] - spot) <= a.range],
    }
    name = a.out or f"nifty_chain_{dt.datetime.now(IST).strftime('%Y%m%d_%H%M')}.json"
    folder = os.path.dirname(name)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(name, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"Saved {name}")


def main():
    p = argparse.ArgumentParser(description="Read-only Nifty option odds scanner for Groww.")
    p.add_argument("--expiry", help="YYYY-MM-DD (default: next Tuesday, Nifty weekly expiry)")
    p.add_argument("--target", type=float, default=30, help="profit target, %% of premium (default 30)")
    p.add_argument("--stop", type=float, default=20, help="stop-loss, %% of premium (default 20)")
    p.add_argument("--hold", type=float, default=60, help="maximum holding time in minutes (default 60)")
    p.add_argument("--view", type=float, default=0, help="your expected Nifty move over the hold, in points (default 0)")
    p.add_argument("--lot", type=int, default=65, help="lot size - check the current one on Groww (default 65)")
    p.add_argument("--lots", type=int, default=1)
    p.add_argument("--charges", type=float, default=60, help="round-trip charges per trade in Rs (default 60)")
    p.add_argument("--range", type=float, default=500, help="strikes within this many points of spot (default 500)")
    p.add_argument("--min-premium", type=float, default=5, dest="min_premium")
    p.add_argument("--rate", type=float, default=0, help="interest rate %% (default 0, keeps short holds free of carry effects)")
    p.add_argument("--paths", type=int, default=4000)
    p.add_argument("--loop", type=float, default=0, help="refresh every N minutes (0 = run once)")
    p.add_argument("--demo", action="store_true", help="use made-up prices instead of Groww")
    p.add_argument("--out", help="save the JSON to this path (default: nifty_chain_<time>.json)")
    a = p.parse_args()

    now = dt.datetime.now(IST)
    auto_expiry = not a.expiry
    expiry = dt.date.fromisoformat(a.expiry) if a.expiry else next_tuesday(now)
    groww = None if a.demo else groww_client()

    while True:
        now = dt.datetime.now(IST)
        days_left = trading_days_left(now, expiry)
        if days_left <= 0:
            sys.exit(f"Expiry {expiry} has passed. Use --expiry with a later date.")
        try:
            if a.demo:
                spot, rows = demo_chain(days_left)
            else:
                spot, rows = fetch_chain(groww, expiry)
                # A holiday moves the weekly expiry a day or two earlier: try those dates too.
                shift = 0
                while auto_expiry and not rows and shift < 2:
                    shift += 1
                    candidate = expiry - dt.timedelta(days=shift)
                    if candidate < now.date():
                        break
                    spot, rows = fetch_chain(groww, candidate)
                    if rows:
                        expiry = candidate
                        days_left = trading_days_left(now, expiry)
        except Exception as e:  # network / API errors: report and keep looping if asked
            print(f"Could not fetch the option chain: {e}")
            if not a.loop:
                sys.exit(1)
            time.sleep(a.loop * 60)
            continue
        if not rows:
            sys.exit(f"Groww returned no strikes for {expiry}. Check the expiry date (holidays move it).")
        results = analyse(spot, rows, days_left, a)
        print_table(spot, expiry, days_left, results, a)
        save_json(spot, expiry, days_left, rows, a)
        if not a.loop:
            break
        time.sleep(a.loop * 60)


if __name__ == "__main__":
    main()
