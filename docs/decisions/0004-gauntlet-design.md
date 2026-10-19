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

## Addendum (2026-10-19): bound the spread of trial Sharpes by luck (thresholds v3)

### Problem

The Deflated Sharpe's luck threshold is `SR0 = √V · z(N)`, where V is the variance of the trials' edge Sharpes
(§3). In the pre-registered crypto batch `23275b56` (decision 0006), the reversal rules trade every day at 20 bps a
side, which gave them edges of −4.0 and −4.8. Rules like that can never be the best of a batch, but they inflated
V: SR0 reached 2.25-2.51 and every card showed DSR 0.000. Synthetic noise does the same. In a top-30 noise batch the
four crypto rules' edges look like +0.2, −3.6, −0.6 and +0.4, and the median SR0 is 1.68 (2.69 on the top 100).

Any real difference between the trials does this, not only costs. On a planted-trend market the five
single-instrument rules earn genuinely different edges, and the sample variance sets the luck threshold at 0.71-0.82
against 0.27-0.30 for a bound on luck.

### Candidates

All five are computed by `stats.trial_variance` (positive_gross in the study only).

| Method | V | Idea |
|---|---|---|
| `sample` (v1, v2) | the sample variance | every trial counts fully |
| `upper_half` | `2 Σ_{x>m} (x − m)² / (N − 1)`, m the median | the maximum comes from the upper tail; nearly unbiased for a symmetric spread from N = 2 |
| `mad` | `(1.4826 · MAD)²`, small-sample factors | ignores outliers in both tails |
| `luck_bound` | `min(sample, L)` | trials with no skill spread no wider than independent ones; L is the sampling variance of one Sharpe with no edge, `(1 + 2ρ/(1 − ρ)) / T` |
| `positive_gross` | the sample variance of trials with a positive edge before costs | decision 0006's suggestion |

### Study design (`qlab calibrate-variance`)

Each study has 2,480 gauntlet runs under thresholds v2:

- **Best of the grid:** the five single-instrument families (100 runs each, 20 years), the three portfolio timing
  rules (40 each, 18 years) and the four crypto rules on the top 30 and top 100 (40 each, 5.6 years).
- **Pre-registered batches** (`pipeline.judge_batch` on synthetic data): every rule of a market type runs its
  defaults on the same market, and each is deflated against the whole batch. There are 100 markets with the five
  single-instrument rules, 40 with the three portfolio rules, 100 top-30 crypto markets and 40 top-100 crypto
  markets with the four crypto rules.
- **Power:** planted trends (single instrument, portfolio) and planted momentum (top-30 crypto) at true edges 0.5
  and 1.0, as best of the grid and inside a batch.

Every other stage is unaffected by V, so each run records the DSR under every method and the methods are compared
on identical runs. The rule for choosing was written before study F finished:

1. Every noise family must stay at or below 5%.
2. Of the eligible methods, pick the one with the highest power in batches.
3. Confirm the choice on fresh seeds.

### Study F (report `5307ae50`, seeds 210,001+, 580,500 trials, 2.5 hours)

| Method | Noise, pooled (2,120 runs) | Worst noise family | Batch: trend 0.5 / 1.0 | Batch: portfolio 0.5 / 1.0 | Batch: crypto momentum 0.5 / 1.0 | Grid: crypto momentum 0.5 / 1.0 |
|---|---|---|---|---|---|---|
| sample | 0.24% | 2.5% | 0% / 0% | 40% / 65% | 0% / 0% | 7% / 50% |
| upper_half | 1.04% | 4.0% | 35% / 53% | 25% / 40% | 0% / 0% | 23% / 80% |
| mad | 0.94% | 4.0% | 25% / 35% | 40% / 65% | 0% / 0% | 10% / 33% |
| **luck_bound** | **0.47%** | **2.5%** | **35% / 85%** | **40% / 75%** | **7% / 83%** | **30% / 83%** |
| positive_gross | 0.47% | 2.5% | 38% / 55% | 40% / 65% | 3% / 33% | 10% / 67% |

What F showed:

- **Every method keeps every noise family at or below 5%.** The other stages hold noise down on their own.
- **The plain variance throws away almost all power in batches.** Planted crypto momentum with a true edge of
  0.98 is caught 85% of the time when it is fixed in advance (study E). Inside a batch it was caught 0 of 30 times,
  with a median SR0 of 2.38.
- **Robust spreads don't fix it.** A real edge in the batch is itself an outlier. With four trials, the upper half
  keeps the momentum rule's own edge in its spread (SR0 1.54), and the MAD does too (1.92). Only a bound on what
  luck can produce separates spread from luck from spread from real differences.
