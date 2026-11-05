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
  python
