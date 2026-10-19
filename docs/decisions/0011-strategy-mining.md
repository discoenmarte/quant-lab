# 0011. Strategy mining: a grammar compiled only by the lab, every try a trial, and calibration at mining scale

- Date: 2026-10-19
- Status: accepted

## Context

By M4 the lab judges published ideas with a calibrated gauntlet, tracks near-misses forward and paper-trades them.
Every pre-registered real candidate so far was rejected: realistic edges of Sharpe 0.2-0.4 are hard to tell from
luck. M5 adds the lab's own idea generator. It is also the riskiest source, for three reasons:

- **The weakest evidence.** Allen & Karjalainen (1999) found genetic-programming rules didn't beat buy-and-hold
  after costs out of sample. FactorBench (Wang & Ventre, 2026) benchmarked about 4,900 factors from nine miners (GP,
  RL, generative, LLM agents) in five markets. No paradigm dominated; the miners' high training IC mostly vanished
  by validation; neutralising beta, volatility, momentum, reversal and liquidity absorbed most of what was left.
- **Search inflates the best result.** Gençay (2026) recorded every evaluation of an LLM strategy-discovery agent
  and deflated its best by that count. The best in-sample Sharpe (1.69) rose more slowly than the deflation
  threshold, and every discovered strategy was rejected, while passive premia were certified.
- **Adaptive search is the hard case for the Deflated Sharpe.** Random trials spread like independent draws.
  Bayesian and evolutionary searches crowd their trials where earlier ones scored well, so the best of N adaptive
  trials can sit further in the tail than the best of N independent ones, while their spread, which sets the luck
  threshold, shrinks. Whether the gauntlet's threshold still holds is a measurement, not an assumption.

The owner asked that the design be checked against the state of the art and practitioner experience
(2026-10-19). The research notes are summarised at the end; each finding that changed the design is cited where
it applies.

## Decisions

### 1. A grammar the lab compiles; nothing generated runs

A candidate is a **spec**, a small JSON document:

| Component | Choices |
|---|---|
| Signal | one of the lab's rules with its parameters (5 single-instrument rules; 3 static and 4 cross-sectional portfolio rules), or a formula |
| Filter | none; trade only on the side of an N-day average (`trend`); trade only while recent volatility is at or below its one-year median (`calm`) |
| Exit (one instrument) | none; leave after N days (`time`); leave after a fall of x% from the best close (`trail`). After an exit the rule waits for its signal to reset |
| Session (one instrument, real opens only) | all day; overnight only (bought at the close, sold at the open); day only |
| Sizing | none; scale down to a volatility target, never above full size |
| Direction / hold | long or long-short (formulas on one instrument); share of members held and days between rebalances (formulas on a universe, long only) |

**Formulas** are trees of Kakushadze-style operators (2016): unary (`neg, abs, sign, slog`), binary
(`add, sub, mul, div`), time series over N past days (`delay, delta, ts_ret, ts_mean, ts_std, ts_min, ts_max,
ts_rank, ts_zscore, ts_sum`), `ts_corr`, and on universes cross-sectional (`cs_rank, cs_zscore, cs_demean`) over
that day's point-in-time members. Terminals are raw prices, volume, the daily return, the overnight gap, dollar
volume (crypto), the market's daily return (universes) and the calendar (day of week, turn of month, month). At most
12 nodes and depth 4.

- **Look-ahead is inexpressible, then checked anyway.** No operator moves data from later rows to earlier ones, and
  cross-sectional operators see only that day's members. Every trial also runs the future-perturbation check (two
  cuts; the gauntlet runs five on the candidate it judges). Gençay (2026) planted a leaky oracle with Sharpe 35 and
  it passed the Deflated Sharpe and PBO: deflation can't catch leakage, so leakage has to be impossible by
  construction. A trial that fails the check is recorded, flagged and never ranked.
- **Untrusted specs are validated, never executed.** `grammar.validate_spec` checks every key, operator, terminal,
  window and range against the scope's space and the owner's bans, and returns reasons. Compilation goes through
  `grammar.compile_spec` only.
- **Tunable parameters.** Up to four of a spec's numbers (signal windows first) become its parameters, with a range
  two steps either side of the mined value, so the gauntlet's grid, walk-forward and plateau stages vary them like
  any family's. The structure (the spec with its numbers replaced by names) hashes to the strategy name
  `mined_<hash>`; the numbers are its parameters. The ledger keys mined trials as it keys every trial.
