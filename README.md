# Nifty Exit Odds

A web page that shows, for every Nifty call and put near the current price, how often the premium reaches your profit target before your stop-loss within your holding time, and what that plan averages per trade after charges.

A GitHub Actions workflow reads the live Nifty option chain from your Groww account every 5 minutes during market hours (Monday to Friday, 09:15–15:30 IST) and publishes it to GitHub Pages. You just open the page.

**Read-only.** The scanner only reads prices. It has no code that can place, change or cancel an order.

**Not a signal service.** The odds come from the volatility already priced into each option. With no directional view, they are the market's own odds, not a forecast, and the average result is usually slightly negative after charges.

## Setup (about 10 minutes)

### 1. Turn on GitHub Pages
Repository **Settings → Pages → Build and deployment → Source: GitHub Actions**.

### 2. Run it once with demo prices
**Actions → Scan Nifty chain → Run workflow.** When it finishes, your page is live at
`https://ballumbbs.github.io/nifty-exit-odds/` showing made-up demo prices.

### 3. Connect Groww
1. Subscribe to the Groww Trading API (live prices and the option chain need a paid plan): https://groww.in/user/profile/trading-apis
2. On https://groww.in/trade-api/api-keys open the dropdown next to **Generate API key** and choose **Generate TOTP token**. Copy the TOTP token and the TOTP secret.
3. In this repository go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `GROWW_TOTP_TOKEN` = the TOTP token
   - `GROWW_TOTP_SECRET` = the TOTP secret
4. Run the workflow again. The page now says **Groww** instead of **Demo prices**.

Never put these values in a file in the repository. Secrets stay encrypted and are not shown in logs.

### Optional settings
**Settings → Secrets and variables → Actions → Variables:**
- `LOT_SIZE` - Nifty lot size if it changes (default 65)
- `NIFTY_EXPIRY` - a fixed expiry date `YYYY-MM-DD`. Leave it unset to use the next Tuesday weekly expiry (the scanner also tries a day or two earlier when a holiday moves it).

## Using the page
- The chain table runs your plan (target %, stop %, hold time, charges) on every strike. Change the plan on the left and the table recalculates.
- Tap any strike to see its full breakdown and a table of other targets.
- "Your expected Nifty move" lets you test how strong a directional view your plan needs before it pays off.

## Running the scanner on your own computer
```
pip install growwapi pyotp numpy
python scanner/nifty_exit_odds.py --target 30 --stop 20 --hold 60
python scanner/nifty_exit_odds.py --demo          # no Groww needed
```
Put your keys in `scanner/groww_keys.txt` (ignored by git):
```
GROWW_TOTP_TOKEN=...
GROWW_TOTP_SECRET=...
```

## Things to know
- GitHub sometimes starts scheduled runs a few minutes late, so the page can lag the market. Check the time shown on the page before using a number.
- If the workflow fails at the Groww sign-in step, Groww may be limiting API access by IP address. GitHub's runners change IP addresses, so in that case run the scanner on your own computer instead.
- Anyone with write access to this repository could use the workflow to read your Groww secrets. Don't add collaborators you don't fully trust.
- The model ignores weekends, holidays and price gaps, so real stop-losses can fill worse than shown.

## TradingView signal script (NOBC)
`tradingview/nifty_option_buyer_confluence.pine` is a separate TradingView Pine Script for buying Nifty CE/PE intraday. It is an indicator, backtester and alert script in one.

- **BUY CE / BUY PE** only when all 6 filters agree: EMA 9/21, Supertrend (10,3), RSI 55/45, ADX > 20 (no chop), VWAP side, and the 15-minute EMA50 trend.
- Built-in risk rules: entries 09:30–14:30 IST, stop-loss 1× ATR, target 2× risk, early exit on Supertrend flip, square-off at 15:10, max 2 trades a day.
- Dashboard shows each filter, a score out of 6 and the ATM strike.

**Use it:** TradingView → Pine Editor → paste the file → Add to chart → `NSE:NIFTY`, 5-minute chart. See results in **Strategy Tester**. For alerts choose *"alert() function calls only"*.

**Backtest caveat:** the tester trades Nifty spot (qty 65 = 1 lot). A real ATM option moves about half as much and loses time value, so real option results will be worse. Tune on one period, check on another, then paper-trade before using real money. Like the rest of this repo, it never places orders.
