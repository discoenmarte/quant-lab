# Pre-registration: aggregate insider buying as a market-timing signal (M5b, Pipeline C1)

- Written: 2026-10-22, before the insider data was downloaded and before any run of the rule on real data
- Author: discoenmarte
- Status: registered in the commit that adds this file; results to be appended below

## What is being tested

When corporate insiders across the US market buy their own companies' shares more than usual, does the index do
better over the following months, by enough to time it after costs?

Two candidates, each judged by the unchanged ten-stage gauntlet (the thresholds in force when they run):

| Question | Instrument | Why |
|---|---|---|
| `insider_timing:SPY:1d` | SPY (large caps) | the market as a whole, as in Seyhun (1988) and Lakonishok & Lee (2001) |
| `insider_timing:IWM:1d` | IWM (small caps) | Lakonishok & Lee find the predictive power mostly in smaller firms |

The rule (`insider_timing`, registered in `qlab.backtest.strategies`, code in `qlab.informed.insiders`):

- **Reading:** the net purchase ratio, (purchases − sales) / (purchases + sales), counted in Form 4 filings over the
  last `window` calendar days, across every US issuer.
- **Standardised** against its own mean and spread over the previous `lookback` days, so it says whether insiders
  buy more than they usually do. (Insiders sell far more than they buy for reasons that have nothing to do with
  information: diversification, taxes, compensation. The level is not comparable across years.)
- **Position:** hold the index while the standardised reading is above `threshold`; cash (earning the T-bill rate)
  otherwise.
- **Defaults** (the candidate the card reports): window 60 days, lookback 730 days, threshold 0. The gauntlet's grid
  covers window 30-120 (step 30), lookback 365-1,095 (step 365) and threshold −1 to 1 (step 0.5): 60 settings, every
  one a ledger trial.

## Data and timing (fixed before any run)

- **Source:** the SEC's Insider Transactions Data Sets (Forms 3, 4 and 5 as filed on EDGAR), one file per quarter
  from 2006Q1.
- **What counts:** original Form 4 filings only (amendments are corrections and can arrive years later); non-derivative
  open-market purchases (transaction code `P`) and sales (`S`). Grants, exercises, gifts and tax withholding are not
  trades on information. A filing counts once per side, whatever its number of lines.
- **When it was known:** each filing is keyed by its EDGAR filing date and used only from the next day on, since
  filings accepted after the 16:00 close still carry that day's date. Decisions are taken at the close and filled
  at the next open, as for every rule in the lab.
- **Research period:** 2009-01-02 (the rule's declared start: three years of data to standardise against) to
  2024-09-30. 15.7 years. The lockbox (2024-10-01 on) stays sealed; opening it is a separate, recorded decision,
  only for a candidate that passes stages 1-9.
- **Costs:** the ETF preset (commission, half-spread and slippage per side), unchanged.

## What the published evidence leads me to expect

- Seyhun (1988, 1992) and Lakonishok & Lee (2001) found that aggregate insider buying predicted market returns over
  the next 6-12 months, more for small firms. Jiang & Zaman (2010) attribute much of it to insiders buying after
  prices fell (contrarian timing), not to private information about the economy.
- This sample (2009-2024) is entirely after those publications, and mostly a long bull market. A timing rule pays
  for every month in cash; the gauntlet judges the edge over holding the index, so leaving the market during
  rallies counts against it.
- **Expectation: rejection on both,** most likely at permutation or significance. IWM is the likelier of the two to
  show anything.

## Method of judgement

- `qlab gauntlet SPY --strategy insider_timing` and `qlab gauntlet IWM --strategy insider_timing`, seed 7,
  default parameters as the candidate, run once each, in that order.
- No parameter, window or instrument is changed after seeing a result. A run that fails for a technical reason is
  fixed and rerun, and the failure is reported here.
- If either candidate passes stages 1-9, the lockbox decision is written down before it is opened.
