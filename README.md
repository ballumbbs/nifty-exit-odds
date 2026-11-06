// ============================================================================
//  NIFTY OPTION BUYER CONFLUENCE  (NOBC)  —  TradingView Pine Script v5
//  Built for: buying Nifty 50 CE / PE intraday (option buyer, no selling)
// ----------------------------------------------------------------------------
//  HOW IT WORKS
//  A signal fires only when ALL 6 filters agree (confluence). Fewer trades,
//  but each one is with the trend, with momentum, and NOT in a sideways
//  market (sideways = theta eats option buyers alive).
//
//    1. EMA 9 above/below EMA 21              -> short-term trend
//    2. Supertrend (10, 3) up/down            -> trend confirmation
//    3. RSI(14) > 55  /  < 45                 -> momentum
//    4. ADX(14) > 20 and DI+ > DI- (or <)     -> market is trending, not choppy
//    5. Price above/below VWAP                -> intraday institutional bias
//    6. 15-min close above/below 15-min EMA50 -> higher-timeframe trend
//
//  BUY CE  = all 6 bullish (fires on the first bar they all line up)
//  BUY PE  = all 6 bearish
//
//  RISK RULES (built in)
//    - Entries only 09:30–14:30 IST (skips opening noise and late theta decay)
//    - Stop-loss = 1 x ATR on spot, Target = 2 x risk (1:2 reward:risk)
//    - Exit early if Supertrend flips against you
//    - Hard square-off at 15:10 IST
//    - Max 2 trades per day (stops revenge trading)
//
//  SETUP
//    1. TradingView -> Pine Editor -> paste this -> "Add to chart"
//    2. Chart: NSE:NIFTY (spot) on 5-minute timeframe
//    3. Open "Strategy Tester" tab to see win rate, profit factor, drawdown
//    4. Alerts: Create alert -> Condition: NOBC -> "alert() function calls only"
//
//  IMPORTANT ABOUT THE BACKTEST
//    The backtest trades the Nifty SPOT with qty = 65 (1 lot). That is like a
//    deep ITM option (delta ~1). A real ATM option moves only ~0.5x the spot
//    AND loses time value (theta) every minute, so real option P&L will be
//    WORSE than the tester shows. Treat the tester as "was the direction
//    right?" — then paper-trade for 1 month before using real money.
// ============================================================================

//@version=5
strategy("Nifty Option Buyer Confluence", shorttitle="NOBC", overlay=true, initial_capital=100000, default_qty_type=strategy.fixed, default_qty_value=65, commission_type=strategy.commission.cash_per_order, commission_value=40, max_labels_count=500)

// ---------------------------- INPUTS ----------------------------------------
grpT = "Trend filters"
emaFastLen = input.int(9, "Fast EMA", minval=1, group=grpT)
emaSlowLen = input.int(21, "Slow EMA", minval=1, group=grpT)
stLen      = input.int(10, "Supertrend ATR length", minval=1, group=grpT)
stMult     = input.float(3.0, "Supertrend multiplier", step=0.1, group=grpT)
useHtf     = input.bool(true, "Use higher-timeframe filter", group=grpT)
htfTf      = input.timeframe("15", "Higher timeframe", group=grpT)
htfEmaLen  = input.int(50, "Higher-TF EMA length", minval=1, group=grpT)

grpM = "Momentum / strength filters"
rsiLen     = input.int(14, "RSI length", minval=1, group=grpM)
rsiBull    = input.float(55, "RSI bullish above", group=grpM)
rsiBear    = input.float(45, "RSI bearish below", group=grpM)
useAdx     = input.bool(true, "Use ADX (no-chop) filter", group=grpM)
adxLen     = input.int(14, "ADX length", minval=1, group=grpM)
adxMin     = input.float(20, "ADX minimum", group=grpM)
useVwap    = input.bool(true, "Use VWAP filter", group=grpM)

grpR = "Risk management"
atrLen     = input.int(14, "ATR length", minval=1, group=grpR)
slMult     = input.float(1.0, "Stop-loss = ATR x", step=0.1, group=grpR)
rr         = input.float(2.0, "Target = risk x (reward:risk)", step=0.1, group=grpR)
useStExit  = input.bool(true, "Exit if Supertrend flips", group=grpR)
maxTrades  = input.int(2, "Max trades per day", minval=1, group=grpR)
entrySess  = input.session("0930-1430", "Entry window (IST)", group=grpR)
exitSess   = input.session("1510-1530", "Square-off window (IST)", group=grpR)
strikeStep = input.int(50, "Strike interval", group=grpR)

