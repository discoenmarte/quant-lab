# 0008. The registry, the daily report and alerts: derived states, a strict near-miss rule, alerts on changes

- Date: 2026-10-19
- Status: accepted

## Context

By the end of M3c and M7a the lab had 18 report cards, two entries in the forward paper book, a nightly job and
about 1.8 million ledger trials. Nothing told the owner each day what had happened. Answering "what did the lab test,
what passed, what is in paper trading and how is it doing" meant reading four pages and the audit log. Decision 0005
put this layer (M6) next, ahead of broker paper trading, because it is what makes a growing lab readable and makes a
silent failure visible. Building it raised six questions. Each decision below answers one.

## Decisions

### 1. Lifecycle states are derived, never stored

A card's state is computed each time from three records the lab already keeps:

- the card: its verdict and stage results in `gauntlet_cards`
- the paper book: `paper_entries`
- the owner's retirements: `registry_events`, append-only

There is no state column that could drift from them. Six states, by precedence:

| State | When |
|---|---|
| in paper book | the card has an active paper-book entry (any verdict) |
| retired | the owner retired it, or its paper entry was retired and none is active |
| validated | it passed every stage, the lockbox included |
| candidate | it passed stages 1-9 and its lockbox is still sealed |
| near-miss | rejected only at the luck stages, and by little (§2) |
| rejected | everything else |

The verdict is always shown beside the state. A rejected card in the paper book reads "in paper book · rejected", so
forward tracking is never mistaken for validation. Retiring a card that has an active entry also retires the entry.
Re-adding a retired card to the book puts it back in the book.

### 2. A near-miss failed only the stages that ask whether the result is luck, and by little

A near-miss is a card with verdict `rejected` whose failed stages are a non-empty subset of {permutation,
significance}, with a permutation p of at most 0.10 and an undeflated PSR of at least 0.80 (caps added on 2026-10-19,
below). It therefore passed every check of how the rule behaves, where the check applies:

- the look-ahead check
- a positive edge and CAGR after costs
- walk-forward re-optimisation out of sample
- a plateau of neighbouring parameters
- regimes
- diversification against the validated portfolio

What stopped it was the permutation test, the Deflated Sharpe, or both: the stages that ask whether an edge of that
size could come from luck in a search of that size.

**Why these two.** A card that fails a behaviour stage has a known defect: costs eat it, it doesn't survive choosing
parameters on the past, it is a lone peak, or it needs one kind of market. Forward days can't repair that. A card
that fails only the luck stages has an edge measured too imprecisely. Forward days add evidence on exactly that
question, and they cost nothing in the paper book.

**Not near-misses:** a card that failed its lockbox. The lockbox is the out-of-sample verdict, so failing it is
evidence against the edge, not a lack of evidence.

**What "near" didn't mean at first.** As first written, the rule was about which stages failed, not by how much. On
the real lab it labeled four cards, and they differ a lot:

| Card | Question | Edge | OOS edge | Permutation p | DSR | State (first rule) |
|---|---|---|---|---|---|---|
| `d2c5a4b45b03` | xs_low_vol:crypto_top30 | +0.43 | +1.45 | 0.012 (passed) | 0.00 | near-miss |
| `c1c207ae352d` | trend_filter:asset_classes_long | +0.34 | +0.16 | 0.046 | 0.89 | in paper book |
| `353323a3bd56` | xs_momentum:all_etfs | +0.27 | +0.07 | 0.180 | 0.62 | near-miss |
| `1335bb8e04b8` | risk_parity:asset_classes_long | +0.27 | +0.06 | 0.293 | 0.81 | near-miss |

A permutation p of 0.29 is nowhere near the 0.02 threshold, so the board always shows p and DSR on every tile. A
tighter rule, for example "and p ≤ 2× the threshold", is an open question for the owner. It should be decided before
the next batch runs, not fitted to these four.

