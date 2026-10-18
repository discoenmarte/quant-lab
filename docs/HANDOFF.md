# Handoff

How to pick this project up and keep building it. Read this, then `README.md`, `ROADMAP.md` and
`docs/STYLE.md`.

- **Last updated:** 2026-10-18, after M3, M3b, M7a and M3c
- **Repository:** https://github.com/discoenmarte/quant-lab (public)
- **Owner:** discoenmarte, a computer scientist learning quant research

## 1. Working agreements (follow these exactly)

1. **Author is discoenmarte only.** Never add `Co-Authored-By` trailers or "Generated with ..." lines to
   commits, pull requests, docs or code. The repo-local git identity is already set
   (`discoenmarte <165704074+discoenmarte@users.noreply.github.com>`).
2. **One feature per commit**, Conventional Commits (`feat(data): ...`, `test(gauntlet): ...`), pushed
   **directly to `main`**. Tag each finished milestone (`m0`, `m1`, ...) and push the tag.
3. **Docstrings** follow `docs/STYLE.md`: every module, class and function has a signature block (summary,
   Args, Returns, Raises). Plain, precise, concrete. TypeScript exports get JSDoc in the same shape.
4. **Be critical.** The owner explicitly wants pushback on ideas that don't hold up, with evidence.
5. **Research rules:** every backtest goes through the ledger; signals use only data available at decision
   time; nothing places real-money orders.
6. Keep `README.md` (milestone table) and this file up to date at the end of each milestone.

## 2. Machine and tools

| Item | Value |
|---|---|
| Machine | Apple M5 Pro, 15 cores (5 performance + 10 efficiency), 48 GB RAM, macOS |
| Python | uv 0.13.1 manages Python 3.12 and `.venv` (`uv sync --group dev`) |
| Web | Node 26.12, npm 11; Vite 8, React 19, TypeScript 7, ECharts 6, Lightweight Charts 5 |
| GitHub | `gh` CLI logged in as `discoenmarte`; remote uses SSH (`git@github.com:discoenmarte/quant-lab.git`) |
| Local LLMs | Ollama with `qwen3.6:35b` (22 GB, default for M4), `gpt-oss:20b`, `qwen2.5:14b`, `llama3.2:3b`. Start with `ollama serve`. |
| Claude Code CLI | `~/.local/bin/claude`; usable headless (`claude -p ... --output-format json`) as an optional LLM provider |
| Paid services | None besides the owner's Claude subscription. Everything else must stay free. |

## 3. Repository map

