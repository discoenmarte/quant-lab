# 0004. Gauntlet design: edge returns, a fixed lockbox, honest N, and thresholds from calibration

- Date: 2026-10-18
- Status: accepted

## Context

M3 builds the ten-stage gauntlet from ROADMAP §3. A first draft followed the roadmap closely. Reviewing it, then
measuring it on synthetic markets where the truth is known, showed six problems. Each decision below fixes one.

## Decisions

### 1. Judge the edge, not the raw return

Every statistic uses the return beyond a static position in the instrument's buy-and-hold:
`r − rf = α + β (b − rf) + ε`, keeping `α + ε` (`metrics.edge`). Its Sharpe is the appraisal ratio, and adding the
strategy to buy-and-hold raises the best attainable Sharpe exactly when it is positive (Treynor & Black, 1973;
Gibbons, Ross & Shanken, 1989).

Why: on raw returns, SPY buy-and-hold alone has a t-statistic near 3.7 over 33 years, so any rule that is mostly
invested "passes" on beta. Zheng (2026) found gates judged on absolute returns reject true signals and admit
exposure. A rule that is 60% invested scores zero edge.

### 2. One fixed lockbox date, and peeking spends it

The lockbox is everything from **2024-10-01** on, for every instrument (`lockbox_from` in the thresholds). The
draft used "the last two years", which rolls forward daily and quietly turns sealed data into research data.

Backtest Studio runs stop the day before the lockbox unless the user ticks a box. A run that includes sealed
dates spends that research question's lockbox, because peeking and opening are the same act. The ledger is the
source of truth: a question's lockbox is spent when any trial on it covers a sealed date. A spent lockbox can't
validate a card; a forward paper test (M7) has to take its place.

Consequence for this lab today: three Studio runs from M2 (`trend_sma:SPY:1d`, `trend_sma:TLT:1d`,
`tsmom:BTCUSDT:1d`) covered 2024-2026, so those three lockboxes are already spent. Other families on those
instruments are untouched.

### 3. N counts what existed when the candidate was chosen

The Deflated Sharpe Ratio deflates against every distinct trial on the instrument, across all strategy families
(choosing a family is also a choice), that existed before the gauntlet run, plus the candidate. The run's own grid
of up to 300 settings is recorded afterwards, so every later candidate on that instrument is deflated against it.

Why: the draft counted the grid it had just generated, which punishes the first candidate for settings nobody had
seen. Counting it for later candidates closes the loophole of looking at the heatmap and then submitting the best
cell.

Variance across trials comes from the trials' edge Sharpes. With equicorrelated trials the cross-sectional
variance shrinks by (1 − ρ), exactly as the expected maximum does, so N needs no separate correlation adjustment.

### 4. Sharpe inference follows the 2026 standard

PSR, DSR and MinTRL use the Sharpe variance corrected for skew, fat tails and lag-1 autocorrelation, evaluated
under the null (López de Prado, Lipton & Zoonekynd, 2026, *JPM* 52(6)). Trend rules hold positions for weeks, so
their returns are autocorrelated and the iid formula understates the error. The card also reports the observed
false-discovery rate (posterior probability of no edge, prior 5% real ideas, real edge Sharpe 0.5), the plainest
answer to "is this real?".

### 5. Permutations keep the volatility path

Calibration (below) found that a plain shuffle of bars passed a no-edge 200-day trend rule 8% of the time at
p ≤ 0.05 on GARCH markets. Shuffling destroys volatility clustering, so the null is too easy to beat for rules
whose exposure tracks volatility. Bars are now shuffled in units of local volatility (EWMA, λ = 0.94) and rescaled
to the volatility where they land: the null keeps the real calm and turbulent stretches and randomises only
direction and order.

### 6. Thresholds come from calibration, not from real strategies

`qlab calibrate` runs the unchanged gauntlet on 1,000 GARCH(1,1) Student-t noise markets (five families; a
researcher who fixed parameters in advance and one who kept the in-sample best of the whole grid) and on 640
markets with a planted regime-switching trend sized to a known long-run edge (Sharpe 0.5 and 1.0 for the trend
rule), at 5, 10, 20 and 30 years.

