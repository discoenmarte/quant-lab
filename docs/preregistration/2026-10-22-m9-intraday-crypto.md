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

## Results (appended 2026-10-22, after the runs)

**Calibration** (thresholds v5, report `343740d3`, seeds 600,001+, 440 runs, 6 minutes on 14 processes):

- Noise passed 0 of 400 runs (95% CI 0-1.0%), for both rules and both researcher types. The condition for the real
  runs was met.
- Power on one-hour planted momentum was 0 of 40. Every run failed the plateau stage, because an effect that exists
  at one setting looks like overfitting.
- Planting it over the first and last four hours and measuring at that setting (reports `97659722` and `d291f58f`)
  still gave 0% at true edges of 0.5, 1, 2 and 3, again all at plateau.
- The reason is the cost cliff. A 28 bps round trip takes most of the gross gain, so a neighbouring setting that keeps
  three quarters of the gross keeps well under half the net. The plateau stage asks neighbours for half the
  candidate's edge.
- **With these costs the gauntlet can't confirm an intraday edge, however strong.** The thresholds were not changed.

**Verdicts** (seed 7, research data 2017-08-17 to 2024-09-30, lockboxes sealed):

| Card | Question | Edge after costs (before) | Costs break even at | Permutation p | Verdict |
|---|---|---|---|---|---|
| `1d96192f4757` | `intraday_momentum:BTCUSDT:1h` | −5.45 (−0.61) | −2 bps | 0.99 | rejected at costs |
| `73f9b8cc72a8` | `intraday_momentum:ETHUSDT:1h` | −3.91 (−0.15) | −1 bps | 0.86 | rejected at costs |
| `20be29a793c1` | `range_breakout:BTCUSDT:1h` | −1.01 (+0.81) | 6 bps | 0.076 | rejected at costs |
| `a87d2f7e3244` | `range_breakout:ETHUSDT:1h` | −0.20 (+1.24) | 12 bps | 0.004 | rejected at costs |

As expected, all four were rejected.

- **Intraday momentum** has no edge on crypto even before costs in this sample. Both coins went the wrong way.
- **The ETH breakout** has a gross signal that noise rarely matches (p = 0.004), but costs of 14 bps a side exceed
  its 12 bps break-even. It also fails walk-forward (−0.73 out of sample) and plateau (0 of 5 neighbours).
- Lower costs (maker orders, fee discounts) could be tested only as a new, pre-registered question, with N counted.
- The lockboxes of the four questions are still sealed.
