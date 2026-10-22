# 0015. Intraday mode on free hourly crypto bars: a library, its calibration, and the cost cliff

- Date: 2026-10-22
- Status: accepted

## Context

The roadmap's optional M9 is a "day-trading mode in the version that can survive testing":

- a library of intraday rules validated on years of data
- a morning selector, itself validated
- slow learning, and a replay player

The roadmap's US version needs consolidated minute data (about $29-99 a month); the free IEX feed is too thin. The
owner asked to finish every milestone as cheaply as possible.

## Decisions

### 1. Free hourly crypto bars, the UTC day

- **Data:** the Binance archive's hourly BTCUSDT and ETHUSDT bars since 2017-08, already ingested and checksummed.
  They cost nothing.
- **The day:** UTC. Its "open" is 00:00 UTC, when Binance's daily candle starts.
- **Rules are ordinary registered strategies on 1h bars,** so the engine, the gauntlet (hours compounded into
  daily returns, 365 days a year), the ledger and the lockbox treat them like any other.
- **Daily bars are refused.** A rule on daily bars would never trade, so `_calendar` raises an error instead.

### 2. The library and its selector

- `intraday_momentum` holds the day's last `hold` hours when its first `window` hours went up (Gao, Han, Li & Zhou
  2018; on Bitcoin, Wen et al. 2022).
- `range_breakout` buys a close above the high of the first `window` hours and holds it to 00:00 UTC.
- **Both are long-only and flat over midnight.** They pay a round trip on every traded day: 28 bps with the crypto
  cost model.
- **The selector** is the roadmap's "morning computes, it doesn't search". `vol_filter` trades only after a day more
  volatile than the previous 60 days' median. It is a parameter of each rule, so its use is in the grid and in N.

### 3. Calibrated before any real run, on synthetic hourly markets

`intraday.synthetic.synthetic_hourly` builds the calibration markets:

- GARCH daily variance with Student-t shocks
- an intraday volatility profile peaking at 15:00 UTC
- planted momentum (optional) linking the day's first and last hours

`qlab calibrate --hourly-reps N --hourly-power-reps M` runs families `hourly/<rule>`.

| Report | What | Result |
|---|---|---|
| `343740d3` | noise, fixed and grid-searching researchers, 100 runs each per rule | 0 of 400 passed (CI 0-1.0%) |
| `343740d3` | power, one-hour planted momentum, at the defaults | 0 of 40: all at plateau |
| `97659722` | power, four-hour planted momentum, at window 4 and hold 4, true edges 0.5 and 1 | 0 of 80: plateau (and costs at 0.5) |
| `d291f58f` | the same at true edges 2 and 3 | 0 of 40: all at plateau |

**The cost cliff.** Net edge is gross edge minus a large fixed cost per traded day. A setting one step away keeps
most of the gross gain but pays the same costs, so it keeps much less than half of the net. The plateau stage
requires neighbours to keep half the candidate's edge.

For intraday rules at retail crypto costs, the gauntlet is therefore extremely conservative: it passes noise 0% of
the time and can't confirm even a planted edge of Sharpe 3. The thresholds were not changed:

- Fragility to a one-hour change in the window is a real weakness of a trading rule, not a statistical artefact.
- Thresholds come from calibration across every family, never from one family's results.

### 4. Pre-registered verdicts

`docs/preregistration/2026-10-22-m9-intraday-crypto.md`: four questions, each run once with the published defaults.
All four were rejected at costs:

- **Intraday momentum** loses even before costs on both coins.
- **The ETH range breakout** has a gross edge Sharpe of 1.24 with permutation p = 0.004. Its costs break even at
  12 bps against the 14 charged, and it fails walk-forward and plateau.

### 5. The Intraday Cockpit, and what isn't built

**The Intraday Cockpit** (`/intraday`, `intraday.cockpit`, `GET /api/intraday`) shows:

- the library and its grids
- the latest hourly calibration
- the verdicts
- the selector's reading for the latest finished UTC day: that day's volatility against its 60-day median, its
  first hour, and the high of its first four hours

The readings are facts about the day, not a rule's results, so they add no trial and touch no lockbox.

**Not built:**

- **A live hourly feed and forward intraday paper trading.** The paper book trades daily at the open, and the
  archive publishes each day after it ends. Both are worth building only if an intraday rule passes.
- **The replay player.**
- **Reinforcement learning.** The roadmap's own note applies: about 250 daily episodes a year overfit.

## Consequences

- **Intraday trading is not a source of money for this lab at retail costs.** It would take execution near 5 bps a
  side (maker orders and fee discounts) and an edge broad enough to survive one-step changes.
- **A cheaper-execution test** would be a new pre-registered question with its own cost preset, and N counted. The
  ETH breakout's gross signal is the only lead.
- **A follow-up worth a calibration study:** whether high-turnover families should judge the plateau on the gross
  edge, with the costs stage alone guarding net profitability. It would have to hold noise at or below 5% for every
  family before it could change a threshold.