- **Creativity has a ceiling, by design.** The miner finds combinations nobody wrote, for example "buy RSI(2) dips
  only above the 150-day average, leave after a 5% fall, hold only overnight". It can't invent new data or new
  kinds of trade; those came from widening the grammar (calendar terminals, sessions, the market return) and from
  the model's "outside the grammar" notes (§4).

### 2. Every try is a trial, budgeted per question

- **Mining questions.** All mining on a scope shares one research question, `mined:<scope>:1d` (family `mined`).
  Every evaluated spec is a ledger trial with source `mining`, so the Deflated Sharpe of any later candidate on that
  scope, mined or not, counts it (decision 0004 §3). Mining on SPY raises the bar for the next trend rule on SPY.
- **One lockbox per mining question.** Every mined candidate on a scope shares it, so it can't become a selection
  device (opening one lockbox per candidate would let 50 candidates each take one look).
- **Research data only.** The miner loads data up to the day before the lockbox (`lockbox.research_end`); on a
  universe it also drops pairs that are never members before then, so it can't see which coins exist later.
- **Counted twice from thresholds v5.** Calibration at mining scale (below) found adaptive search outrunning its count of
  trials; each mined trial counts twice in N.
- **Budgets.** Each mining question has a cap on its trials, 2,000 unless the owner sets another with a reason
  (`qlab mine budget`, audited). A job takes at most what is left, and stops on its own trial budget, its time
  limit or a stop request.
- **One writer.** Jobs run one at a time on the API server's background runner (the CLI sends them there when the
  server is up); evaluation spreads over worker processes that never touch the database.

### 3. Three search paradigms on one loop, compared on the same budget

- **Random**: independent draws from the grammar. The baseline.
- **Bayesian (Optuna TPE)**: the same decisions as define-by-run categorical parameters, so a formula tree's
  choices are conditional parameters. Optuna is a new dependency.
- **Genetic programming**: tournaments, subtree crossover and mutation of formulas, swaps and redraws of
  components, elitism, and a parsimony penalty of 0.005 edge Sharpe per node in selection only (complexity limits
  reduce overfitting; Becker & Seshadri, 2003; AlphaAgent, 2025).

All three draw their first candidates from one function (`grammar.sample_spec`), so they differ only in how they
search. The objective is the edge Sharpe after costs, the number the gauntlet judges; turnover is paid inside it
(an MCTS miner found turnover feedback cut IC slightly but improved after-cost results, 2505.11122). Repeats are
never evaluated twice.

### 4. An optional language-model proposer that must give a reason

`qlab mine propose` and the `llm` paradigm ask the local model (Ollama `qwen3.6:35b`, through the inbox's
providers and cost meter) for proposals in the grammar's JSON, each with an economic rationale of 40-1,500
characters: who pays the premium and why it survives costs.

- **It never sees a result.** It proposes hypotheses; it doesn't optimise the backtest.
- **Validated in the loop.** Invalid proposals go back once with the reasons (Gençay, 2026: 0 of 60 usable
  candidates from a JSON-emitting model without in-loop validation, 99 of 100 with it).
- **Ideas outside the grammar** (funding rates, on-chain data, order books in the first test) come back as notes
  for the owner and are never run.
- **The rationale is untrusted text.** In the first real call one rationale argued for buying high-volume coins
  while its spec held the lowest-volume ones. The lab doesn't check alignment (AlphaAgent scores it with another
  model call); it shows the plain-English rendering of the spec next to the rationale.
- Its trials count in N like any other. It isn't calibrated: model calls are slow and not reproducible enough to
  run thousands of times.

### 5. What a job reports

- **The race:** after each batch, the best edge so far beside the edge luck alone reaches among the N trials on the
  scope (the Deflated Sharpe's expected maximum, with the thresholds' spread method, `luck_max` in v3 and later) and
  roughly where the Deflated Sharpe reaches 0.95. Gençay (2026) calls it the evaporation curve.
- **Candidates**, de-duplicated by the correlation of their daily edges (above 0.9 is one idea), each with its
  Deflated Sharpe at the job's N, turnover, complexity and **originality**: its largest correlation with the lab's
  known rules on the same scope, which every job evaluates first at their published defaults. FactorBench found most
  mined predictiveness was known exposure in disguise; the edge measure already removes beta.
