#!/usr/bin/env python3
"""
ORB paper trader - Nifty opening-range breakout traded as a debit spread (PAPER ONLY)

Runs every 5 minutes inside the GitHub workflow. Each run:
  1. Reads today's 15-minute Nifty candles and the live option chain from Groww.
  2. Applies the ORB rules (same as tradingview/nifty_orb_spread.pine):
       - Opening range = 09:15-09:30 IST candle
       - Breakout window 09:30-11:30: a 15-min candle CLOSES outside the range,
         on the right side of VWAP, with ADX(14) above 20. Max 1 trade a day.
       - Skips expiry days: Tuesdays, and the earlier day when a holiday moves expiry.
       - Bull: buy ATM CE + sell CE `width` points higher. Bear: same with PEs.
       - Exits: candle closes back inside the range, spot reaches the other side
         of the range (stop), spot moves `target` points (target), or 15:00 IST.
  3. Records the REAL option prices at entry and exit and the resulting P&L,
     in docs/data/orb_paper.json, which docs/paper.html displays.

It never places, modifies or cancels an order. There is no order code in it.
"""
import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nifty_exit_odds import IST, fetch_chain, groww_client, next_tuesday  # noqa: E402

OR_START = dt.time(9, 15)
WIN_START = dt.time(9, 30)
WIN_END = dt.time(11, 30)
LATEST_ENTRY = dt.time(12, 0)      # never record an entry later than this, even if a run was missed
TIME_EXIT = dt.time(15, 0)
CANDLE = dt.timedelta(minutes=15)


# ---------------------------------------------------------------- indicators
def rma(values, n):
    """Wilder's moving average (what TradingView's ta.rma / ta.dmi use)."""
    out, avg = [], None
    for i, v in enumerate(values):
        if i < n - 1:
            out.append(None)
            continue
        if avg is None:
            avg = sum(values[i - n + 1:i + 1]) / n
        else:
            avg = (avg * (n - 1) + v) / n
        out.append(avg)
    return out


def adx_series(c, n=14):
    """ADX for each candle (None until enough history)."""
    tr, pdm, mdm = [0.0], [0.0], [0.0]
    for i in range(1, len(c)):
        h, l, pc = c[i]["h"], c[i]["l"], c[i - 1]["c"]
        up, dn = h - c[i - 1]["h"], c[i - 1]["l"] - l
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
    atr, ps, ms = rma(tr, n), rma(pdm, n), rma(mdm, n)
    dx = []
    for a, p, m in zip(atr, ps, ms):
        if a is None or a == 0:
            dx.append(None)
            continue
        pdi, mdi = 100 * p / a, 100 * m / a
        dx.append(0.0 if pdi + mdi == 0 else 100 * abs(pdi - mdi) / (pdi + mdi))
    first = next((i for i, v in enumerate(dx) if v is not None), None)
    out = [None] * len(c)
    if first is None:
        return out
    sm = rma(dx[first:], n)
    for i, v in enumerate(sm):
        out[first + i] = v
    return out


def vwap_series(day):
    """Cumulative intraday VWAP. Index candles often have no volume: then equal weights."""
    use_vol = any(x["v"] for x in day)
    num = den = 0.0
    out = []
    for x in day:
        w = x["v"] if use_vol else 1.0
        num += (x["h"] + x["l"] + x["c"]) / 3 * w
        den += w
        out.append(num / den if den else None)
    return out


# ---------------------------------------------------------------- data
def parse_ts(v):
    if isinstance(v, (int, float)):
        return dt.datetime.fromtimestamp(v, IST)
    s = str(v).replace("T", " ")[:19]
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)


def fetch_candles(groww, now):
    start = (now - dt.timedelta(days=7)).strftime("%Y-%m-%d 09:00:00")
    end = now.strftime("%Y-%m-%d %H:%M:%S")
    raw = groww.get_historical_candles(
        exchange=groww.EXCHANGE_NSE, segment=groww.SEGMENT_CASH, groww_symbol="NSE-NIFTY",
        start_time=start, end_time=end, candle_interval=groww.CANDLE_INTERVAL_MIN_15)
    out = []
    for row in raw.get("candles", []):
        t = parse_ts(row[0])
        if t + CANDLE > now:            # still forming: only closed candles count
            continue
        out.append({"t": t, "o": float(row[1]), "h": float(row[2]), "l": float(row[3]),
                    "c": float(row[4]), "v": float(row[5] or 0)})
    return sorted(out, key=lambda x: x["t"])


