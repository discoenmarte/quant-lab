# 0006. A survivorship-free crypto cross-section: dynamic panels, drifting positions and a cross-sectional null

- Date: 2026-10-18
- Status: accepted

## Context

All ten pre-registered ETF portfolio candidates were rejected in M3b. That wasn't because the gauntlet is
strict. Edges of Sharpe 0.2-0.35 on about 20 years of daily data can't be told from luck by any honest method
(decision 0004, docs/SCALING.md). Power grows with independent bets, so M3c builds breadth from the free
Binance archive. That needed five design changes, each decided before any strategy ran on real crypto data.

## Decisions

### 1. Ingest everything the archive lists, delisted pairs included

The archive is an S3 bucket. Its listing (1,000 entries a page) says which kline files exist and gives each
file's MD5. `qlab ingest --source binance --universe usdt` does the following:

1. Lists every USDT pair.
2. Leaves out pairs that aren't crypto risk, recording a reason for each in a manifest:
   - stablecoins and fiat
   - gold tokens
   - wrapped or staked copies
   - leveraged tokens: `BTCUP` is one because `BTC` is listed; `JUP` and `SYRUP` aren't
   - tokenized equities (`AAPLB`...), all listed from June 2026
3. Downloads the 1d monthly files, plus daily files for the current and previous month, verified against the
   listed MD5.

On 2026-10-18 the archive held 757 USDT pairs: 588 were kept and 169 left out. The kept pairs include delisted
ones such as LUNA, ANC and BTT. Fetching over one keep-alive pool from the bucket's own endpoint took the
ingest from about five hours to about twenty minutes.

### 2. Dynamic panels: an availability mask and point-in-time membership

- **Mask:** a panel can hold instruments that enter and leave. An availability mask (T × N) marks the bars that
  exist (positive prices and volume); prices are NaN elsewhere.
- **Membership:** on each date, the top N by average dollar volume over the past 30 days, from data up to that
  date only. A pair needs bars on all 30 days, so a new listing, or one back from a halt, waits a full window.
- **Universes:** `crypto_top30` and `crypto_top100` start on 2019-02-14, the first date with 20 members. Before
  2019, Binance listed few USDT pairs.
- **Jumps:** a series is split at a one-day rise above 20×, a reused ticker. Before the lockbox this fired only
  after token swaps at the end of trading halts (COCOS, DREP, the new LUNA), where the halt had already split
  the series.
- **Real crashes stay:** the LUNA collapse (−94%, −100%, −84% on 2022-05-11..13) is kept; a rule that held LUNA
  loses with it.

### 3. Drifting positions, and a delisted position exits at its last close

The M3b engine holds constant fractions: each day starts at the target weights. That is a free daily rebalance:
selling what rose and buying what fell. In a cross-section where coins move 4-6% a day, that hidden trade is
worth several percent a year if daily returns mean-revert, and it favours any rule that tilts toward volatile
coins. On dynamic panels the engine therefore holds drifting positions, as a real account does:

- **Trades:** the book trades only on days its targets change.
- **Membership:** it is enforced on those days. A holding that falls out of the top N between rebalances is
  kept, since it still trades.
- **Exits:** a position whose pair has no bar the next day is sold at its last close, paying costs, and the money
  waits in cash.

The exit is optimistic. Real delistings are announced days ahead, with falling prices and widening spreads, and
an unannounced halt can trap a position. The benchmark is equal weight of the members, rebalanced weekly, through
the same engine and costs. Static ETF panels keep the constant-fraction engine, so the M3b calibration (study D)
and verdicts stay valid.

Costs on the cross-section are the `crypto_xs` preset: 20 bps a side (10 bps taker fee, half of a 10 bps spread,
5 bps slippage) on every pair, the majors included.

### 4. Look-ahead checks that change who trades

On a dynamic panel the future-perturbation check changes three things after the cut and recomputes membership:

- prices
- dollar volume (log-uniformly by 0.01-100×)
- which coins keep trading (a fifth delist, half of them on the first day)

Every rule passes. Three cheating rules are caught 5 of 5 times: rules that read tomorrow's membership,
tomorrow's volume or tomorrow's delistings.

### 5. Calibrate on synthetic cross-sections with zero true edge

The synthetic crypto market (`synthetic_crypto_panel`):

- coins list and delist at random
- volatilities are log-normal around 80%
- a common GARCH factor and per-coin GARCH shocks with Student-t(5) tails
- dollar volume is persistent and independent of returns
- the universe is the same point-in-time top N