```
src/qlab/config.py        paths (QLAB_DATA overrides the data folder; tests use it)
src/qlab/db.py            DuckDB connection + schema (trials, audit, sources, series, quality_issues,
                          gauntlet_cards, thresholds, lockbox_uses, paper_entries, paper_decisions, paper_scores)
src/qlab/ledger.py        record_trial(s) / count_trials / selection_set / list_trials / questions
src/qlab/lockbox.py       fixed sealed date, spent questions (from the ledger), clamp_end for Studio runs
src/qlab/audit.py         log / recent
src/qlab/timeutil.py      UTC helpers and clocks
src/qlab/glossary.py      plain-language definitions used by UI tooltips
src/qlab/backtest/metrics.py   Sharpe, Sortino, CAGR, drawdowns, Calmar, exposure, turnover, edge (appraisal ratio)
src/qlab/data/            sources (binance: archive listing, MD5-checked downloads, the USDT universe filter;
                          yahoo; french), ingest (incl. ingest_usdt_universe), store, quality, universe (manifest)
src/qlab/portfolio/       panel (static universes; dynamic ones with an availability mask and point-in-time
                          membership), engine (constant fractions, or drifting positions on dynamic panels),
                          strategies (incl. the cross-sectional crypto rules xs_*), leakage, runner, synthetic
src/qlab/gauntlet/        stats (PSR/DSR/FDR/MinTRL), pbo, nulls (permutations, bootstrap), thresholds,
                          subjects (bars or panel; day shuffles and the cross-sectional null for dynamic panels),
                          pipeline (ten stages, cards, lockbox), calibration (incl. synthetic crypto cross-sections)
src/qlab/paper/           forward paper book: sessions (bar open/close times), book (add, decide, retire), score
                          (replay through the engines), monitor (bands, CUSUM alarm), nightly (job, launchd)
src/qlab/api/main.py      FastAPI app; serves web/dist at /
src/qlab/api/routes_core.py    /api/health, /system, /overview, /audit, /glossary, POST /demo
src/qlab/api/routes_paper.py   /api/paper/book, /entries, /entries/{id}, /entries/{id}/retire, /run, /score
src/qlab/cli.py           qlab serve | init | ingest | backtest | portfolio | gauntlet | calibrate | paper ...
tests/                    pytest; conftest gives each test its own QLAB_DATA folder
web/src/App.tsx           shell: sidebar (nav.ts), top bar, routes, command palette
web/src/nav.ts            every page, its milestone and whether it's built (flip `ready` when done)
web/src/pages/            Home, DataHealth, BacktestStudio, PortfolioLab, Gauntlet, Ledger, TradingDesk, System,
                          Audit, Glossary, Placeholder
web/src/components/       Card, Stat, Badge, InfoTip, DataTable, EChart, Icon, Clocks, Toast, CommandPalette
.github/workflows/ci.yml  ruff + pytest, and web type-check + build, on every push
docs/decisions/           0001 Python first / Rust measured, 0002 REA scope, 0003 small stack, 0004 gauntlet,
                          0005 portfolios and forward evidence first, 0006 crypto cross-section, 0007 paper book
docs/preregistration/     batches written down and committed before they run
ops/launchd/              template for the nightly paper job (rendered by `make paper-launchd`, never installed)
```

Run: `make setup`, `make test`, `make serve` (http://127.0.0.1:8765). `make help` lists everything.

## 4. Status

| | Milestone | State |
|---|---|---|
| M0 | Foundations | **Done** (tag `m0`). Ledger, audit, metrics, Lab API, CLI, web shell with 4 pages, CI green. |
| M1 | Data lake | **Done** (tag `m1`). 26 series ingested on 2026-10-17, zero errors. Data Health page. |
| M2 | Backtest engine | **Done** (tag `m2`). 6 strategies, next-open engine with costs and cash, look-ahead check, Studio. |
| M3 | Validation gauntlet | **Done** (tag `m3`). Ten stages on edge returns, fixed lockbox, calibrated in three studies. |
| M3b | Portfolio lab | **Done** (tag `m3b`). Engine, six strategies, portfolio gauntlet and calibration, Portfolio Lab page. |
| M3c | Breadth: crypto cross-section | **Done** (tag `m3c`). 588 Binance USDT pairs incl. delisted, point-in-time universes, drifting positions, four crypto factor rules, cross-sectional null, study E, eight pre-registered verdicts (all rejected). |
| M7a | Forward paper book | **Done** (tag `m7a`). Decisions before outcomes, engine-exact scoring, frozen bands, CUSUM alarm, nightly job, Trading Desk. |
| M6, M7b, M4, M5 | Registry, broker paper trading, inbox, mining | In that order (decision 0005). |
| M8, M9 | Go-live gate, intraday | See ROADMAP §6, §6b and §7. |

## 5. What M1 and M2 delivered (facts worth knowing)

- **Data lake** (`qlab ingest --source all`, ~2 minutes with cached Binance zips): BTCUSDT and ETHUSDT 1d
  (3,348 bars) and 1h (80,221 bars) since 2017-08; 21 ETFs from Yahoo (SPY since 1993, 8,487 bars); Ken
  French FF3 + RF since 1926 (26,317 days). Warnings are real events: the 9/11 closure, the 1933 bank holiday,
  early Binance outages.
- **Source quirks handled:** Stooq now serves a JavaScript proof-of-work challenge, so it isn't used (do not
  try to bypass it). Binance spot timestamps switch from milliseconds to microseconds on 2025-01-01. Yahoo
  stamps daily bars at the session open; the lab keys them by session date at 00:00 UTC. FRED timed out.