- **Coverage:** how often each kind of signal met each component, with the best edge per cell; operator and
  terminal counts; every trial's edge against the spread luck gives one trial.
- **The gauntlet hand-off:** `send_to_gauntlet` judges a candidate like any other card. Its question is the mining
  question; its N counts every trial on the scope.

### 6. Sessions: overnight and day-only holding

The engine can hold only one leg of each day (`run(..., session=...)`). An overnight position is decided at close
t-2 and bought at close t-1, because a market-on-close order goes in before the close is known; every day held is a
round trip and pays costs twice. The overnight premium in US stocks is large before costs (SPY $1 → $17 overnight
vs $1.20 by day over 30 years, Elm Wealth 2025) and mostly gone after them (QuantConnect; Alpha Architect 2020).

- The paper book and the brokers trade at the open only, so they refuse session rules until close orders exist.
- **Open question:** the permutation stage shuffles whole bars, keeping each day's overnight and daytime moves
  together. A rule that always holds overnight would earn the same overnight premium in every shuffled world, so the
  stage can't confirm a session premium even if it is real (a day shuffle can't see the size premium either,
  decision 0006 §6). A session null would deal overnight and daytime moves out separately. Not built.

## Calibration at mining scale (the exit criterion)

`qlab calibrate-mining` adds a **miner researcher** to calibration: on each synthetic market (the generators of
studies A-G: GARCH bars, eight-asset panels, top-30 crypto cross-sections), each paradigm searches the full grammar
for a fixed budget and submits its in-sample best to the unchanged gauntlet, deflated against every trial it made.
Paradigms share markets and budgets.

The decision rule was written before the study ran:

1. The exit criterion holds if every family's observed noise rate is at most 5%.
2. If it holds, no threshold changes.
3. If not, thresholds stay; a multiplier on the N of mined candidates (the smallest of 2, 5, 10, 20, 50 that brings
   every family to 5% or less) or a fix to the failing stage is measured by re-judging the same runs and confirmed on
   fresh seeds before adoption.

### Study M1 (report `60973742`, seeds 400,001+, thresholds v4, code `763a0fa`)

690 searches (390 on noise, 300 on planted edges), 771,376 calibration trials, 47 minutes on 14 processes. Budgets
1,000 trials per search (500 on crypto). Power is at the true edge of the market type's default rule.

| Family | Noise, as run | Noise, N ×2 | Power 0.5 / 1.0, as run | Power, N ×2 | Best in sample on noise (median) | Luck threshold (median) |
|---|---|---|---|---|---|---|
| gp/bars | 0/60 (0.0%) | 0/60 (0.0%) | 55% / 100% | 40% / 100% | +0.73 | 0.74 |
| random/bars | 0/60 (0.0%) | 0/60 (0.0%) | 20% / 100% | 15% / 100% | +0.55 | 0.73 |
| tpe/bars | 0/60 (0.0%) | 0/60 (0.0%) | 60% / 100% | 45% / 100% | +0.70 | 0.73 |
| gp/panel | 3/40 (7.5%) | 2/40 (5.0%) | 100% / 93% | 100% / 93% | +0.81 | 0.75 |
| random/panel | 0/40 (0.0%) | 0/40 (0.0%) | 93% / 100% | 87% / 100% | +0.67 | 0.75 |
| tpe/panel | 4/40 (10.0%) | 2/40 (5.0%) | 93% / 100% | 93% / 100% | +0.78 | 0.74 |
| gp/xs30 | 0/30 (0.0%) | 0/30 (0.0%) | 53% / 93% | 47% / 93% | +1.22 | 1.24 |
| random/xs30 | 0/30 (0.0%) | 0/30 (0.0%) | 33% / 80% | 20% / 80% | +0.94 | 1.27 |
| tpe/xs30 | 0/30 (0.0%) | 0/30 (0.0%) | 53% / 100% | 47% / 100% | +1.19 | 1.29 |

**Rule 1 failed:** Bayesian search on panels passed noise 10% of the time and genetic programming 7.5%.