The noise version gives every coin a zero expected arithmetic return. Then every long-only, fully invested rule
has an expected edge of exactly zero against equal weight, whatever it tilts toward. That makes even the
low-volatility rule calibratable; a drift defined in log terms would pay volatile coins more. Checked across 36
noise markets of 20 years at zero cost: the four rules' mean edges were within 1.6 standard errors of zero.
Power is measured on planted momentum (a hidden trend per coin) and on a planted premium for small dollar volume.

### 6. The permutation null for cross-sectional rules: deal each day's moves out across coins

The spec asked for the M3b null, a shuffle of whole days, with the mask kept in place. On synthetic cross-sections
it turned out blind to a whole class of real edges.

**The problem.** A day shuffle keeps each coin's own moves, only in another order. So it also keeps each coin's
average move per unit of volatility. A persistent tilt, such as small coins earning more for years, is therefore
present in the null worlds too, and a rule that captures it can't beat them.

**The alternative.** A cross-sectional null tests what a cross-sectional rule actually claims: that a coin's own
characteristics predict its next move relative to the others. On each day it deals the moves out again at
random, in units of each coin's local volatility, and rescales each to the volatility of the coin that receives
it. It keeps:

- the order of days and the market's move each day
- every volatility path
- dollar volume, membership and the mask

Comparing the two found and fixed a flaw. The table shows the share of 200-world permutation tests with
p ≤ 0.02, on top-30 markets of 6.5 years with default parameters; 48 markets for low-vol and size noise, 24 per
other row.

| Market | Day shuffle | Cross-sectional, log units | Cross-sectional, simple units |
|---|---|---|---|
| noise: momentum | 0% | 0% | 0% |
| noise: reversal | 0% | 0% | 0% |
| noise: low volatility | 6% | **40%** | 2% |
| noise: size | 2% | 2% | 4% |
| momentum, true edge ≈ 0.8 | 46-67% | 54% | **88%** |
| size, true edge ≈ 0.4-0.6 | 0% | 62% | 25% |

The three columns use different seeds, so rows compare across columns only roughly. Notes from the full log:

- Edges before costs and after costs gave the same rates, so the stage keeps the after-costs edge used
  everywhere else.
- The size edge in the last column is smaller (0.41 against 0.57), which explains most of its lower power.

**The flaw, and its fix.** The first cross-sectional version dealt moves in log units. A log move carries its own
coin's volatility drag (about −σ²/2). Handed from a volatile coin to a calm one, it gives the calm coin too much
drag, so in the null worlds calm coins lose and volatile coins gain. A low-volatility rule with zero true edge
then beat its null 40% of the time. Dealing **simple** returns per unit of volatility keeps a coin with zero
expected return at zero wherever its moves land, and brought that rate to 2%.

**The opportunity set.** The final version deals members' moves among members (by the previous close's
membership) and other pairs' moves among themselves.

Every choice in this section was made on synthetic markets. No real crypto result had been computed when they
were made.

## Results: calibration study E

Study E: report `d4239d73`, seeds 170,001+, thresholds v2, 800 runs, 183,200 trials under `calib:` questions,
105 minutes on 14 processes.

**Noise (640 runs, 5.6-year markets):**

- Noise passed 1 of 640 times: **0.16% pooled** (95% CI 0.0–0.9%).
- **Every family is at or below 2.5%.** The worst is top-30 size with a tuned researcher, at 1 of 40.
- **No threshold changes.** The rate stays at 0.16% when the stages that look at absolute returns (net CAGR > 0
  and regimes) are switched off, so the low rate doesn't depend on the synthetic market falling.

**Power** (share of planted-edge markets admitted; true edges measured on eight 50-year markets):

| Rule | True edge | 4 years | 5.6 years |
|---|---|---|---|
| Momentum (top 30) | 0.49 | 20% | 40% |
| Momentum (top 30) | 0.98 | 60% | **85%** |
| Size (top 30) | 0.51 | 0% | 0% |
| Size (top 30) | 1.00 | 10% | **5%** |

**Why size power is low.** Three causes:

1. **Absolute returns.** The costs stage also requires a positive net CAGR, and the regimes stage requires
   positive net returns in at least 60% of regimes. Zero arithmetic drift at about 80% volatility makes almost
   every long-only crypto portfolio shrink in compound terms, whatever its edge. Planted momentum lifts the
   portfolio's own returns, while a size tilt that only beats the index doesn't.
2. **Statistics.** With the CAGR and regimes conditions switched off, size power at edge 1.0 is still only
   30-35%. The permutation and Deflated Sharpe stages each pass about 45%.