showDash   = input.bool(true, "Show dashboard", group="Display")

// ---------------------------- CALCULATIONS ----------------------------------
emaF = ta.ema(close, emaFastLen)
emaS = ta.ema(close, emaSlowLen)
[stLine, stDir] = ta.supertrend(stMult, stLen)   // stDir < 0 = uptrend
rsi = ta.rsi(close, rsiLen)
[diP, diM, adx] = ta.dmi(adxLen, adxLen)
vw  = ta.vwap(hlc3)
atr = ta.atr(atrLen)

// Higher timeframe values from the LAST CLOSED 15m bar (no repainting)
htfEma   = request.security(syminfo.tickerid, htfTf, ta.ema(close, htfEmaLen)[1], lookahead=barmerge.lookahead_on)
htfClose = request.security(syminfo.tickerid, htfTf, close[1], lookahead=barmerge.lookahead_on)

// Individual conditions
b1 = emaF > emaS
s1 = emaF < emaS
b2 = stDir < 0
s2 = stDir > 0
b3 = rsi > rsiBull
s3 = rsi < rsiBear
b4 = useAdx ? (adx > adxMin and diP > diM) : true
s4 = useAdx ? (adx > adxMin and diM > diP) : true
b5 = useVwap ? close > vw : true
s5 = useVwap ? close < vw : true
b6 = useHtf ? htfClose > htfEma : true
s6 = useHtf ? htfClose < htfEma : true

bullScore = (b1 ? 1 : 0) + (b2 ? 1 : 0) + (b3 ? 1 : 0) + (b4 ? 1 : 0) + (b5 ? 1 : 0) + (b6 ? 1 : 0)
bearScore = (s1 ? 1 : 0) + (s2 ? 1 : 0) + (s3 ? 1 : 0) + (s4 ? 1 : 0) + (s5 ? 1 : 0) + (s6 ? 1 : 0)

bullAll = b1 and b2 and b3 and b4 and b5 and b6
bearAll = s1 and s2 and s3 and s4 and s5 and s6

// Session / day handling (Asia/Kolkata)
inEntry = not na(time(timeframe.period, entrySess, "Asia/Kolkata"))
inExit  = not na(time(timeframe.period, exitSess, "Asia/Kolkata"))

var int tradesToday = 0
if timeframe.change("D")
    tradesToday := 0

flat     = strategy.position_size == 0
canTrade = inEntry and flat and tradesToday < maxTrades

longSig  = canTrade and bullAll and not bullAll[1]
shortSig = canTrade and bearAll and not bearAll[1]

atmStrike = math.round(close / strikeStep) * strikeStep

// ---------------------------- ORDERS ----------------------------------------
var float slLvl = na
var float tpLvl = na

if longSig
    risk = atr * slMult
    slLvl := close - risk
    tpLvl := close + risk * rr
    strategy.entry("CE", strategy.long)
    strategy.exit("CE-x", "CE", stop=slLvl, limit=tpLvl)
    tradesToday += 1
    alert("NIFTY BUY " + str.tostring(atmStrike) + " CE | Spot " + str.tostring(close, "#.##") + " | Spot SL " + str.tostring(slLvl, "#.##") + " | Spot Target " + str.tostring(tpLvl, "#.##"), alert.freq_once_per_bar_close)

if shortSig
    risk = atr * slMult
    slLvl := close + risk
    tpLvl := close - risk * rr
    strategy.entry("PE", strategy.short)
    strategy.exit("PE-x", "PE", stop=slLvl, limit=tpLvl)
    tradesToday += 1
    alert("NIFTY BUY " + str.tostring(atmStrike) + " PE | Spot " + str.tostring(close, "#.##") + " | Spot SL " + str.tostring(slLvl, "#.##") + " | Spot Target " + str.tostring(tpLvl, "#.##"), alert.freq_once_per_bar_close)

// Early exit on Supertrend flip
if useStExit and strategy.position_size > 0 and stDir > 0
    strategy.close("CE", comment="ST flip")
    alert("NIFTY EXIT CE — Supertrend flipped down", alert.freq_once_per_bar_close)
if useStExit and strategy.position_size < 0 and stDir < 0
    strategy.close("PE", comment="ST flip")
    alert("NIFTY EXIT PE — Supertrend flipped up", alert.freq_once_per_bar_close)

// Intraday square-off
if inExit and not flat
    strategy.close_all(comment="15:10 square-off")
    alert("NIFTY SQUARE-OFF — close all option positions", alert.freq_once_per_bar_close)