- **Dropping trials with a negative edge before costs** cuts away about half of every noise grid: the
  best-of-grid SR0 falls to 0.13-0.32, against 0.28-0.50. Real edges still inflate V in batches.
- **luck_bound binds where it should:** in every crypto batch, in 26% of single-instrument noise batches, in about
  28% of crypto grids (the reversal grid's daily settings) and in 3% of other grids. In noise grids it never
  changed a verdict.

### A correction before confirming: `luck_max`

`luck_bound` assumes the trials are at least uncorrelated. Trials without skill spread `(1 − ρ̄) · L` in
expectation, where ρ̄ is their average correlation. ρ̄ can be as low as −1/(N − 1), for example when trend and
reversion rules bet against each other, so luck can spread up to `L · N / (N − 1)`. `luck_max` caps V there:
`min(sample, L · N / (N − 1))`. It is valid at any correlation and never below `luck_bound`, so its noise rate on
F's runs is at most luck_bound's. The difference is 15% of SR0 at N = 4, 5% at N = 10 and nothing at grid size.

Both bounds also average each trial's own L when the trials cover different periods (a shorter history spreads its
Sharpe wider by luck). The gauntlet passes each trial's calendar span from the ledger. Every trial on a scope in
today's ledger shares one period, so this changes nothing yet.

### Study G (report `0a00a0d2`, seeds 250,001+): confirmation

Study G ran on fresh seeds after `luck_max` was added (code `e074aed`, 580,500 trials, 2.2 hours). Its rule was
also fixed before its results: adopt `luck_max` if every noise family stays at or below 5%. If it failed,
`luck_bound` would fail too, since its V is never larger, and the sample variance would stay.

| Method | Noise, pooled (2,120 runs) | Worst noise family | Batch: trend 0.5 / 1.0 | Batch: portfolio 0.5 / 1.0 | Batch: crypto momentum 0.5 / 1.0 | Grid: crypto momentum 0.5 / 1.0 |
|---|---|---|---|---|---|---|
| sample | 0.33% | 2.5% | 5% / 0% | 40% / 75% | 0% / 0% | 10% / 43% |
| upper_half | 0.52% | 5.0% | 25% / 58% | 40% / 50% | 0% / 0% | 13% / 87% |
| mad | 0.52% | 5.0% | 23% / 38% | 40% / 80% | 0% / 0% | 10% / 43% |
| luck_bound | 0.38% | 2.5% | 20% / 75% | 40% / 85% | 13% / 60% | 37% / 90% |
| **luck_max** | **0.33%** | **2.5%** | **20% / 73%** | **40% / 85%** | **13% / 60%** | **37% / 90%** |
| positive_gross | 0.38% | 2.5% | 28% / 60% | 40% / 75% | 7% / 43% | 10% / 53% |

`luck_max` admitted exactly the same noise runs as the sample variance: no family above 2.5%. Its mean power is
49% in batches against 20%, and 81% best-of-grid against 68%. Median luck thresholds in noise batches are 0.51
(top 30 and top 100) against 1.70 and 2.73 for the sample variance. The bound binds in every crypto batch, in 15%
of single-instrument batches, in about 30% of crypto grids and in 2-3% of other grids.

Batch power on crypto momentum at 0.98 was 83% in F and 60% in G. With 30 markets per cell, each rate is uncertain
by about ±15 points.

### Decision

**Thresholds v3 = v2 with `dsr_variance: luck_max`.** Fresh labs default to it. Versions saved before the key
existed keep the sample variance (`thresholds.LEGACY`), so every card judged under v1 or v2 keeps its verdict and
its numbers. No card was re-judged.

For batch `23275b56` the change would have meant a luck threshold of about 0.5 instead of 2.25-2.51. That figure
comes from the published edges, the 5.6-year research length and four trials per universe, assuming little
autocorrelation. No verdict would change: no candidate reached PSR 0.95 even undeflated.

### Disclosure

- During design, the values `upper_half` and the luck bound give on batch `23275b56`'s published top-30 edges were
  worked out by hand (about 0.9 and 0.5). The choice rests on studies F and G alone, and the decision rule was
  written down before F's results were seen.
- Study F's code stamp is `3dfa32c-dirty`. The uncommitted files belonged to another session's milestone (M6:
  config, db, alerts); none was in the gauntlet.
- The first launch of study G was stopped at 500 of 2,480 runs to add `luck_max`. Nothing from it was summarised,
  saved or recorded, and G restarted on the same seeds.
- Development checks (timing one run per market type, the card wording, a smoke run of the study) ran `evaluate`
  on synthetic markets in scratch labs, outside the real ledger. None used real data.
- The two studies added 1,161,000 calibration trials, about 110 MB each, to `data/lab.duckdb`.