- **Engine contract:** decision at bar t's close, fill at bar t+1's open; returns on dividend-adjusted
  prices; costs per side = commission + half-spread + slippage; idle cash earns Ken French RF (ETFs only).
- **Questions:** each run is a trial on `family:symbol:interval`, e.g. `trend_sma:SPY:1d`.
- **First results (with costs):** 200-day rule on SPY 1993-2026: CAGR 10.1% vs 10.8% buy-and-hold, Sharpe
  0.64 vs 0.65, max drawdown 19.7% vs 55.2%. TSMOM on BTC 2017-2026: 32% vs 38% CAGR, drawdown 63% vs 83%.
- **Speed (decision 0001):** 2.7 ms per daily backtest on SPY, 17 ms on 80k hourly BTC bars.
- **Incident:** `.gitignore` once ignored `src/qlab/data` (pattern `data/`); fixed to `/data/`. Ruff skips
  ignored files, which hid lint errors until then.

### What M3 delivered (2026-10-18)

- **Gauntlet** (`qlab gauntlet SPY --strategy trend_sma`, `qlab portfolio-gauntlet multi_asset --strategy tsmom`,
  Gauntlet page): ten stages on the *edge* beyond buy-and-hold, a grid over the family's parameter space (≤ 300
  settings, all recorded as trials), N counted across families on the instrument before the run, PSR/DSR with the
  autocorrelation-adjusted variance (López de Prado, Lipton & Zoonekynd 2026), false-discovery rate, PBO and MinTRL
  as diagnostics, volatility-preserving permutations, bootstrap sizing, regimes and crashes, diversification
  against validated cards, and the lockbox. About 3 s per single-instrument run and 3-6 s per portfolio run.
- **Lockbox:** fixed at 2024-10-01. Spent per question when any trial covers a sealed date. Already spent:
  `trend_sma:SPY:1d`, `trend_sma:TLT:1d`, `tsmom:BTCUSDT:1d` (M2 Studio runs).
- **Thresholds:** v1 = calibrated defaults (PBO reported, walk-forward needs a positive OOS edge, permutation
  p ≤ 0.05); **v2 (in force)** = permutation p ≤ 0.02 after study B showed trend rules at 7% false positives.
- **Calibration** (`qlab calibrate --seed N`, ~8 min on 12 cores, ~375k trials per study, all under `calib:`
  questions): study C on fresh seeds passes noise 1.4% (worst family 3%) and catches edge 1.0 / 0.5 95% / 52% of
  the time with 20 years. Full table in decision 0004.
- **Bugs found and fixed:** `load_bars` end date included the next day's bar (would have leaked one sealed bar);
  the Studio and card tables showed blank question columns; additive portfolio legs and a close-to-close portfolio
  benchmark created fake edges of ±0.7 for equal-weight copies.

## 6. Specs for the next milestones

### M3b: portfolio lab (done)

- `qlab.portfolio`: panels for six universes (from the first date every member exists), a weights engine with
  next-open fills, compounded legs, per-instrument costs and leverage financing, and equal weight through the same
  engine as the benchmark. Strategies: time-series momentum with volatility targeting, the ten-month trend
  filter, cross-sectional and dual momentum, risk parity, volatility management, equal weight.
- The gauntlet judges `PanelSubject`s the same way (day-level volatility-preserving shuffles across instruments).
- `qlab portfolio`, `qlab portfolio-gauntlet`, `qlab portfolio-batch` (pre-registered batches, deflated against
  the batch), Portfolio Lab page, portfolio option in the Gauntlet Console.