def chain_with_fallback(groww, now):
    expiry = next_tuesday(now)
    for shift in range(3):              # a holiday moves the weekly expiry earlier
        cand = expiry - dt.timedelta(days=shift)
        if cand < now.date():
            break
        spot, rows = fetch_chain(groww, cand)
        if rows:
            return spot, rows, cand
    raise RuntimeError(f"no option chain for expiry {expiry}")


def ltp(rows, strike, side):
    for r in rows:
        if abs(r["k"] - strike) < 0.01:
            return r.get(side)
    return None


# ---------------------------------------------------------------- state
def load_state(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"trades": []}


def save_state(path, st):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)


def today_trade(st, day):
    for t in st["trades"]:
        if t["date"] == day:
            return t
    return None


# ---------------------------------------------------------------- engine
def run(a):
    now = dt.datetime.now(IST)
    st = load_state(a.state)
    day = now.date().isoformat()
    st["settings"] = {"target_pts": a.target, "width": a.width, "adx_min": a.adx_min,
                      "lot": a.lot, "charges": a.charges}

    def finish(status):
        st["status"] = status
        st["updated"] = now.isoformat(timespec="seconds")
        save_state(a.state, st)
        print(status)

    if now.weekday() >= 5:
        return finish("Weekend - market closed.")
    if now.weekday() == 1:
        return finish("Tuesday (weekly expiry) - skipped by the rules.")
    if now.time() < WIN_START:
        return finish("Building the opening range (09:15-09:30).")

    groww = groww_client()
    candles = fetch_candles(groww, now)
    spot, rows, expiry = chain_with_fallback(groww, now)
    if expiry == now.date():
        return finish("Expiry day (moved by a holiday) - skipped by the rules.")
    adx = adx_series(candles)
    for c, v in zip(candles, adx):
        c["adx"] = v
    today = [c for c in candles if c["t"].date() == now.date()]
    vw = vwap_series(today)
    for c, v in zip(today, vw):
        c["vwap"] = v

    orc = next((c for c in today if c["t"].time() == OR_START), None)
    live = {"spot": spot, "expiry": expiry.isoformat()}
    if orc is None:
        st["live"] = live
        return finish("Waiting for the 09:15 candle from Groww.")
    orh, orl = orc["h"], orc["l"]
    last = today[-1]
    live.update({"or_high": orh, "or_low": orl, "vwap": round(last["vwap"], 2),
                 "adx": None if last["adx"] is None else round(last["adx"], 1)})
    st["live"] = live

    tr = today_trade(st, day)

    # ---- look for the entry signal
    if tr is None:
        sig = None
        for c in today:
            ct = c["t"].time()
            if not (WIN_START <= ct < WIN_END):
                continue
            if c["adx"] is None or c["adx"] <= a.adx_min:
                continue
            if c["c"] > orh and c["c"] > c["vwap"]:
                sig = ("BULL", c)
                break
            if c["c"] < orl and c["c"] < c["vwap"]:
                sig = ("BEAR", c)
                break
        if sig is None:
            if now.time() >= WIN_END:
                return finish("No breakout by 11:30 - no trade today.")
            return finish("Watching for a breakout (09:30-11:30).")
        if now.time() > LATEST_ENTRY:
            return finish("A breakout happened but no run caught it in time - no trade recorded.")
        side, c = sig
        atm = round(spot / 50) * 50
        opt = "ce" if side == "BULL" else "pe"
        sell_k = atm + a.width if side == "BULL" else atm - a.width
        buy_px, sell_px = ltp(rows, atm, opt), ltp(rows, sell_k, opt)
        if not buy_px or not sell_px:
            return finish(f"{side} signal, but Groww gave no price for {atm}/{sell_k} - will retry.")
        tr = {
            "date": day, "side": side, "expiry": expiry.isoformat(),
            "signal_candle": c["t"].strftime("%H:%M"),
            "entry_time": now.strftime("%H:%M"), "entry_spot": round(spot, 2),
            "or_high": orh, "or_low": orl,
            "buy": f"{atm} {opt.upper()}", "sell": f"{sell_k} {opt.upper()}",
            "buy_strike": atm, "sell_strike": sell_k, "opt": opt,
            "buy_entry": buy_px, "sell_entry": sell_px, "cost": round(buy_px - sell_px, 2),
            "target_spot": round(spot + a.target if side == "BULL" else spot - a.target, 2),
            "status": "OPEN",
        }
        st["trades"].append(tr)
        return finish(f"{side} spread opened at {tr['entry_time']}: buy {tr['buy']} @ {buy_px}, "
                      f"sell {tr['sell']} @ {sell_px}, cost {tr['cost']}/unit.")

    # ---- manage the open trade
    if tr["status"] != "OPEN":
        return finish(f"Today's trade is closed ({tr['exit_reason']}): "
                      f"Rs {tr['pnl_lot']:+,.0f} for 1 lot.")

    bull = tr["side"] == "BULL"
    entry_t = dt.datetime.strptime(f"{day} {tr['entry_time']}", "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    after = [c for c in today if c["t"] + CANDLE > entry_t]
    reason = None
    if bull:
        if spot <= tr["or_low"] or any(c["l"] <= tr["or_low"] for c in after):
            reason = "stop (other side of range)"
        elif spot >= tr["target_spot"] or any(c["h"] >= tr["target_spot"] for c in after):
            reason = "target"
        elif any(c["c"] < tr["or_high"] for c in after):
            reason = "back inside range"
    else:
        if spot >= tr["or_high"] or any(c["h"] >= tr["or_high"] for c in after):
            reason = "stop (other side of range)"
        elif spot <= tr["target_spot"] or any(c["l"] <= tr["target_spot"] for c in after):
            reason = "target"
        elif any(c["c"] > tr["or_low"] for c in after):
            reason = "back inside range"
    if reason is None and now.time() >= TIME_EXIT:
        reason = "15:00 time exit"

    buy_now, sell_now = ltp(rows, tr["buy_strike"], tr["opt"]), ltp(rows, tr["sell_strike"], tr["opt"])
    if buy_now and sell_now:
        tr["value_now"] = round(buy_now - sell_now, 2)
        tr["pnl_now"] = round((tr["value_now"] - tr["cost"]) * a.lot - a.charges, 0)
    if reason is None:
        return finish(f"{tr['side']} spread open since {tr['entry_time']}. "
                      f"Now {tr.get('value_now', '?')} vs cost {tr['cost']} per unit.")
    if not (buy_now and sell_now):
        return finish(f"Exit ({reason}) due, but Groww gave no price - will retry.")
    tr.update({
        "status": "CLOSED", "exit_reason": reason, "exit_time": now.strftime("%H:%M"),
        "exit_spot": round(spot, 2), "buy_exit": buy_now, "sell_exit": sell_now,
        "value_exit": round(buy_now - sell_now, 2),
    })
    tr["pnl_unit"] = round(tr["value_exit"] - tr["cost"], 2)
    tr["pnl_lot"] = round(tr["pnl_unit"] * a.lot - a.charges, 0)
    return finish(f"Closed ({reason}) at {tr['exit_time']}: Rs {tr['pnl_lot']:+,.0f} for 1 lot.")


def main():
    p = argparse.ArgumentParser(description="ORB paper trader (no orders).")
    p.add_argument("--state", default="docs/data/orb_paper.json")
    p.add_argument("--target", type=float, default=100, help="target in Nifty points (default 100)")
    p.add_argument("--width", type=int, default=150, help="spread width in points (default 150)")
    p.add_argument("--adx-min", type=float, default=20, dest="adx_min")
    p.add_argument("--lot", type=int, default=65)
    p.add_argument("--charges", type=float, default=100, help="charges per spread round trip, Rs (default 100)")
    a = p.parse_args()
    try:
        run(a)
    except Exception as e:  # keep the page alive and show what went wrong
        st = load_state(a.state)
        st["status"] = f"Error this run: {e}"
        st["updated"] = dt.datetime.now(IST).isoformat(timespec="seconds")
        save_state(a.state, st)
        print(st["status"])
        sys.exit(1)


if __name__ == "__main__":
    main()