// ---------------------------- PLOTS -----------------------------------------
plot(emaF, "EMA fast", color=color.new(color.aqua, 0), linewidth=1)
plot(emaS, "EMA slow", color=color.new(color.orange, 0), linewidth=1)
plot(useVwap ? vw : na, "VWAP", color=color.new(color.purple, 0), linewidth=2)
plot(stLine, "Supertrend", color=stDir < 0 ? color.new(color.green, 0) : color.new(color.red, 0), linewidth=2, style=plot.style_linebr)

plot(strategy.position_size != 0 ? slLvl : na, "Stop-loss", color=color.red, style=plot.style_linebr, linewidth=1)
plot(strategy.position_size != 0 ? tpLvl : na, "Target", color=color.lime, style=plot.style_linebr, linewidth=1)

plotshape(longSig, "BUY CE", shape.labelup, location.belowbar, color.new(color.green, 0), text="BUY CE", textcolor=color.white, size=size.small)
plotshape(shortSig, "BUY PE", shape.labeldown, location.abovebar, color.new(color.red, 0), text="BUY PE", textcolor=color.white, size=size.small)

// Shade background when market is choppy (ADX low) — option buyers should stay out
bgcolor(useAdx and adx < adxMin ? color.new(color.gray, 90) : na, title="Choppy zone")

// ---------------------------- DASHBOARD -------------------------------------
var table dash = table.new(position.top_right, 3, 10, bgcolor=color.new(color.black, 15), border_width=1, border_color=color.new(color.gray, 60))

f_row(r, name, val, bullOk, bearOk) =>
    mark = bullOk and not bearOk ? "▲" : bearOk and not bullOk ? "▼" : "–"
    bg = bullOk and not bearOk ? color.new(color.green, 30) : bearOk and not bullOk ? color.new(color.red, 30) : color.new(color.gray, 50)
    table.cell(dash, 0, r, name, text_color=color.white, text_size=size.small, text_halign=text.align_left)
    table.cell(dash, 1, r, val, text_color=color.white, text_size=size.small)
    table.cell(dash, 2, r, mark, text_color=color.white, bgcolor=bg, text_size=size.small)

if showDash and barstate.islast
    table.cell(dash, 0, 0, "NOBC — Nifty", text_color=color.yellow, text_size=size.small)
    table.cell(dash, 1, 0, "Value", text_color=color.yellow, text_size=size.small)
    table.cell(dash, 2, 0, "Bias", text_color=color.yellow, text_size=size.small)
    f_row(1, "EMA 9/21", b1 ? "9 > 21" : "9 < 21", b1, s1)
    f_row(2, "Supertrend", stDir < 0 ? "Up" : "Down", b2, s2)
    f_row(3, "RSI", str.tostring(rsi, "#.0"), b3, s3)
    f_row(4, "ADX", str.tostring(adx, "#.0") + (adx < adxMin ? " chop" : ""), useAdx ? (adx > adxMin and diP > diM) : false, useAdx ? (adx > adxMin and diM > diP) : false)
    f_row(5, "VWAP", close > vw ? "Above" : "Below", close > vw, close < vw)
    f_row(6, "15m trend", htfClose > htfEma ? "Up" : "Down", htfClose > htfEma, htfClose < htfEma)
    table.cell(dash, 0, 7, "Score", text_color=color.white, text_size=size.small, text_halign=text.align_left)
    table.cell(dash, 1, 7, "CE " + str.tostring(bullScore) + "/6  PE " + str.tostring(bearScore) + "/6", text_color=color.white, text_size=size.small)
    sigTxt = bullAll ? "CE READY" : bearAll ? "PE READY" : "WAIT"
    sigBg = bullAll ? color.new(color.green, 0) : bearAll ? color.new(color.red, 0) : color.new(color.gray, 40)
    table.cell(dash, 2, 7, sigTxt, text_color=color.white, bgcolor=sigBg, text_size=size.small)
    table.cell(dash, 0, 8, "ATM strike", text_color=color.white, text_size=size.small, text_halign=text.align_left)
    table.cell(dash, 1, 8, str.tostring(atmStrike), text_color=color.white, text_size=size.small)
    table.cell(dash, 2, 8, "", text_color=color.white, text_size=size.small)
    table.cell(dash, 0, 9, "Trades today", text_color=color.white, text_size=size.small, text_halign=text.align_left)
    table.cell(dash, 1, 9, str.tostring(tradesToday) + " / " + str.tostring(maxTrades), text_color=color.white, text_size=size.small)
    table.cell(dash, 2, 9, "", text_color=color.white, text_size=size.small)