- **Calibration study D** (seeds 130,001+, thresholds v2, report `1782243a`): noise passes 1.0% pooled (CI
  0.6-1.7%, 1,240 runs), no family above 2.5%; portfolio trend with true edge 1.0 is caught 95% of the time with
  18 years, 40% with 10; edge 0.5, 40% with 18 years.
- **First real verdicts** (pre-registered batch `portfolio-batch`, published defaults, universes
  `asset_classes_long` and `all_etfs`): **all ten rejected.** Edge Sharpes 0.17-0.34 for the timing rules (risk
  parity negative on `all_etfs`), permutation p 0.05-0.5, DSR 0.6-0.9, walk-forward re-optimisation negative out of
  sample. Best near-miss: trend filter (edge 0.34, p 0.046 and 0.076, false-discovery chance 55-61%). The lockboxes
  of these ten questions are still sealed (the batch used research data only).
- **Disclosure:** during development the six strategies were run over the full history on `multi_asset`,
  `sectors`, `crypto` and `etfs_and_crypto` before being recorded; they were then recorded with the lockbox
  included, so those 24 questions are spent (audit event `lockbox.disclosure`).

### M3c: breadth, a survivorship-free crypto cross-section (done; decision 0006)

**Data.** `qlab ingest --source binance --universe usdt`:

- Lists the archive's S3 bucket (1,000 entries a page) and keeps the USDT pairs.
- Leaves out stablecoins and fiat, gold tokens, wrapped or staked copies, leveraged tokens and tokenized equities,
  with a reason for each in `curated/binance/usdt_universe.json`.
  - Leveraged tokens are recognised by their listed underlying, so `JUP` and `SYRUP` stay in.
  - Tokenized equities are `…B` pairs first listed from 2026-06.
- Downloads 1d monthly files plus the current and previous month's daily files, each checked against the listed
  MD5, over one keep-alive pool.

On 2026-10-18 the archive had 757 USDT pairs: 588 kept, 169 left out, zero ingest errors. Klines now keep
`quote_volume` (dollar volume).

**Panels.** `crypto_top30` and `crypto_top100`:

- Each day's universe is the top N by the past 30 days' average dollar volume, from data up to that day, with bars
  on all 30 days.
- An availability mask and NaN prices where a pair doesn't trade.
- Series are split at gaps and at one-day rises above 20× (token swaps after halts: COCOS, DREP, the new LUNA).
- Both start on 2019-02-14, the first day with 20 members, which leaves 5.6 research years.
- Pairs that are ever members: 374 (top 30) and 565 (top 100).

**Engine.**

- Dynamic panels hold drifting positions and trade only when targets change; membership is enforced then.
- A pair with no bar the next day is sold at its last close (optimistic).
- Costs are `crypto_xs`, 20 bps a side.
- The benchmark is equal weight of the members, rebalanced weekly.
- Static ETF panels keep the M3b engine.

**Rules.** The long legs of published crypto factors, `kind="dynamic"`:

- `xs_weekly_momentum`: LTW 2022, three weeks.
- `xs_reversal`: Zaremba et al. 2021, prior day.
- `xs_low_vol`: RETVOL, one week.
- `xs_small_size`: PRCVOL, one week of dollar volume.

Each holds the best or worst quintile of members in equal weights. The runner refuses rules on the wrong kind of
universe.

**Gauntlet.**

- **Look-ahead on dynamic panels** also changes future volume and delists coins after the cut, then recomputes
  membership. Cheats that read tomorrow's membership, volume or delistings are caught.
- **Permutation null:** each day's moves, as simple returns per unit of local volatility, are dealt out again
  among the previous close's members (and separately among the other pairs).
  - A day shuffle can't see a size premium (0% power).
  - Dealing log returns let low-volatility noise pass 40% of the time.