With the roadmap's starting thresholds, noise passed 0 of 1,000 times, but a true 0.5 edge was caught 2% of the
time with 20 years of data. Re-judging the same runs (`judge()` is the only place thresholds decide, so this needs
no re-run) showed three gates costing power without buying protection:

| Change | Why |
|---|---|
| PBO reported, not gated (`pbo_max` 1.0) | On a plateau, picking the best setting is a coin flip even when every setting has the edge, so PBO sits near 0.5 for real edges. Gating halved power to cut false positives from 2.0% to 0.9%. |
| Walk-forward needs a positive out-of-sample edge, no "keeps 50% of in-sample" ratio | The ratio is noise when the in-sample Sharpe is modest. |
| Permutation p ≤ 0.05, not 0.01 | One pre-specified rule; search is handled by the Deflated Sharpe. |

Thresholds tuned on one study are confirmed on another with different seeds, so they aren't fitted to the
simulated markets they are judged on. Per-family false-positive rates, not only the pooled rate, must stay at or
below 5%.

A confirmation study on fresh seeds (B) showed the pooled rate hiding a family problem: trend rules fixed in
advance passed noise 7% of the time. The permutation test and the Deflated Sharpe rank noise runs almost
identically (rank correlation about 1.0), and both run slightly hot under volatility clustering, so passing both is
no stricter than passing one. The volatility-preserving shuffle (decision 5) moved the trend rule's permutation
false-positive rate from 11% to 9%, which was not enough, so the permutation level became 0.02 (thresholds v2). A
third study on new seeds (C) confirmed it.

## Results

Each study: 1,000 noise runs (five families, two researcher types, 20 years) and 640 planted-trend runs.

| Study | Seeds | Shuffle | Thresholds | Noise passes (pooled) | Worst family | Edge 0.5, 20 y | Edge 1.0, 20 y |
|---|---|---|---|---|---|---|---|
| A | 1,001+ | plain | roadmap | 0.0% | 0% | 2% | 50% |
| A, re-judged | 1,001+ | plain | PBO off, no WF ratio, p ≤ 0.05 | 2.0% | 8% | 68% | 90% |
| B (`cb29ac97`) | 50,001+ | plain | v1 (as above) | 2.4% | 7% | 57% | 95% |
| B' (`722b431d`) | 50,001+ | volatility-preserving | v1 | 2.4% | 7% | 57% | 95% |
| **C (`8029abfe`)** | **90,001+** | **volatility-preserving** | **v2 (p ≤ 0.02)** | **1.4%** (CI 0.8–2.3%) | **3%** | **52%** | **95%** |

**M3 exit statement (study C):** with 20 years of daily data the gauntlet detects a single-instrument edge
Sharpe of 1.0 95% of the time and 0.5 about half the time (52%); with 10 years, 70% and 15%; and it passes noise
1.4% of the time, no family above 3%.

## What the calibration means for strategy research

Statistical power on one instrument is low no matter how good the gates are. A single one-sided 5% test of an edge
Sharpe of 0.5 has about 73% power with 20 years of daily data and about 40% with 10. Crypto has 7 years of
research data. Single-instrument timing edges of realistic size are therefore hard to tell from luck.
Diversification across instruments is the way out: independent edges of Sharpe `s` on `k` instruments combine to
roughly `s·√k`, which is both more profitable and far easier to verify. This motivates moving multi-asset
portfolio research ahead of the paper inbox and mining (decision 0005).

## Disclosure

During development the gauntlet was run on real SPY and BTC data, in a scratch copy of the lab that isn't part of
the ledger. Every threshold and method change above was decided on synthetic calibration evidence only.

## Consequences

- The gauntlet is honest in both directions: about 1–3% of noise passes, and real edges of realistic size are
  caught as often as the data length allows.
- PBO, the false-discovery rate and the minimum track record are still on every card as diagnostics.
- Calibration writes its grids to the ledger under `calib:` questions (about 400,000 trials per full study, about
  60 MB). They are excluded from every N and hidden from the Ledger by default.