3. **Smaller realized edges.** The realized edge after costs in these 5.6-year markets has a median of 0.65,
   below the 1.0 measured on 50-year markets. The synthetic universe is younger and changes faster.

So a rejected size candidate is weak evidence against a size premium. A rejected momentum candidate says more.

## First real verdicts (pre-registered batch `23275b56`, 2026-10-18)

The batch ran after the pre-registration was committed (`ed1d334`), on research data only (2019-02-14 to
2024-09-30). **All eight were rejected.** Every lockbox is still sealed.

| Candidate | Edge after costs (before) | Permutation p | PSR, N = 1 | Verdict |
|---|---|---|---|---|
| momentum, top 30 | +0.64 (+0.82) | 0.054 | 0.93 | rejected at walk-forward (out-of-sample −0.07); also plateau, permutation, significance |
| reversal, top 30 | −4.00 (−1.47) | 1.000 | 0.00 | rejected at costs; fails everything |
| low volatility, top 30 | +0.43 (+0.72) | **0.012** | 0.84 | rejected at significance only (minimum track record 12.3 years) |
| size, top 30 | −0.87 (−0.61) | 0.934 | 0.02 | rejected at costs |
| momentum, top 100 | +0.23 (+0.48) | 0.168 | 0.71 | rejected at walk-forward (out-of-sample −0.14) |
| reversal, top 100 | −4.76 (−1.04) | 1.000 | 0.00 | rejected at costs |
| low volatility, top 100 | +0.02 (+0.52) | 0.052 | 0.52 | rejected at plateau |
| size, top 100 | −0.32 (+0.05) | 0.433 | 0.23 | rejected at costs |

What the batch says:

- **Reversal is wrong-signed in a liquid universe, even before costs.** Yesterday's losers among the top 30 or 100
  kept losing. That matches Zaremba et al.'s finding of daily momentum among the largest coins. Daily turnover at
  20 bps then makes it ruinous.
- **The size premium didn't show up.** The least-traded members did worse than equal weight among the top 30 and
  about the same among the top 100. Calibration said a size rejection is weak evidence (power 5-10%), but here the
  edge itself is negative, which is more than a lack of power.
- **Momentum earned an edge on the top 30 (+0.64 after costs), but not robustly.**
  - Choosing parameters on the past did worse than nothing in the following year.
  - Only 7 of 35 neighbouring settings kept half the edge.
  - It is weaker on the top 100.
- **Low volatility on the top 30 is the near-miss.** It passed costs, walk-forward (+1.45 out of sample), the
  plateau, the permutation test (p = 0.012) and regimes. It failed significance: even undeflated, the chance its
  edge is above zero is 0.84, and the minimum track record at its Sharpe is 12.3 years against 5.6 available.
  This runs against the published expectation (no low-volatility premium). It is a candidate for the forward
  paper book, not for money.
- **Every long-only crypto portfolio fell about 95% from the 2021 peak.** The bootstrap stage sizes each at 9-11%
  of capital to keep its 95th-percentile drawdown within 25%.

**Disclosure about the Deflated Sharpe.** Every card shows DSR 0.000 with a luck threshold of edge Sharpe 2.25
(top 30) and 2.51 (top 100). The threshold comes from the spread of the batch's trial Sharpes, and the
reversal candidates' cost-driven −4.0 and −4.8 inflate that spread far beyond what luck produces.

The verdicts don't depend on it. Without any deflation (N = 1, the PSR column), no candidate reaches 0.95. Every
candidate except low volatility on the top 30 also fails another stage.

Estimating the cross-trial variance robustly is a method change for later batches. Two options: leave out trials
whose edge is negative before costs, or use a spread that ignores outliers. Either should be decided on
calibration evidence.

## Open question for the owner

The costs stage (net CAGR > 0) and the regimes stage judge absolute returns, while every other stage judges the
edge over the passive alternative (decision 0004 §1). For a long-only crypto rule in a falling market, that
rejects an edge that beats the index but still loses money. Whether that is the right gate for a spot trader is
a choice about goals, not statistics. Changing it should come with a calibration study on markets with drift,
and applies only to later batches.

## Consequences

- The lab has breadth for free: 588 pairs, up to 100 members a day, delisted pairs included.
- Cross-sectional rules are judged against the passive alternative, equal weight of the same point-in-time
  universe, with drifting positions and realistic costs.
- Known optimism that remains:
  - exits at the last close
  - one cost level for all pairs
  - the archive's daily bars hide intraday liquidity