- **Calibration study E** (`qlab calibrate --xs-reps 40`, report `d4239d73`, seeds 170,001+, 800 runs, 105 min):
  - noise passes 0.16% (worst family 2.5%)
  - momentum: an edge of 1.0 is caught 85% of the time and 0.5 40% (5.6 years)
  - size: an edge of 1.0 is caught 5-10%, because the gates on absolute returns and the statistical stages block it
- Thresholds stay v2.

**First real verdicts** (pre-registered in `docs/preregistration/2026-10-18-m3c-crypto-factors.md`, commit
`ed1d334`, batch `23275b56`): **all eight rejected.**

- Reversal: wrong-signed even before costs.
- Size: negative edge.
- Momentum on the top 30: edge +0.64 after costs, but negative after walk-forward re-optimisation and fragile
  among its neighbours.
- **Near-miss: low volatility on the top 30.**
  - Edge +0.43; passed costs, walk-forward (+1.45 out of sample), plateau, permutation (p = 0.012) and regimes.
  - Failed significance: PSR 0.84 even undeflated; minimum track record 12.3 years.
- Every long-only crypto portfolio fell about 95% from the 2021 peak.
- Lockboxes are still sealed.
- **Disclosure:** the batch's Deflated Sharpe threshold was inflated by the reversal candidates' −4 edges (DSR 0.000
  on every card). No verdict depends on it (decision 0006).

**Follow-ups worth doing:**

1. Estimate the Deflated Sharpe's cross-trial variance robustly, decided on calibration evidence.
2. Decide whether the absolute-return gates (net CAGR > 0, regimes) fit long-only crypto (decision 0006, open
   question).
3. Put the low-volatility near-miss in the forward paper book. Crypto decisions are late with the daily archive
   (gotchas), so a live price feed comes first.

### M7a: forward paper book (done; decision 0007)

- `qlab paper add <card>...` puts gauntlet cards (any verdict, daily or portfolio) in the book and re-simulates the
  research period once to freeze what it expects: research beta, daily edge and log-return moments, and the card's
  own research band. `qlab paper run` records each active entry's targets for the latest final close with
  `decided_at` and `next_open`; `qlab paper score` replays the decisions through the engines and saves
  `reports/paper/<entry>.json`; `qlab paper list | retire | nightly | launchd`.
- **Refusals:** a backfill (a later final bar is stored) and stale data (the next session has closed but isn't
  stored). Late decisions (after the next open) are kept and flagged. Runs are idempotent.
- **Ledger:** paper trials go to `paper:<question>` with source `paper`: one reference trial when a card joins, one
  per scoring that sees new bars. They don't spend a lockbox or enter selection sets.
- **Power, measured:** for the trend-filter near-miss (edge Sharpe 0.33) the alarm threshold is 41.6 and it fires
  ~5.5 years after the edge vanishes on average; the forward band excludes zero only after ~21 years. The book
  catches breaks and large decay, not small ones (decision 0007 §5).
- **Replay check** (scratch lab, real bars 2026-08-28 to 2026-10-16 fed one day at a time): 34 forward days for both
  trend-filter cards and risk parity, every decision 11.5 to 59.5 hours before its open, all inside their bands.
- **Started on the real lab on 2026-10-18:** entries `922c0528e008` (trend_filter:all_etfs, card `9f757395ca74`)
  and `ded5af67de61` (trend_filter:asset_classes_long, card `c1c207ae352d`), both rejected near-misses. First
  decisions on the 2026-10-16 close, recorded 19.7 hours before the 2026-10-19 open (gross 71% and 43%).
  Keep it running: `make paper-nightly` after each US close, or `make paper-launchd` and follow the printed steps.


### M1: data lake (done; kept for reference)