**Update (2026-10-19): caps on how far a near-miss missed** (owner's choice). The owner answered the open question
with two caps, both inclusive:

- **Permutation p ≤ 0.10.** Five times the 0.02 threshold.
- **Undeflated PSR ≥ 0.80.** The chance that the edge Sharpe is above zero with N = 1, before deflating for the
  search. It uses the same autocorrelation-adjusted Sharpe variance as the Deflated Sharpe, and is computed from the
  card's significance chart: daily edge Sharpe, skew, kurtosis, lag-1 autocorrelation and the number of research
  days. The Deflated Sharpe itself can't serve here, because it falls with every trial on the scope. The undeflated
  PSR asks only whether this edge, on its own, is distinguishable from zero.

A card that fails either cap is `rejected`, even if only the luck stages failed. A card whose chart is missing or
incomplete can't show it missed by little, so it isn't a near-miss either. The rule reads recorded cards; no card
was re-judged, and the verdicts don't change. Only the derived state does.

| Card | Question | Permutation p | Undeflated PSR | State before | State now |
|---|---|---|---|---|---|
| `d2c5a4b45b03` | xs_low_vol:crypto_top30 | 0.012 | 0.84 | near-miss | **near-miss** |
| `c1c207ae352d` | trend_filter:asset_classes_long | 0.046 | 0.95 | in paper book (near-miss) | in paper book (near-miss) |
| `353323a3bd56` | xs_momentum:all_etfs | 0.180 | 0.88 | near-miss | rejected (p cap) |
| `1335bb8e04b8` | risk_parity:asset_classes_long | 0.293 | 0.90 | near-miss | rejected (p cap) |

Both exclusions come from the p cap. Their PSR (0.88 and 0.90) would pass on its own: an edge measured over 17-20
years of ETF data can be distinguishable from zero and still be beaten by a fair share of shuffled histories. The
PSR cap is there for the other case, a lucky ranking among shuffles with an edge too small to tell from zero.

These caps were set by the owner with the four cards above in view. They are a reading rule for the board, not a
gate: they decide which rejected cards are worth free forward days, never a verdict. They apply to every card,
including those judged under thresholds v2.

The first daily report after this change lists `353323a3bd56` and `1335bb8e04b8` as moving from near-miss to
rejected. The rule changed, not the cards.

**Disclosure.** The rule comes from the M6 brief, but it was checked against the 18 existing cards before this record
was written. Decision 0007 and the handoff called both trend-filter cards in the paper book "near-misses". By this
rule only `c1c207ae352d` is one. `9f757395ca74` (trend_filter:all_etfs) also failed walk-forward (out-of-sample edge
−0.00). Its state is "in paper book", its verdict "rejected", and its tile has no near-miss badge. It stays in the
book: forward days are free, and that entry has been decided on since 2026-10-16.

### 3. The daily report reads what the lab recorded; it computes nothing new

`qlab.report.build` assembles one JSON document from:

- the ledger: trials since the last report, by origin and by research question, each with the N on its question
- the registry: new cards, state changes and counts
- the paper book's saved forward reports: results against their frozen bands and the CUSUM state
- the audit log: paper runs and refusals, the nightly job, calibration studies
- the data catalog: freshness (§4)

It runs no backtest and scores nothing, so building it adds no ledger trial. The nightly job scores the book just
before the report.

- **Each report covers the time since the previous one,** whichever way either was built (nightly job, CLI, web). The
  first covers everything since the lab began.
- **A plain answer first.** `headline` is a short list of sentences with a tone (ok, good, info, warn, bad). The
  sections behind it hold the numbers.
- **Storage:** JSON under `reports/daily/<id>.json`, plus a `daily_reports` row holding every card's state and the
  report's alert conditions, which the next report compares against. Building takes under a second on the real lab
  (18 cards, 1.8 million trials).

### 4. Freshness is lag behind the calendar, with tolerances for what the calendar doesn't model

The latest bar that should exist is the last session whose close is final at the time of the check
(`paper.sessions`): weekdays for US ETFs (final at 16:30 New York) and every day for Binance (closed at 00:00 UTC).
Hourly Binance bars are judged by whole days, since the archive publishes whole days.

A source is as fresh as its stalest default symbol. A missing default symbol, or nothing stored, counts as stale.

| Source | Tolerance | Why |
|---|---|---|
| US ETFs (Yahoo) | 1 session | exchange holidays aren't in the calendar, so a holiday looks like one missing session |
| Binance | 2 days | the archive publishes a day's file the next day, so one day of lag is normal |
| Ken French factors | 95 days | updated about monthly with data through an earlier month-end (48 days old on 2026-10-18) |

A single missed ETF day therefore doesn't raise a stale-data alert. It doesn't go unnoticed, though: the paper book
refuses to decide on stale data, which fails the nightly job, which alerts (§5).

### 5. Four alert rules, fired on changes

The rules:

1. **New candidate:** a card's verdict became candidate (it passed stages 1-9) or validated (it passed its lockbox).
2. **Paper-book alarm:** an active entry's CUSUM decay alarm is above its threshold. The threshold is set for one false
   alarm in ten years (decision 0007 §5).
3. **Stale data:** a source is beyond its tolerance.
4. **Failed nightly job:** a source failed, a decision was refused or failed, a step crashed, or the report failed.

**On changes.** Rules 1-3 alert in the first report that sees a condition, then stay quiet while it lasts. If the
condition clears and comes back, it alerts again. Rule 4 alerts every time, because each failed night is a new event.
The nightly job also sends the headline as a digest.

**Deliberately not an alert:** a forward edge outside its 90% band. By construction that happens about one day in ten
while the edge is exactly as researched, so alerting on it would teach the owner to ignore alerts. The report shows
the band status.

### 6. Channels: a local record always, macOS by default, Telegram only behind settings

- **file:** JSON lines in `<data>/alerts/alerts.jsonl`. Always on, and the record a quiet run (`--no-notify`) still
  leaves.
- **macos:** a Notification Center notification through `osascript`. The text is passed as arguments, never pasted
  into the script. On by default on macOS.
- **telegram:** the Bot API. Used only when `QLAB_TELEGRAM_TOKEN` and `QLAB_TELEGRAM_CHAT` are set, in the environment
  or in `<data>/lab.env`. The data folder is gitignored, and background jobs (launchd, the API server) read the file
  too, so the token never goes in a plist, a shell profile or the repository.

`QLAB_ALERT_CHANNELS` replaces the list; tests set it to `file`.

**Secrets.** The token is never logged, stored, returned by the API or put in an error message. httpx errors carry
the request URL, which contains the token, so the channel reports only the error type.

**Delivery never raises.** A failing channel is recorded on the alert, and the others still deliver.

**Nightly hook.** The report is the nightly job's last step. In API mode the server builds it (`POST /api/report`), so
the two processes never fight over DuckDB's single writer. If any earlier step crashes, the job still ends in a report
with a failure alert. If the report itself fails (a bug, or a server that went away), the failure alert goes straight
to the channels without the database. The job then exits 1, as it did before for a failed step.

## Out of scope, and why

- **The allocator** (HRP or risk parity with caps across validated strategies, a correlation heatmap, "what if I add
  X?"). ROADMAP §6 puts it in M6, but no card is validated or even a candidate, so an allocator would have nothing to
  allocate. It belongs with the first validated strategy, and its weights would be a new trial.
- **A two-way Telegram bot** (/status, /pause). Outbound messages only for now. Commands that change anything need the
  asymmetric-friction rules of ROADMAP §6b and belong with M7b's kill switch.
- **A missed nightly run.** A job that never ran can't report itself. The report says how long ago the last nightly
  job reported (a warning after 36 hours), but only an outside watcher could alert on silence.

## Consequences

- The owner gets one page (the Strategy Board) and one message a night that answer the daily question. The verdict,
  N and p-values sit beside every state, so the board can't make a lucky card look validated.
- Owner actions on the board go through existing paths with their audit trail: putting a card in the paper book
  (`POST /api/paper/entries`) and retiring one (with a required reason).
- The first real report listed four near-misses and zero candidates. With the caps (§2) two remain, one of them in
  the paper book. That is the honest state of the lab after 18 pre-registered candidates.