**Before choosing a fix, what the seven noise winners were.** All seven sized positions to a volatility target, most
with the calm filter. Volatility timing earns a real edge where volatility clusters and the drift is positive (decision
0005), so they might have found something the noise market planted by accident. They hadn't: on four 150-year noise
panels their edges were +0.02 to +0.10, against +1.14 to +1.28 in the studies (`qlab mine truth`; the searches were
repeated deterministically and reached the same edges). The search found luck faster than its count of trials allowed.

**Rule 3:** counting each mined trial twice brought every family to 5% or less (both panel families 2 of 40); five
times, to 2.5% or less; ten times, to zero.

### Study M2 (report `c0ed8dbd`, seeds 500,001+, code `8999466`): confirmation

769,069 trials, 46 minutes. The two noise winners' true edges were +0.11 and +0.06.

| Family | Noise, as run | Noise, N ×2 | Power 0.5 / 1.0, as run | Power, N ×2 | Best in sample on noise (median) | Luck threshold (median) |
|---|---|---|---|---|---|---|
| gp/bars | 1/60 (1.7%) | 1/60 (1.7%) | 55% / 95% | 40% / 95% | +0.69 | 0.74 |
| random/bars | 0/60 (0.0%) | 0/60 (0.0%) | 25% / 95% | 25% / 95% | +0.53 | 0.73 |
| tpe/bars | 0/60 (0.0%) | 0/60 (0.0%) | 55% / 95% | 50% / 95% | +0.71 | 0.73 |
| gp/panel | 0/40 (0.0%) | 0/40 (0.0%) | 93% / 100% | 93% / 100% | +0.76 | 0.76 |
| random/panel | 0/40 (0.0%) | 0/40 (0.0%) | 87% / 100% | 80% / 100% | +0.64 | 0.75 |
| tpe/panel | 1/40 (2.5%) | 1/40 (2.5%) | 93% / 100% | 87% / 100% | +0.71 | 0.76 |
| gp/xs30 | 0/30 (0.0%) | 0/30 (0.0%) | 60% / 93% | 53% / 93% | +1.13 | 1.26 |
| random/xs30 | 0/30 (0.0%) | 0/30 (0.0%) | 33% / 87% | 27% / 87% | +0.96 | 1.26 |
| tpe/xs30 | 0/30 (0.0%) | 0/30 (0.0%) | 60% / 93% | 53% / 93% | +1.15 | 1.28 |

**Confirmed:** with each mined trial counted twice, every family stays at or below 5% (the worst 2.5%). M2 also passed
as run. Pooled over both studies, Bayesian search on panels passed noise 5 of 80 times as run (6.25%) and 3 of 80 with
N×2 (3.75%); genetic programming 3 of 80 and 2 of 80.

### Decision: thresholds v5 = v4 with `mining_n_factor: 2`

- Each mined trial counts twice in the Deflated Sharpe's N, for every candidate on its scope, mined or not, and in each
  job's luck line and estimates. The spread of the trial Sharpes still uses each trial once.
- Created on the real lab on 2026-10-19 (audit `gauntlet.thresholds`, actor `claude`). Fresh labs default to it;
  versions saved before the key count each trial once (`thresholds.LEGACY`). No card was re-judged.
- Random search alone never needed it (0 noise passes in 780 searches). The factor applies to every mined trial anyway:
  a scope's trials come from several jobs and paradigms, and one rule is simpler to audit.

### What the benchmark says about the paradigms (same markets, same budgets)

- **No paradigm dominates** (as FactorBench found). Adaptive search buys power with false positives.
- **Random** is the safest and the weakest: no noise pass, but the lowest power on weak edges (one instrument at 0.5:
  15-25% with N×2; crypto momentum at 0.49: 20-27%).
- **Bayesian and genetic search** reach higher in-sample bests on noise (medians 0.69-0.81 against 0.53-0.67 for
  random on one instrument and panels, 1.13-1.22 against 0.94-0.96 on crypto), catch weak edges more often (40-50%
  on one instrument at 0.5, 47-53% on crypto at 0.49), and caused every noise pass on panels.
- **Strong edges** (1.0) are found 80-100% of the time by every paradigm, with N×2. On planted markets the miners
  usually beat the default rule's edge in sample (median bests of 1.1-2.9), as a search should.
- **Speed:** random and GP take 10-20 s per 1,000-trial search on one instrument or a panel; TPE 50-80 s (Optuna's
  own overhead); a 500-trial crypto search about 2 minutes, half of it the gauntlet.