Free sources (all reachable from the owner's network on 2026-10-17):

| Source | What | URL pattern | Notes |
|---|---|---|---|
| Binance public data | BTCUSDT, ETHUSDT klines, 1d and 1h | `https://data.binance.vision/data/spot/monthly/klines/<SYM>/<INT>/<SYM>-<INT>-<YYYY-MM>.zip` (+ `.CHECKSUM`) | Columns: open_time, open, high, low, close, volume, close_time, ... **Spot timestamps are microseconds from 2025-01-01, milliseconds before**: detect by magnitude. Current month only in `daily/` files. |
| Stooq | Daily OHLCV for US ETFs | `https://stooq.com/q/d/l/?s=spy.us&i=d` | CSV `Date,Open,High,Low,Close,Volume`. Check whether prices are adjusted. |
| Yahoo chart API | Raw close and adjusted close | `https://query1.finance.yahoo.com/v8/finance/chart/SPY?period1=..&period2=..&interval=1d&events=div,split` | Unofficial; use for raw-vs-adjusted and split/dividend markers. |
| Ken French | Daily factors including RF | `.../ken.french/ftp/F-F_Research_Data_Factors_daily_CSV.zip` | Percent units; header lines to skip. RF is the cash rate for backtests. |
| FRED | Macro | `fredgraph.csv?id=...` | **Timed out** from this network; optional. |

Universe: ETFs `SPY QQQ IWM EFA EEM TLT IEF GLD DBC VNQ HYG LQD XLK XLF XLE XLV XLY XLP XLI XLU XLB`; crypto
`BTCUSDT ETHUSDT`.

Build `qlab/data/` with `sources/<name>.py` (download → parse to the bar contract), `store.py` (Parquet write,
`series` catalog upsert, data hash), `quality.py` (duplicates, non-positive prices, OHLC consistency, extreme
returns, stale closes, gaps against the weekday calendar), `ingest.py` (orchestrates a source, updates
`sources`, writes `quality_issues`, logs audit). API: `GET /api/data/sources|series|coverage|issues`,
`GET /api/data/bars`, `POST /api/data/ingest`. CLI: `qlab ingest [--source X]`. Web: Data Health page with
a freshness board, coverage heatmap, candle inspector (Lightweight Charts) and an issues table. Tests use
small fixtures (no network).

Exit: a full rebuild from scratch works, and quality checks run on every ingest.

### M2: backtest engine and Backtest Studio (done; kept for reference)

- Strategy contract: `positions = strategy(bars, **params)`, values in [-1, 1], using data up to each bar.
- Engine: position decided at bar t is held from the **open of t+1**. Day return = `p_old × gap + p_new ×
  intraday − costs`. Costs per unit of position change: commission + half-spread + slippage (bps). Idle cash
  earns RF (Ken French).
- Library: buy-and-hold, trend SMA (the 200-day rule with entry/exit bands), SMA crossover, time-series
  momentum, Donchian breakout, RSI reversion.
- Every run records a ledger trial. Report JSON: stats, equity vs benchmark, drawdown, monthly returns, cost
  breakdown, trades.
- Leakage test: perturb data after a cut-off and assert positions before it don't change.
- Benchmark script for decision 0001: time a backtest on daily and on minute bars.
- Web: Backtest Studio (form, stats banner with N, synced charts, monthly heatmap, cost waterfall, trades,
  candles with trade markers).

### M3: validation gauntlet (done; kept for reference)

Stages in order (see ROADMAP §3): leakage, costs, walk-forward / out-of-sample, plateau (≥ 0.6), permutation
(p ≤ 0.01), stationary bootstrap (risk), Deflated Sharpe (≥ 0.95, N from the ledger) and PBO via CSCV
(≤ 0.2), regimes, diversification, lockbox (last 2 years, once per family).

Deflated Sharpe (Bailey & López de Prado, 2014), with per-period Sharpe `SR`, `T` observations, skew `g3`,
raw kurtosis `g4`, and `V` the variance of trial Sharpe ratios:
`SR0 = sqrt(V) × ((1 − γ) Φ⁻¹(1 − 1/N) + γ Φ⁻¹(1 − 1/(N e)))`, γ ≈ 0.5772;
`DSR = Φ((SR − SR0) √(T − 1) / √(1 − g3·SR + (g4 − 1)/4 · SR²))`.

Calibration: pure-noise strategies give the false-positive rate (target ≤ 5%); planted regime-switching
drifts give detection power versus data length. Web: Gauntlet Console and Ledger page.

### M4: research inbox

arXiv API (`cat:q-fin.TR OR q-fin.PM OR q-fin.ST OR q-fin.CP`, sorted by submission date) → triage with the
two-minute rubric (ROADMAP §12) via Ollama `qwen3.6:35b` in JSON mode → `StrategySpec` → human review queue →
code from templates → replication → post-publication test → gauntlet. Provider interface so the Claude CLI
can be swapped in.

### M5: strategy mining

Grammar of signals × filters × exits × sizing; random/Bayesian search (add `optuna`), genetic programming for
formulas; budgets per research question; every candidate is a ledger trial. Exit: noise calibration still
holds at mining scale.

## 7. Known gotchas

- The desktop app's preview tool may look for `.claude/launch.json` in an old folder. A config that works:
  `uv run --project <repo> qlab serve --port 8765`. `.claude/` is gitignored.
- DuckDB allows one writing process. Stop `qlab serve` before long CLI jobs, or trigger them through the API.
- `ROADMAP.md` was written before implementation; where it differs from decisions 0001-0005, the decisions win
  (no MLflow, no Dagster yet, Vite instead of Next.js, portfolios and a forward paper book before M4/M5).
- Every backtest, including quick sanity checks, goes through the ledger. A run that touches 2024-10-01 or later
  spends that question's lockbox. Use research-period runs (the default) for development.
- `n_perm` and `perm_max_p` are linked: with n shuffles the smallest p-value is 1/(n+1). Threshold updates
  that make the permutation stage impossible are refused.
- Calibration grows `data/lab.duckdb` by about 60 MB per full study.
- For UI work against a copy of the lab, `.claude/launch.json` has a `lab-scratch` entry that sets `QLAB_DATA`.
- Paper book: a missed nightly run can't be made up (backfills are refused). If the job ran on stale data it says
  "stale data: ingest first"; ingest and run again before the next session closes. Holidays aren't modelled, so a
  missed run right before a holiday is refused rather than accepted.
- The lockbox and the paper book's forward days overlap (decision 0007 §6). Before a card in the book opens its
  lockbox, cap the lockbox window at the entry's first decision.
- Crypto daily decisions are always late with the public archive (it publishes after the next candle opens).
- Crypto cross-section:
  - **Timings:** the full ingest (`qlab ingest --source binance --universe usdt`, about 33,000 files) takes about
    20 minutes on an empty cache and a minute or two when cached. Re-run it to pick up new listings and months.
  - **Tokenized equities:** the rule "ends in B, first file from 2026-06 on" will also catch any genuine coin named
    that way listed later; read the manifest's exclusions after each ingest.
  - **Speed:** a gauntlet run on `crypto_top100` takes a couple of minutes (565 columns, drifting-position loop),
    and calibration study E took 105 minutes for 800 runs.
  - **Rules by kind:** cross-sectional rules (`kind="dynamic"`) are refused on static ETF universes and static
    rules on the crypto universes (`runner.check_fit`).

## 8. References

- `ROADMAP.md` §1 critique, §2 prior art, §3 architecture and gauntlet, §5 data sources, §6 and §6b milestones
  and screens, §8 budget, §12 reading list.
- `docs/decisions/` for every architecture decision.
- Key papers: Bailey & López de Prado (2014) Deflated Sharpe; Bailey et al. (2017) PBO; Harvey, Liu & Zhu
  (2016); White (2000); Arnott, Harvey & Markowitz (2019) backtesting protocol; Vidovich & Zerjal (2026) six
  backtest-vs-live defects; Zheng (2026) admission gates; Wang & Ventre (2026) FactorBench.
