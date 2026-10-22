# Pre-registration: attention and news tone as de-risk signals (M10, the radar)

- Written: 2026-10-22, before any of these rules or tests has been run on real data
- Author: discoenmarte
- Status: registered in the commit that adds this file; results to be appended below

## What is being tested

Two hypotheses with published support, each tested two ways: as a forecast of volatility (a regression) and as a
trading rule the unchanged ten-stage gauntlet judges.

**H1. Attention spikes come before higher volatility, so stepping out after them improves risk-adjusted returns.**
Searches and page views rise when investors pay attention; attention measures predict trading volume and
volatility far more reliably than returns (Da, Engelberg & Gao, 2011; Antweiler & Frank, 2004; Dimpfl & Jank,
2016, on search queries and Dow Jones volatility; Moat et al., 2013, on Wikipedia views before market moves; on
Bitcoin, Kristoufek, 2013, and Urquhart, 2018, who finds that attention also follows volatility).

**H2. A drop in the tone of market news comes before higher volatility.** Pessimistic news predicts price pressure,
its reversal, and high volume (Tetlock, 2007; Garcia, 2013).

| Question | Rule | Signal (as published, see below) |
|---|---|---|
| `attention_derisk:SPY:1d` | hold SPY; out for `hold` days after a spike | Wikipedia views of *S&P 500*, *Stock market crash*, *Recession* |
| `crypto_attention_derisk:BTCUSDT:1d` | hold BTC; out for `hold` days after a spike | Wikipedia views of *Bitcoin*, *Cryptocurrency* |
| `tone_derisk:SPY:1d` | hold SPY; out for `hold` days after a drop | GDELT average tone of English news matching `("stock market" OR "wall street")` |

**The rule** (`qlab.radar.research`): a day's attention score is the mean over the articles of each article's
robust z-score of log views against its previous `window` days (median and median absolute deviation); tone's score
is the robust z-score of the day's average tone. Each decision takes the most extreme score among the days published
since the previous decision. A score at or above `threshold` (attention) or at or below minus `threshold` (tone) is a
spike; the rule then holds cash for `hold` decisions, the spike's own included, and holds the asset otherwise.

**The candidates** are the defaults as registered: `window` 90, `threshold` 2.5, `hold` 5.

**The grid** (60 settings per rule): `window` 60, 90, 120; `threshold` 2.0, 2.5, 3.0, 3.5; `hold` 1, 3, 5, 7, 9.

**The forecasting tests** (`qlab radar har`): next-day log Parkinson volatility regressed on today's, the last
week's mean and the last month's mean (HAR, Corsi 2009) plus the signal's score at the decision (window 90), with
Newey-West errors (5 lags). H1 predicts a positive coefficient for SPY and for BTC, H2 a negative one for SPY. One
regression each, one-sided, p below 0.05 counts as support. Regressions are not backtests and add no trial; they are
fixed here all the same.

## Data, timing and costs (fixed before any run)

- **Prices:** the lab's own: Yahoo SPY (ETF costs, 3 bps a side, T-bill on cash), Binance BTCUSDT (crypto costs,
  14 bps a side, no cash yield).
- **Wikipedia:** daily views by people (agent `user`, all access) from the Wikimedia REST API, from 2015-07-01.
  The research period starts 2015-11-01 (`starts`), so the longest baseline exists.
- **GDELT:** the DOC 2.0 API's `timelinetone`, English sources, from 2017-01-01 (GDELT refuses earlier start dates).
  The research period starts 2017-06-01.
- **Research period ends 2024-09-30.** The lockbox (2024-10-01 on) stays sealed.
- **Point in time.** A position is decided at 20:00 UTC of each bar's day and held from the next bar's open. A signal
  day counts once published: Wikipedia history is stamped 24 hours after its day ends (on 2026-10-22 the previous
  day was already served at 11:18 UTC, so this is conservative), GDELT one hour after. So SPY sees views through two
  days back and tone through one.
- **Vintages.** Values are today's view of the past: GDELT recomputes old days from its current index, and Wikipedia's
  automated-traffic filter changed in 2020. This can't be undone with free data and is a known weakness.
- **Statistics:** daily returns, 252 days a year for SPY, 365 for BTC. The benchmark is holding the asset.

## Calibration first

These are the first rules whose input is a separate signal series rather than prices. They are calibrated on
synthetic daily markets with a signal column (`qlab.radar.research.synthetic_signal_bars`):

- `qlab calibrate --noise-reps 0 --power-reps 0 --portfolio-reps 0 --hourly-power-reps 0 --signal-reps 100
  --signal-power-reps 20 --seed 700000`
- **Noise:** the signal is unrelated to everything, so every rule has a true edge of exactly zero (or below, after
  costs). Fixed and grid-searching researchers, 100 runs each, for `attention_derisk` (8.9 years) and `tone_derisk`
  (7.3 years).
- **Power:** each spike is followed by five days of higher volatility, planted at true edges of 0.1 and 0.2.

The rule written now: **the real gauntlet runs go ahead only if noise passes at most 5% of the time for both
families and both researcher types.** If not, thresholds are not changed to fit; the finding is reported and the
runs wait.

## What the evidence leads me to expect

- **A volatility-only effect can't make much edge.** Stepping out of turbulent days keeps the asset's drift on calm
  days; in a market with a Sharpe of 0.375 the edge of such a rule tops out near 0.23 however strong the effect
  (measured on synthetic markets before writing this). Over 7-9 years the gauntlet needs an edge near 0.55 to reach
  significance, so **expect all three questions rejected**, most likely at significance or the Deflated Sharpe, even
  if H1 is true. Calibration power should be near zero, which says the test can't see this kind of effect, not that
  it is absent.
- **The HAR tests are the informative ones.** Expect a positive, significant attention coefficient for SPY and BTC
  (attention and volatility move together, and a two-day lag still leaves a lot of persistence), but small R²
  gains. Expect tone to be weaker: GDELT's tone moves with coverage and is noisy day to day.
- **Reverse causality.** Attention also follows volatility (Urquhart, 2018). HAR controls for past volatility, so a
  positive coefficient means attention adds to it, not that attention causes it.

## Method of judgement

1. Collect the data into the lab (`qlab radar collect --deep --only wiki`, then `--only gdelt` until the `markets`
   query is stored).
2. Calibration as above. Stop if the noise condition fails.
3. `qlab radar har`, once.
4. `qlab gauntlet SPY --source yahoo --strategy attention_derisk`, then `qlab gauntlet BTCUSDT --source binance
   --strategy crypto_attention_derisk`, then `qlab gauntlet SPY --source yahoo --strategy tone_derisk`. Seed 7, once
   each, in that order.
5. If GDELT's history can't be collected (it refuses most requests from this network), H2's runs wait and say so.
6. No parameter, article, query, window or instrument changes after seeing a result. If a candidate passes stages
   1-9, the lockbox decision is written down before it is opened.

Disclosure: before this file was written, the radar's collectors were tested on a scratch copy of the lab
(Wikipedia views, Hacker News, markets and stress files) and its anomaly scan was run there. No relation between
attention or tone and returns or volatility was looked at.

## Results

(to be appended after the runs)
