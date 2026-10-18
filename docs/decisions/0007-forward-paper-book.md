# 0007. The forward paper book: decisions before outcomes, frozen expectations, and honest power

- Date: 2026-10-18
- Status: accepted

## Context

Decision 0005 moved the forward paper book (M7a) ahead of the inbox and mining, because forward evidence only
accrues with calendar time and costs nothing. Forward evidence is the one kind no backtest can fake, but only if
every decision is provably recorded before its outcome exists and is scored by the same code that was researched.
Building it raised seven questions. Each decision below answers one.

## Decisions

### 1. Record targets with wall-clock times, not just dates

A decision is the target an entry's strategy computes from data up to one close (`as_of`). It is stored with the
UTC time it was taken (`decided_at`) and the first open it can fill at (`next_open`), in an append-only table (one
row per entry and date, never updated). Daily bars are keyed by date, so the times come from the asset class
(`paper.sessions`): US ETFs open at 09:30 and close at 16:00 New York time, and a close counts as final 30 minutes
later because Yahoo revises the last price for closing-auction prints; Binance daily candles run from 00:00 to 00:00
UTC and the archive only publishes complete days. Calendars are weekdays for equities and every day for crypto.
Holidays aren't modelled, which can only make the book refuse a decision it could have taken.

### 2. Refuse backfills and stale data; keep late decisions but mark them

`qlab paper run` decides only on the latest bar whose close is final, and refuses:

- **a backfill:** a later final bar is already stored. A missed day stays missed; the next decision is on the
  newest close, and in between the last target is held (as a real book would).
- **stale data:** the next session has already closed in the world but isn't stored. Deciding now would look like
  a decision before that session while being taken after it. Ingest first.

A decision taken after the next open (but before that session closes) is accepted and marked **late** with its lead
time. Its target can't depend on anything after its close (it is computed on data clipped at the close), but filling
it at that open is optimistic. Crypto entries are always late with the public archive, which publishes a day after
the next candle has opened; their forward record carries that optimism until broker paper fills (M7b) measure it.

### 3. Run the researched code, score with the researched engines

Targets come from the card's own subject (`gauntlet.subjects`) and strategy, on the data clipped at the decision's
close. Scoring replays the stored targets through `portfolio.engine` or `backtest.engine`, with the same next-open
fills, costs and cash rate, and holds the last target on days without a decision. A test checks that the replay
equals a direct engine run with the strategy's own weights. The first decision's own day isn't scored (its return
happened before the decision), and bars whose close isn't final yet are left out.

### 4. Freeze the expectations when a card enters the book

Adding a card re-simulates its research period once (a ledger trial) and stores what it expects: the beta to the
benchmark, the mean, volatility, skew, kurtosis and autocorrelation of the daily edge, and the same for daily log
returns. Forward edge uses that **research beta**, fixed in advance: with a few weeks of data a beta estimated on
the forward days would absorb most of the return. The bands compare against these numbers and never move:

- forward equity against the research period's expected growth, ±1.645 standard deviations of cumulative log
  return, widened by `(1 + ρ)/(1 − ρ)` for autocorrelation;
- cumulative edge against its expected path, the same way;
- forward edge Sharpe against the card's research band, the band the lockbox stage uses.

Any verdict can join. Rejected near-misses cost nothing to track and show what a candidate's forward test looks
like; the verdict stays on the entry so nobody mistakes one for a validated strategy.

### 5. A CUSUM decay alarm with a stated false-alarm rate, and its power said out loud

The alarm is Page's (1954) one-sided CUSUM on standardised forward edge, tuned to detect the edge falling from its
research mean to zero (`k = μ0/(2σ0)`). The threshold `h` gives one false alarm every ten years on average while the
edge is as researched, from Siegmund's (1985) approximation of the average run length; a Monte Carlo test checks the
in-control and out-of-control run lengths against the formula within 12%. The alarm doesn't apply to a card whose
research edge isn't positive.

The power is low and the page says so. For the trend-filter near-miss (research edge Sharpe 0.33), `h` is 41.6 and
the alarm takes about 5.5 years on average to fire after the edge vanishes; the Sharpe band clears zero only after
about 21 years. So the book guards against breaks (a bug, a data change, a market that no longer behaves like the
research period) and large decay. It cannot confirm a small edge in months. The go-live gate (M8) has to rest on
the gauntlet's verdict plus forward consistency (no alarm, inside the band, no late or refused decisions), not on
a few months of paper profit.

### 6. Paper trials go to the ledger under their own question

Every scoring that sees new bars is a ledger trial (source `paper`) on the question `paper:<research question>`,
and so is the reference re-simulation. A separate question means forward scoring never spends the research
question's lockbox. Paper trials are also left out of the selection set (`ledger.selection_set`) and of the
spent-lockbox list: scoring a fixed decision record on new days chooses nothing, so it mustn't raise N for the next
candidate.

Known gap: the lockbox runs from 2024-10-01 to the latest bar, so it overlaps the forward days. If a card in the
book later opens its lockbox, part of that verdict comes from days the owner has already seen in the book. Only
candidates can open a lockbox, and the book holds none yet; capping the lockbox window at an entry's first decision
is the fix when one does.

### 7. Schedule it, but let the owner install it

`qlab paper nightly` ingests the free sources, decides and scores. DuckDB allows one writer, so when `qlab serve` is
up the job asks the server to do each step through the API; otherwise it runs in process. `make paper-launchd`
writes a launchd job for this checkout under `data/ops/` at a local time after the New York close all year (19:00 on
this machine) and prints the install steps. The lab never installs it.

## Consequences

- The forward clock can start the night a card is added. Decisions are idempotent, so the job can run any number of
  times a day.
- The Trading Desk shows the book, each entry's forward equity and edge against frozen bands, the alarm with its
  threshold and expected detection delay, and every decision with its timing.
- Approximations carried forward: weekday calendars without holidays; the Ken French risk-free rate is published
  with a lag, so forward days use its last value; the engines' daily rebalancing to target weights (as in research).
- Nothing in the book can place an order. Broker paper accounts, real fills and a kill switch are M7b.
