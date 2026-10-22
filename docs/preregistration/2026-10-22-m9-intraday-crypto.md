# Pre-registration: the intraday library on hourly BTC and ETH (M9)

- Written: 2026-10-22, before either intraday rule has been run on real data
- Author: discoenmarte
- Status: registered in the commit that adds this file; results to be appended below

## What is being tested

Can a rule that enters and leaves the market within one UTC day beat holding the coin, after paying a round trip
on every day it trades? Four candidates, each judged by the unchanged ten-stage gauntlet:

| Question | Rule | Published source |
|---|---|---|
| `intraday_momentum:BTCUSDT:1h` | hold the day's last hour when its first hour went up | Gao, Han, Li & Zhou (2018), *Market intraday momentum*, JFE; on Bitcoin, Wen, Bouri, Xu & Zhao (2022), NAJEF |
| `intraday_momentum:ETHUSDT:1h` | the same on Ether | as above |
| `range_breakout:BTCUSDT:1h` | buy a close above the high of the day's first four hours, hold to 00:00 UTC | the opening-range breakout (Crabel, 1990; Zarattini & Aziz, 2023 on US stocks) |
| `range_breakout:ETHUSDT:1h` | the same on Ether | as above |

**The candidates** (the rules' published defaults, as registered in `qlab.backtest.strategies`):

- `intraday_momentum`: window 1 hour, hold 1 hour, no selector
- `range_breakout`: window 4 hours, no selector

**Grids:**

- `intraday_momentum`: window 1-12, hold 1-4, selector off or on (96 settings)
- `range_breakout`: window 1-12, selector off or on (24 settings)

The selector is the roadmap's "morning computes, it doesn't search": trade only after a day more volatile than the
previous 60 days' median. It is a parameter of each rule, so its use is part of the grid and counted in N.

## Data, timing and costs (fixed before any run)

- **Data:** Binance public archive, spot hourly klines of BTCUSDT and ETHUSDT, checksummed. They start
  2017-08-17. The research period ends 2024-09-30 (7.1 years); the lockbox (2024-10-01 on) stays sealed.
- **The day:** UTC; the position is decided at an hour's close and held from the next hour's open. Every rule is
  flat over 00:00 UTC.
- **Costs:** the `crypto` preset, 14 bps a side (10 bps taker fee, 1 bp half-spread, 3 bps slippage). That is a
  28 bps round trip on every day a rule trades.
- **Statistics:** daily returns (hours compounded per UTC day), 365 days a year. The benchmark is holding the coin.

## Calibration first

These are the first rules on hourly bars. The gauntlet's thresholds were calibrated on daily markets, so the rules
are calibrated before the real runs:

- `qlab calibrate --hourly-reps 100 --hourly-power-reps 20 --seed 600000`, on synthetic hourly markets. Each has
  GARCH volatility, Student-t shocks and an intraday volatility profile.
- **Noise:** fixed and grid-searching researchers, 100 runs each per rule.
- **Power:** planted intraday momentum at true edges of 0.5 and 1.0.

The rule written now: **the real runs go ahead only if noise passes at most 5% of the time for both rules and both
researcher types.** If it doesn't, the thresholds are not changed to fit; the finding is reported, and the real
runs wait for a calibrated fix.

## What the evidence leads me to expect

- **Costs decide it.** BTC's last hour moves about 0.5% (one standard deviation). A conditional edge has to pay
  28 bps every traded day, and published intraday crypto momentum is a few basis points per day, measured before
  costs.
- **The breakout** trades on more than half of all days and holds for hours. It earns mostly the coin's drift
  while invested, which the edge measure removes.
- **Expectation: all four rejected,** most likely at the costs stage (net CAGR below zero) or at significance.

## Method of judgement

- `qlab gauntlet BTCUSDT --source binance --interval 1h --strategy intraday_momentum`, the same on ETHUSDT, then
  `--strategy range_breakout` on both. Seed 7, run once each, in that order, after the calibration report exists.
- No parameter, window or instrument is changed after seeing a result.
- If any candidate passes stages 1-9, the lockbox decision is written down before it is opened.