### The first real job (2026-10-19)

At the owner's request another session ran `qlab mine run all_etfs --paradigm gp --trials 500` under thresholds v4. With
1,909 trials already on the universe, luck reached an edge Sharpe of 0.67. The best find was a variant of the lab's
cross-sectional momentum rule (378-day lookback, six ETFs, monthly) traded only above the 150-day average: edge +0.69,
DSR 0.55, correlation 0.65 with the published rule. Nothing cleared the bar, and nothing went to the gauntlet.

## Limits, stated plainly

- **Walk-forward is weaker for mined cards.** The gauntlet re-chooses among the candidate's parameter settings each
  year, but the structure itself was chosen on the whole research period, so the out-of-sample years helped choose
  it. The honest walk-forward for mining re-runs the search before each year; that costs a search per fold and isn't
  built. The Deflated Sharpe, counting every trial, carries the search.
- **The permutation test doesn't know about the search.** It shuffles the history under one fixed rule; a rule fitted
  to the real history beats its shuffles easily (the first SPY job's best: p = 0.002, DSR 0.78).
- **Calibration covers three paradigms.** The language model isn't calibrated (§4).
- **Mining taxes the scope.** Every mined trial raises N for everything later tested on that scope.
- **Single instruments need very large edges.** With 20 years and N around 1,000, a mined edge needs roughly edge
  Sharpe 1.2 to clear the Deflated Sharpe. Breadth (universes) is where mining has power, as for every idea in this
  lab (decision 0005).

## What the research pass found (2026-10-19)

- Gençay (Aug 2026), *What survives honest evaluation?*, arXiv 2608.27734: structural guardrails over procedural
  promises; the evaporation curve; certifying moderate edges needs decades or breadth (t ≈ SR·√years).
- Wang & Ventre (Oct 2026), *FactorBench*, arXiv 2610.06947: no dominant paradigm; training IC doesn't survive;
  known exposures absorb most mined predictiveness; budgets weren't equalised in the benchmark itself.
- AlphaAgent (2025), arXiv 2502.16789: originality against known factors, hypothesis alignment, complexity limits.
- Practitioners (StrategyQuant forum, WorldQuant BRAIN guides): data the generator sees gets fitted; only data that
  never influenced selection counts; correlation is measured on daily PnL; diversity comes from new data or logic.
- Crypto 2025-26 (an SSRN replication on Binance perpetuals, arXiv 2608.21888, GitHub replications): cross-sectional
  momentum roughly equals its costs; reversal lives in the illiquid tail. Consistent with decision 0006's verdicts.

## Disclosure

- Development checks ran the grammar, the paradigms and the miner researcher on synthetic markets in scratch labs
  outside the real ledger: one full-budget run per family on seed 990,001 (timing), a tiny study at budget 50 on
  seeds 980,001+ (wiring) and the tests. Single runs say nothing about rates; none changed a setting.
- Two development jobs on real SPY research data ran in a scratch lab (500 mining trials, two gauntlet cards with
  300-setting grids). Their numbers appear above as examples. Looking is a choice, so their 1,100 trials were then
  recorded in the real ledger (audit `mining.import`): `mined:SPY:1d` has 500 of its 2,000 mining trials used, and
  every later candidate on SPY is deflated against all 1,100. The two cards themselves stay in the scratch lab.
- The language model was called once for four crypto proposals in a scratch lab; nothing was evaluated.
- The true-edge check of M1's winners ran after M1's results were seen, to understand them; the fix followed rule 3 as
  written, and the rule for M2 was recorded before M2 ran. To recover the winners' specs (M1 saved only their
  descriptions) the seven searches were repeated; the repeats reached identical edges and, being repeats of recorded
  trials, weren't recorded again. The long-market backtests were (`calib:60973742:truth`).

## Consequences

- The lab can search its grammar on any ingested instrument or universe, within budgets, and every try is counted.
- The Mining Lab page shows the race against luck, what the search covered and how original its finds are; the
  gauntlet stays the only judge.
- Follow-ups: a walk-forward that re-runs the search per fold; a session null; close orders so session rules can be
  paper-traded; more terminals from new data (funding rates, on-chain flows), which the model keeps suggesting.
  Nightly mining is a scheduled task that runs only while `mining_n_factor` is at least 2.
