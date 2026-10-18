# Pre-registration: published crypto factors on the Binance cross-section (M3c)

- Written: 2026-10-18, before any strategy has been run on the real crypto cross-section
- Author: discoenmarte
- Status: registered in commit `ed1d334`, then run as batch `23275b56` (results appended below)

## What is being tested

Do the long legs of published cross-sectional crypto factors beat equal weight of the same point-in-time
universe, after costs, on Binance spot data that includes delisted pairs?

Eight candidates, each judged by the unchanged ten-stage gauntlet (thresholds in force when the batch runs) and
deflated against the whole batch (`judge_batch`):

| Rule | Published source | Parameters (as published) |
|---|---|---|
| `xs_weekly_momentum` | Liu, Tsyvinski & Wu (2022), *Common Risk Factors in Cryptocurrency*, J. Finance 77(2): three-week momentum r(3,0), their main momentum measure | lookback 21 days, skip 0, top quintile, rebalance every 7 days |
| `xs_reversal` | Zaremba, Bilgin, Long, Mercik & Szczygielski (2021), Int. Rev. Financial Analysis 78: coins with a low prior-day return beat those with a high one | lookback 1 day, bottom quintile, rebalance daily |
| `xs_low_vol` | LTW (2022) RETVOL, the standard deviation of daily returns in the formation week; Burggraf & Rudolf (2021), FRL 40 | lookback 7 days, lowest quintile, rebalance every 7 days |
| `xs_small_size` | LTW (2022) PRCVOL, average daily dollar volume in the formation week (size proxy) | lookback 7 days, lowest quintile, rebalance every 7 days |

Each rule runs on two universes: `crypto_top30` and `crypto_top100`.

## Data, universe and costs (fixed before any run)

- **Data:** Binance public archive, spot 1d klines of every USDT pair, delisted pairs included. The archive was
  listed on 2026-10-18 18:10 UTC. Of the 757 pairs, 588 were kept and 169 left out:
  - 87 tokenized equities, all listed from June 2026
  - 50 leveraged tokens
  - 26 stablecoins and fiat
  - 4 wrapped or staked copies
  - 2 gold tokens

  The manifest with each reason is `data/curated/binance/usdt_universe.json`. A series is split at a gap and at
  a one-day rise above 20x. Before the lockbox, that fired only after trading halts with token swaps (COCOS
  2021-01, DREP 2021-04, the new LUNA 2022-05-31). The LUNA collapse itself (−94%, −100%, −84% on 2022-05-11..13)
  is kept.
- **Universe:** each day, the top N pairs (30 or 100) by average dollar volume over the past 30 days. Only data up
  to that day is used, and a pair needs bars on all 30 days. Both universes start on 2019-02-14, the first day with
  20 members.
- **Research period:** 2019-02-14 to 2024-09-30 (5.6 years). The lockbox (2024-10-01 on) stays sealed; it is opened
  only for a candidate that passes stages 1-9, as a separate, recorded decision.
- **Engine:**
  - Signals are decided at the close and filled at the next open.
  - Positions drift between trades; the book trades only when targets change.
  - Membership is enforced when targets change.
  - A position whose pair stops trading is sold at its last close. That is optimistic: real delistings are
    announced with falling prices and wider spreads.
- **Costs:** `crypto_xs`, 20 bps a side (10 bps fee, 5 bps half-spread, 5 bps slippage) on every pair, including
  the majors.
- **Benchmark:** equal weight of the members, rebalanced every 7 days, through the same engine and costs.

## Deviations from the papers

- **Weights:** equal weights inside the quintile; the papers use value weights, and the archive has no market caps.
- **Long legs only:** the papers report long-short spreads; the lab trades spot.
- **Universe:** the liquid Binance top N, not every coin above $1M market cap on CoinMarketCap.
- **Sample:** 2019-2024, which is after the papers' samples (LTW 2014-2018; Zaremba et al. to 2020). This is a
  post-publication test, where decay of around half is typical (McLean & Pontiff, 2016).

## What the published evidence leads me to expect

- **Low volatility:** rejection. LTW report the RETVOL spread as insignificant, and Burggraf & Rudolf find no
  low-volatility premium.
- **Reversal:** rejection on costs. It trades about every day at 20 bps, and Zaremba et al. find daily momentum,
  not reversal, among the largest coins, which is where our universe sits.
- **Momentum and size:** the only ones with a published premium large enough (about 3% a week long-short in
  2014-2018) to survive costs. But this is a liquid universe in a later sample, and only the long leg.

## Method of judgement

- `qlab portfolio-batch --universes crypto_top30,crypto_top100 --strategies
  xs_weekly_momentum,xs_reversal,xs_low_vol,xs_small_size`, seed 7.
- Each candidate is deflated against the eight.
- **Thresholds:** version 2, unchanged by calibration study E:
  - permutation p ≤ 0.02 with 500 null worlds
  - Deflated Sharpe ≥ 0.95
  - grids of at most 300 settings
- **Permutation null:** the cross-sectional one (decision 0006 §6). Each day's moves, as simple returns in units
  of local volatility, are dealt out again among the previous close's members, and separately among the other
  pairs.
- **Research data fingerprints:** `crypto_top30` `fde2003f893a66f3` (374 pairs ever members),
  `crypto_top100` `7cde858747fe54f0` (565 pairs).
- No other parameter setting on these eight questions counts as confirmation of a rejected candidate. Their grids
  are recorded as trials and raise N for any later candidate on the same universe.

## What a verdict can and can't say (from calibration study E, before any real run)

On synthetic cross-sections of this length (5.6 years):

- **Noise:** passes 0.16% of the time (no rule above 2.5%). A pass would be strong evidence.
- **Momentum:** a true edge of 1.0 is caught 85% of the time and 0.5 40% of the time. A momentum rejection is
  informative for edges near 1, weak for edges near 0.5.
- **Size:** caught 5-10% of the time even at a true edge of 1.0. The gates on absolute returns (net CAGR > 0,
  positive in most regimes) and the statistical stages each block it. A size rejection is weak evidence against
  a size premium.

## Results (appended after the run, 2026-10-18)

Batch `23275b56`: the audit event `gauntlet.preregister` came first, then the eight cards. **All eight rejected**;
every lockbox is still sealed.

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

Against the expectations above:

- **Low volatility:** was expected to be rejected. It was, but it was also the closest to passing (on the top 30).
- **Reversal:** rejected as expected, and wrong-signed even before costs.
- **Momentum:** earned an edge on the top 30 that didn't survive re-optimisation or its neighbours.
- **Size:** negative, which is more than the expected lack of power.

The Deflated Sharpe's luck threshold was inflated by the reversal candidates (decision 0006, "Disclosure about
the Deflated Sharpe"). No verdict depends on it.
