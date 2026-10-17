# Quant Research Lab: Roadmap

*Drafted 2026-10-17, including a frontier scan of Aug–Oct 2026 papers. A learning-first, evidence-first lab for discovering, testing and paper-trading
systematic strategies. Prices and tool facts were checked in October 2026; re-check before paying for anything.*

---

## 0. What this lab is, and what it isn't

**Goal:** a machine that turns trading ideas (from papers, from your own hypotheses, from automated
search) into honest verdicts, keeps every result, paper-trades the few survivors, and lets you see
everything through dashboards.

**Non-goals:** guaranteed profit, get-rich-quick, AI that trades on its own, meme-coin sniping.

**Honest expectations:**
- Most ideas will be rejected. That's the lab working, not failing.
- If it finds anything, expect a few weak, uncorrelated edges worth a few % a year above the market,
  scaling with the capital you put in.
- The guaranteed returns are skills, a strong portfolio project, and protection from bad strategies.

---

## 1. Critical review of the original idea

| Original idea | Keep / change | Why |
|---|---|---|
| Mine strategies exhaustively, all day | **Change: budgeted, hypothesis-driven search** | Compute is free on your Mac, but every extra trial raises the bar a result must clear. In an Aug 2026 study, the honest threshold climbed faster than the best result as trials grew, and every LLM-discovered strategy was rejected. Fewer, better-motivated hypotheses are statistically stronger *and* cheaper. |
| Alert the moment a strategy is found | **Keep, but alert means "start paper trading", not "go live"** | A freshly mined winner is most likely luck. Paper trading costs nothing and catches flukes. Live trading needs time-based evidence. |
| Constantly test the latest papers | **Keep, with replication first and a post-publication test** | Many published anomalies fail to replicate (64% of 447 were insignificant once microcaps were handled). Published returns tend to shrink after publication. The real test is the period *after* the paper came out. |
| Let AI create strategies | **Change: AI reads, codes, proposes and reports; the gauntlet decides** | 2025–2026 studies found LLM strategies lose their edge over long, broad tests and show no directional edge in production. AI is great at the research labour, not as the source of edge. |
| Use Monte Carlo and permutations | **Keep, and know what each proves** | Permutation tests ask "does this beat random timing?" (evidence of edge). Bootstrap Monte Carlo estimates drawdown risk (used for sizing, not proof). |
| Track alpha correlation | **Keep** | Add a strategy only if it diversifies the current portfolio (low correlation, positive marginal contribution). |
| Go live when confident | **Keep, but define "confident" before you start** | Realistic sample sizes give modest confidence at best. Size by uncertainty, start tiny, and enforce kill rules outside any model. |
| Do everything at once | **Change: one asset class first** | US index/sector ETFs (daily) plus BTC/ETH (daily/hourly). Expand only after the gauntlet is proven. |
| Statistics will catch cheating | **Change: make look-ahead impossible by construction** | A deliberately leaky strategy with Sharpe 35 passed Deflated Sharpe and PBO tests in the Aug 2026 study. Leakage must be prevented structurally (feature registry, timestamp cutoffs), not detected afterwards. |
| Copy top traders in real time | **Change: test "follow the informed" signals; don't copy with money** | Leaderboards show the lucky tail of thousands of traders (the same multiple-testing trap as mining). Copiers fill later and at worse prices, leaders are paid to take risk, and in experiments the option to copy caused excessive risk-taking. Legally disclosed insider purchases are the best-documented version. |
| Find a new strategy each morning, trade it that day, reinforce on the day's P&L | **Change: pre-validated intraday library + a validated morning selector + slow learning** | A morning is far too little data to tell skill from luck, mornings behave differently from afternoons, and one day's P&L is mostly noise (even an elite Sharpe-2 strategy is up on only ~55% of days). Daily reinforcement would chase noise. See M9. |

---

## 2. Prior art: academic work doing something like this

| Work | What it does | What it teaches us |
|---|---|---|
| **R&D-Agent(Q)**, Microsoft Research & CMU, NeurIPS 2025 | Multi-agent LLM loop: hypotheses → code → backtest → feedback, built on Qlib | The leading open-source automated factor/model R&D pipeline. Claims up to 2× returns vs classic factor libraries, but these are the authors' own backtests. |
| **Alpha-GPT**, EMNLP 2025 demo | Human-AI interactive alpha mining | Placed top-10 of 41,000+ teams in WorldQuant's 2024 championship. Human-in-the-loop works best. |
| **AlphaAgent**, 2025 | LLM alpha mining with regularised exploration | Argues GP/RL miners chase historical metrics and decay. Economic rationale matters. |
| **AlphaGen**, KDD 2023 | Reinforcement learning for formula ("formulaic") alpha collections | Search for *sets* of alphas that work together, not single winners. |
| **What survives honest evaluation?**, Aug 2026 | Leakage-safe, search-aware evaluation of an LLM strategy-discovery agent | Rejected every LLM-discovered strategy and certified only passive benchmarks. Our gauntlet design follows its principles. |
| **FINSABER**, KDD 2026 | 20-year, 100+ symbol test of LLM timing strategies | Reported LLM advantages deteriorate. Too timid in bull markets, too aggressive in bear markets. |
| **Agentic Trading survey**, 2026 | Review of 185 studies (80 closed-loop systems) | Common pitfalls: split schemes, unrealistic costs, survivorship bias, execution fidelity. |
| **From Knowing to Doing**, May 2026 | Memory-controlled benchmark of 10 LLM agents | Returns were mostly market and style exposure, not skill. |
| **LLM agents in production**, Sep 2026 | 3,505 real-money AI agent vaults plus a perps fleet over 6 months | No directional edge. Useful realism check. |
| **SoK: Trading agents or market crashers?**, Sep–Oct 2026 | Security and robustness audit of 15 academic schemes | None enforced stop-losses outside the LLM. Our risk layer must. |
| **Replicating Anomalies** (Hou, Xue, Zhang) / **Is There a Replication Crisis?** (Jensen, Kelly, Pedersen) | Large replication studies | Pessimistic vs more optimistic replication rates. Test anomalies yourself and expect shrinkage. |
| **Deflated Sharpe, PBO/CSCV** (Bailey & López de Prado); **Reality Check** (White); **SPA** (Hansen); **t > 3** (Harvey, Liu, Zhu) | Multiple-testing statistics | The maths behind "the bar must rise with the number of trials". |
| **Open Source Asset Pricing** (Chen & Zimmermann); **101 Formulaic Alphas** (Kakushadze) | Public libraries of published signals with code | Free seed ideas for Pipeline A and templates for Pipeline B. |
| **WorldQuant BRAIN** | Free alpha-mining platform; some users become paid consultants | The industry version of Pipeline B. Good practice, and possible pay. |

---

## 3. Architecture

```
  arXiv / SSRN / OpenAlex           Your hypotheses, templates, grammar
          |                                        |
          v                                        v
  +-----------------------+            +--------------------------+
  | PIPELINE A            |            | PIPELINE B               |
  | papers: triage ->     |            | mining: grammar search,  |
  | spec -> code ->       |            | GP formulas, LLM         |
  | replicate -> post-pub |            | proposer (with rationale)|
  +-----------+-----------+            +------------+-------------+
              |                                     |
              +------------------+------------------+
                                 v
                 +-------------------------------+        +----------------------+
                 | VALIDATION GAUNTLET           |<------>| TRIAL LEDGER         |
                 | (cheap -> expensive, fail     |        | every test, every N, |
                 |  fast, N-aware, leakage-safe) |        | params, data hash    |
                 +---------------+---------------+        +----------------------+
                                 | pass
                                 v
                 +-------------------------------+
                 | CANDIDATE REGISTRY            |---> ALERT (Telegram / Discord / email)
                 | + correlation / marginal value|
                 +---------------+---------------+
                                 v
                 +-------------------------------+
                 | PAPER TRADING (3-6 months)    |  live-vs-backtest band, slippage,
                 | incubation + decay detection  |  reconciliation, calibration
                 +---------------+---------------+
                                 | you approve (human gate)
                                 v
                 +-------------------------------+
                 | TINY LIVE + HARD LIMITS       |  kill switch, loss limits, position caps
                 | enforced outside any model    |  (never AI-controlled)
                 +-------------------------------+

  Underneath everything:  DATA LAKE (Parquet + DuckDB, point-in-time, versioned)
                          FEATURE REGISTRY (only features that can't see the future)
  Around everything:      LAB UI + TELEGRAM (see 6b), Dagster, MLflow, Optuna Dashboard, alerts
```

### The gauntlet, in order (each stage can reject)

1. **Leakage and sanity checks:** features come only from the registry; timestamp audit; "shift the future and nothing in the past changes" test.
2. **Costs:** net of commission, spread, slippage and funding. Reject if net return ≤ 0.
3. **Out-of-sample:** walk-forward plus combinatorial purged cross-validation (CPCV).
4. **Plateau:** neighbouring parameters must also pass (plateau ratio ≥ 0.6, as in the Rust paper).
5. **Permutation test:** strategy vs shuffled-timing null distribution (Masters-style).
6. **Block-bootstrap Monte Carlo:** drawdown and risk distribution, used for sizing.
7. **N-aware significance:** Deflated Sharpe using the ledger's trial count, plus PBO via CSCV.
8. **Regimes and stress:** bull, bear, high-vol and crisis replays.
9. **Diversification:** correlation and marginal Sharpe vs the current portfolio.
10. **Lockbox holdout:** a final data slice used once per strategy family, then retired.

Starting thresholds (to be calibrated in M3): DSR ≥ 0.95 · PBO ≤ 0.2 · permutation p ≤ 0.01 after correction ·
plateau ≥ 0.6 · positive net in most regimes · correlation to portfolio ≤ 0.5.

---

## 3b. Operating modes and schedule

"Not all day" means no unlimited brute-force search. The lab still works every day, on a schedule with
a fixed research budget, and everything is logged.

| When | What runs | Mode |
|---|---|---|
| Every night (after the US close; a fixed UTC time for crypto) | Ingest data → update features → reconcile paper P&L → budgeted mining batch on the Mac → gauntlet → daily report and alerts | Research + paper |
| Market hours | Only strategies that already passed the gauntlet execute (paper first). Monitoring and kill switches. **No research during the session.** | Paper / live |
| Weekly (weekend) | Frontier paper scan (Pipeline A), ledger review, threshold recalibration, incubation review | Research |
| Monthly | Promote/retire decisions, portfolio rebalance, cost model updated from real slippage | Governance |
| On demand | **Replay mode:** re-run a past day minute by minute to debug the system or practise manual trading | Replay |

---

## 4. Tech stack (built around your Mac: Apple M5 Pro, 15 cores, 48 GB RAM)

| Layer | Choice | Notes |
|---|---|---|
| Language and env | Python 3.13 + `uv`; Rust later if needed | NautilusTrader gives a Rust core without writing Rust |
| Storage | Parquet files + DuckDB | Fast, free, local; versioned snapshots |
| Orchestration and data observability | **Dagster** | Asset lineage, freshness, run history in a UI |
| Experiment tracking / trial ledger | **MLflow** + DuckDB ledger table | Every backtest is a run: params, metrics, data hash, N |
| Data quality | Pandera | Schema and range checks on every load |
| Research backtests | vectorbt or a small NumPy engine of our own | Thousands of variants per minute on your Mac |
| Event-driven / paper / live | **NautilusTrader** (Rust core, same code for backtest and live) | QuantDinger as reference or alternative |
| ML / factor research | scikit-learn, LightGBM; Qlib + RD-Agent to study | Purged CV only |
| Portfolio | skfolio or Riskfolio-Lib (HRP, risk parity) | Caps per strategy |
| Statistics | Own implementations (tested), cross-checked with pypbo | Libraries are young; tests matter |
| LLMs | Claude Haiku 5.5 (triage), Sonnet 5.5 or Opus 5.5 (deep read, code); optional local open model | Batch API halves cost |
| Interface | FastAPI "Lab API" + Next.js (React, TypeScript) web app; TradingView Lightweight Charts (candles, trade markers); Apache ECharts (heatmaps, fans, funnels); FINOS Perspective (live grids); Telegram bot on the same API | All free and open source. Lightweight Charts needs a TradingView attribution link. Embeds Dagster, MLflow and Optuna Dashboard instead of rebuilding them |
| Alerts | Telegram bot / Discord webhook / email | |
| Brokers (paper) | Alpaca paper (US stocks/ETFs), exchange testnets (crypto) | Free |

---

## 5. Data sources (start free, upgrade only when research needs it)

| Need | Free | Paid upgrade (approx., verify) |
|---|---|---|
| US ETFs / stocks, daily | Alpaca Basic (IEX feed, bars back to 2016, 200 calls/min), Tiingo, Stooq | Norgate (survivorship-bias-free, ~$35–55/mo); Alpaca full feed $99/mo |
| US intraday | Alpaca Basic (IEX only, ~2.5% of volume) | Massive (formerly Polygon.io), Starter ~$29/mo; Databento (usage-priced) |
| Crypto OHLCV and trades | **Binance public data** (data.binance.vision, daily/monthly files, checksums); CCXT | Tardis.dev tick/order book (~$350+/mo; skip) |
| Fundamentals | SEC EDGAR (XBRL) | Sharadar / FMP |
| Macro | FRED | – |
| Factors and published signals | Ken French Data Library; Open Source Asset Pricing; AQR datasets | – |
| News / attention | GDELT; SEC filings | – |
| Insider trades and fund holdings | SEC EDGAR Form 4 and 13F (free) | – |
| On-chain wallets | Polymarket and Hyperliquid public APIs; Dune | wallet-analytics services (optional) |
| Research papers | arXiv API (q-fin), SSRN, OpenAlex, Semantic Scholar | Quantpedia Premium (optional) |

**Watch-outs:** free stock data usually lacks delisted companies (survivorship bias). Stick to ETFs and
crypto until you pay for bias-free stock data.

---

## 6. Milestones

Part-time pace (~10–15 h/week). Each milestone ships something you can see.

### M0: Foundations (≈2 weeks)
- Repo, `uv` env, tests and CI, Dagster + MLflow + DuckDB skeleton, Lab API and web app shell (see 6b).
- Learning: investing basics, Sharpe and drawdown, why backtests overfit.
- **Exit:** `make demo` runs a toy asset pipeline end-to-end and shows it in the UI.

### M1: Data lake v1 (≈3 weeks)
- Ingest: US ETFs daily, BTC/ETH daily and 1h (Binance), FRED macro, Ken French factors.
- Pandera checks, split/dividend handling, point-in-time timestamps, versioned snapshots.
- **Observability:** data-health page (freshness, gaps, outliers, coverage heatmap).
- **Exit:** full rebuild from scratch is reproducible; daily refresh job is green for 7 days.

### M2: Backtest engine + cost model (≈3–4 weeks)
- Vectorised engine; fees, spread, slippage, funding; standard metrics; QuantStats tearsheets.
- Feature registry: features declare their lookback and availability time.
- **Six-defects checklist** (Vidovich & Zerjal, Oct 2026): one code path for backtest and live;
  unadjusted prices for fill simulation; timezone tests; broker limits enforced inside the backtest;
  config-propagation tests; data-feed coverage checks (the free IEX feed is a small slice of volume).
- **Exit:** reproduces the 200-day rule within tolerance; "future-shift" leakage tests pass; random
  strategies lose roughly their costs.

### M3: Validation gauntlet (≈4–6 weeks). The heart of the lab.
- All ten stages above, plus the trial ledger and the lockbox.
- **Calibration on synthetic data:** pure-noise strategies give the false-positive rate (target ≤ 5%);
  planted edges (Sharpe 0.5 / 1 / 2) give detection power vs data length.
- Compare every gate against a **random-admission control** at the same adoption rate, and compute
  criteria on **excess** returns, not absolute (Zheng, Oct 2026).
- **Observability:** gauntlet funnel, null-distribution histograms, plateau heatmaps, Monte Carlo fan
  charts, DSR/PBO panels, FPR and power curves.
- **Exit:** a calibration report saying "with X years of data, we detect a Sharpe-1 edge Y% of the
  time and pass noise Z% of the time".

### M4: Pipeline A, research-paper ingestion (≈4–6 weeks)
- Daily feed from arXiv q-fin and others → cheap triage on abstracts → deep read of the top few →
  structured `StrategySpec` (JSON: universe, data, signal, rebalance, costs, claimed results, pub date).
- Human review queue → code from templates → **replication** (in-sample vs claim) → **post-publication
  test** → gauntlet.
- **Observability:** paper inbox, spec viewer, "claimed vs replicated vs post-pub" chart, LLM cost meter.
- **Weekly frontier scan:** arXiv q-fin recent listings plus keyword queries, scored with the
  two-minute rubric in section 12.
- **Exit:** 3 known papers replicated within tolerance; weekly research digest.

### M5: Pipeline B, strategy mining (≈6–8 weeks)
- Strategy grammar (signals × filters × exits × sizing) → random/Bayesian search (Optuna) → genetic
  programming for formulas → LLM proposer that must state an economic rationale.
- Search budgets per research question; every trial goes to the ledger.
- Implement at least two mining paradigms (start with GP) and benchmark them. FactorBench found no
  paradigm consistently dominates.
- **Observability:** trials over time, best-so-far vs N-aware threshold curve, search-space coverage map.
- **Exit:** nightly mining on your Mac; noise calibration still holds at mining scale.

### M5b (optional): Pipeline C, "follow the informed" signals (≈3–4 weeks)
Copy trading is tested here as a hypothesis, never done with real money.
- **C1 Corporate insiders** (SEC Form 4, free on EDGAR). The best-documented version: in published
  studies, opportunistic (non-routine) insider purchases have predicted returns. Test the
  post-publication period.
- **C2 Big funds** (13F holdings, quarterly, filed up to 45 days after quarter-end). Weak but testable.
- **C3 On-chain "smart wallets"** (Polymarket, Hyperliquid). Forward-only paper tests. Choose leaders
  using only data available at the time, count every wallet you considered (the same multiple-testing
  trap as mining), and measure copy delay and slippage.
- **Skip:** copying congressional trades (a major study found members' portfolios mediocre) and paid
  copy-trading or signal groups.
- **Legal note:** never trade a company's peers using confidential information from your job. US courts
  have treated that kind of "shadow trading" as insider trading (SEC v. Panuwat, 2024).
- **Exit:** a verdict per source, net of delay and costs, from the same gauntlet.

### M6: Registry, portfolio and alerts (≈3–4 weeks)
- Candidate lifecycle: candidate → incubating → live → retired. Correlation and marginal-value check.
  HRP / risk-parity portfolio with caps.
- Alerts when a candidate passes; daily research report.
- **Observability:** registry board, correlation matrix, risk contributions, alert log.
- **Exit:** idea → gauntlet → alert works end-to-end without manual steps.

### M7: Paper trading and monitoring (build ≈3–4 weeks, then run ≥3–6 months per candidate)
- NautilusTrader (or simple adapters) to Alpaca paper and crypto testnets; reconciliation; decay
  detection (CUSUM, live-vs-backtest band); calibration plots for probability signals (Jev goes here).
- Pick probability thresholds (e.g. Jev's) with Learn-Then-Test risk control, not a fixed constant
  like the Rust paper's 0.70.
- **Process telemetry:** log internal state (active factors, holdings vs fallback), not just returns
  (FORESIGHT-9).
- **Observability:** live-vs-backtest band, slippage vs model, fills, Brier/reliability plots, decay alarms.
- **Exit:** incubation report per candidate.

### M8: Go-live gate (a decision, not code)
- See checklist below. Start tiny. Review monthly. Retire without regret.

### M9 (optional, after M7 is stable): Intraday mode
Your "day-trading mode", in the version that can survive testing:
- **Library:** intraday strategies validated on *years* of minute data, e.g. opening-range breakout and
  market intraday momentum (Gao, Han, Li & Zhou 2018: the first half-hour's return predicted the last
  half-hour's). Test the period after publication too.
- **Morning routine:** compute pre-open and opening features (overnight gap, pre-market volume, volatility
  regime, scheduled events). A **selector**, itself validated on history like any strategy, decides which
  library strategies run today. The morning *computes*; it doesn't *search*.
- **End of day:** reconcile fills (Six Defects checklist), log slippage and process telemetry. That's the
  daily learning that helps: execution, data and bugs.
- **Slow learning:** update each strategy's weight over weeks to months (Bayesian or bandit updates with
  strong priors and caps). Never refit on a single day.
- **Reinforcement learning:** fine for experiments in replay and simulation (e.g. FinRL). A year gives only
  ~250 daily episodes, so RL overfits easily. No real money.
- **Data:** consolidated minute data is required (≈ $29–99/month). The free IEX feed is too thin.
- **Exit:** the selector-plus-library system passes the gauntlet on held-out years, then ≥ 3 months of
  paper trading.

**Timeline:** M0–M3 ≈ 3 months · M4–M6 ≈ 3–4 months · incubation 3–6 months → earliest small live
allocation ≈ 9–12 months from start.

---

## 6b. Interface plan: something real to click at every milestone

### Design principles, borrowed from the best tools
- **Backtest report layout (QuantConnect):** a stats banner on top, then synchronized charts (equity vs
  benchmark, drawdown, exposure, turnover, capacity) that share one zoom. Orders expand into fills, and the
  report downloads.
- **Trades on the chart (Jesse):** candles, indicators, orders and completed trades in one interactive chart.
  The *same* chart component serves backtest, paper and live.
- **Bot control (FreqUI):** start/stop and force exit, a multi-strategy overview, a balance-over-time chart,
  and a timezone setting.
- **One backend, two front doors (Hummingbot's Condor):** the web app and a Telegram bot share one API.
  "Routines" (small deterministic scripts) run from either. Alerts fire on state changes and risk-limit
  breaches. Remote login uses short-lived links.
- **Search visuals (Optuna Dashboard):** embedded rather than rebuilt.
- **Trading-UI conventions:** dark by default; dense but modular grid; colour means state (green/red only for
  P&L direction, amber for warnings, a red banner whenever real money is involved); no more than ~5 live regions
  per screen; every number shows its time in both exchange time and UTC.
- **Learn as you go:** every metric has an info icon with a one-line definition and a small picture. There's
  also a Glossary page.
- **Honesty by design:** never show an equity curve without its trial count (N), deflated Sharpe and costs
  beside it. Pretty charts make lucky strategies look convincing, so the UI has to make luck visible.

### Control model: running everything from the interface
- Every button calls the Lab API, and every action lands in the audit log (who, what, when, why).
- **Asymmetric friction:** reducing risk is instant (pause, kill switch, flatten, tighten a limit). Increasing
  risk needs a typed confirmation, a written reason, and a 24-hour cooling-off period to loosen a limit or
  switch paper → live. AI can never trigger an action that increases risk.
- **Command palette (⌘K):** run anything by typing ("gauntlet spy-trend", "pause mining", "open desk").
- **Telegram:** /status, /pnl, /alerts, /report, /pause <strategy>, and /kill (with confirmation). Nothing
  that increases risk works from the phone.
- **Routines folder:** drop in a script and it appears as a button in the UI and as a Telegram command.

### Screens by milestone
| Milestone | What you can open | What you can control |
|---|---|---|
| M0 | **Lab Shell:** home status cards, system health, settings (timezones), audit log, command palette | Run the demo pipeline and watch it complete |
| M1 | **Data Health:** freshness board per source; coverage heatmap (symbol × month); candle inspector with raw vs adjusted prices and split/dividend markers; data-issues grid; link to Dagster lineage | Re-ingest a source, backfill a date range, quarantine a bad series |
| M2 | **Backtest Studio:** stats banner; synced equity-vs-benchmark, underwater drawdown, exposure and turnover charts; monthly-returns heatmap; cost waterfall (fees / spread / slippage / funding); trades table → fills → trade on the candle chart; compare 2–4 runs | Run, cancel, duplicate, compare, export a report |
| M3 | **Gauntlet Console:** funnel of candidates through the 10 stages; per-strategy report card with 10 badges; permutation histogram (your Sharpe vs noise), plateau heatmap, Monte Carlo fan chart, DSR/PBO gauges, regime bars; calibration page (false-positive and power curves); ledger explorer (millions of rows) | Edit thresholds (versioned, reason required, future runs only); open the lockbox (typed confirmation, once per strategy family); re-run a stage |
| M4 | **Research Inbox:** paper feed with 7-dot rubric scores; triage queue; 3-pane spec review (paper PDF · editable spec · generated-code diff); claimed vs replicated vs post-publication chart; AI cost meter vs budget; weekly digest | Approve or reject (with a reason), generate code, queue for the gauntlet, set the monthly AI budget |
| M5 | **Mining Lab:** jobs board (trials, CPU, AI spend vs budget); **best-so-far vs rising-threshold** chart; embedded Optuna Dashboard; formula-tree viewer with a plain-English rendering; AI hypothesis cards showing their economic rationale | Start/pause/stop jobs, set budgets per question, ban a feature family, send a candidate to the gauntlet |
| M5b | **Signals Watch:** insider (Form 4) timeline; fund-holdings (13F) changes; wallet leaderboard with a point-in-time selection audit; copy-delay and slippage chart | Add or remove sources; start a forward paper test |
| M6 | **Strategy Board:** kanban (Candidate → Incubating → Live → Retired). **Portfolio:** allocation treemap, risk contributions, correlation heatmap, "what if I add X?". **Alert Center.** Morning report | Promote to paper, retire (with a reason), adjust allocations within caps, configure and test alerts |
| M7 | **Trading Desk (paper):** top bar with mode, today's P&L, risk-budget gauges, data-freshness lights and an always-visible **KILL** button; streaming positions/orders blotter; per-strategy live-vs-backtest band, slippage scatter and decay alarm; reconciliation panel; Jev calibration (reliability diagram, Brier score); process telemetry | Start/stop a strategy, pause new orders, flatten one or all, kill switch; the same from Telegram |
| M8 | **Go-Live Gate:** a checklist that turns green only when the evidence exists (links to the report card, paper report and limits); live P&L vs risk budget; red "real money" banner | Paper → live switch (all items green + typed name + reason + 24 h cooling-off); limits editor (tighten instantly, loosen after 24 h) |
| M9 | **Intraday Cockpit:** pre-market briefing (overnight gap, pre-market volume, volatility regime, events calendar); selector decision card ("today: run X, skip Y, because..."); live chart with the opening-range box, signals and orders; end-of-day review; **Replay player** (pick a past day, play at 1–60×, step bar by bar, practise with paper orders) | Approve or override the selector (overrides are logged and counted as trials); set a stop-all time |

Cross-cutting pages that grow each milestone: **Ops** (runs, failures, durations), **Costs** (AI, data and
server spend vs budget), **Audit log**, **Settings**, **Glossary**.

---

## 7. Go-live checklist (all must be true)
- [ ] Passed every gauntlet stage with thresholds fixed *before* testing.
- [ ] Ledger shows the full trial count; DSR computed with that N.
- [ ] Lockbox holdout passed (first and only use).
- [ ] ≥ 3–6 months of paper trading inside the expected band; slippage within the cost model.
- [ ] Adds diversification to the existing portfolio.
- [ ] Max loss budget written down; position, notional and daily-loss limits enforced outside any model.
- [ ] Kill switch tested; stop rule written (live Sharpe below the backtest band → stop).
- [ ] Money at risk is money you can afford to lose entirely.

---

## 8. Monthly budget (cumulative, by milestone)

Two settings. **Lean:** Haiku 5.5 triage plus Sonnet 5.5 through the batch API, free data, a $5 server.
**Comfortable:** Opus 5.5 for reading and code, more mining proposals, a $20 server, and paid data where noted.
The interface adds $0: it's all open source and runs on the same machine or server.

| Milestones running | Roughly when | Lean / month | Comfortable / month | What's added |
|---|---|---|---|---|
| M0–M3 | months 1–3 | **$0** | **$0** | Your Mac, open-source tools, free data |
| + M4 papers | months 4–5 | **≈ $6** | **≈ $18** | AI triage and deep reading of ~40 papers/month, code for ~20 specs, weekly digest |
| + M5 mining (+ M5b) | months 6–7 | **≈ $10** | **≈ $43** | AI hypothesis proposer (300 vs 900 proposals/month); Pipeline C data is free |
| + M6 registry and alerts | month 7 | **≈ $10** | **≈ $47** | Optional AI-written daily report; alerts are free |
| + M7 paper trading | month 8 onward | **≈ $16** | **≈ $75** | Always-on server ($5 vs $20); Jev forward test (≈ $1 vs $8) |
| + M8 small live | ≈ months 10–12 | **≈ $16 + trading costs** | **≈ $75 + trading costs** | US ETFs commission-free at brokers like Alpaca (tiny regulatory fees); crypto ≈ 0.1% per trade |
| + M9 intraday (optional) | year 2 | **≈ $50** | **≈ $185** | Consolidated minute data (≈ $29 Massive vs $99 Alpaca); more Jev decisions |

**First year:** about **$110** (lean) to **$500** (comfortable) in total.
**Optional any time:** survivorship-bias-free US stock data (Norgate ≈ $35–55/month; sources disagree).
**Avoid early:** QuantGPT Pro (≈ $149/month), Tardis (≈ $350+/month).
**Not included:** your existing AI coding subscription, electricity, and any money you trade.

**LLM cost math (list prices, Oct 2026; batch API halves these):**
- Triage ~1,000 abstracts/month on Haiku 5.5 ($0.10 / $0.50 per M tokens): **≈ $0.15**.
- Deep-read 40 papers/month (~30k tokens in, ~10k out each): Sonnet 5.5 ≈ **$6**, Opus 5.5 ≈ **$13**.
- Code for ~20 strategy specs: Sonnet ≈ **$2**, Opus ≈ **$4**.
- LLM hypothesis proposer, 900 proposals/month: Haiku ≈ **$1**, Sonnet ≈ **$25**, Opus ≈ **$50**.
  Fewer, better hypotheses are cheaper *and* statistically stronger.

**Jev:** ≈ $0.042 per M input tokens, output free. 300k decisions/month at ~600 tokens each ≈ **$8**.

**Compute:** your Mac is free. A small VPS for 24/7 jobs is ≈ $5–20/month.

---

## 9. Where the original tools fit

| Tool | Role in this lab |
|---|---|
| **Jev** | Optional **forward-only** signal in M7. No training cutoff is published, so it can't be honestly backtested on history (look-ahead risk). Measure it live with Brier score and calibration. Cheap, but cheap ≠ edge. |
| **jev-trader** | Reading material for real-time loops. Don't run live: gas burn, no risk controls, mock model. |
| **QuantDinger** | Reference implementation (walk-forward, PBO, DSR, Monte Carlo, paper/live). Possible M7 backbone. |
| **QuantGPT** | Maybe later, for bias-free US stock data and quick studies. Results are gross of costs; $49–149/mo. |
| **The papers' rules** | Adopted: held-out years, trial ledger, plateau ≥ 0.6, kill rule, fractional Kelly with caps, risk outside models. |
| **"Jane Street-style" prompt** | Its risk rules and final checklist become our requirements. |
| **REA, AIStock** | Not needed. |

---

## 10. Main risks and mitigations

| Risk | Mitigation |
|---|---|
| Overfitting / data mining | Ledger + N-aware thresholds + lockbox + noise calibration |
| Look-ahead leakage | Feature registry, timestamp cutoffs, future-shift tests (structural, not statistical) |
| Survivorship bias | ETFs and crypto first; paid bias-free data before individual stocks |
| Unrealistic costs | Conservative cost model; paper-trading slippage feeds back into it |
| Strategy decay | Live-vs-backtest band, CUSUM alarms, retirement rules |
| LLM errors in specs or code | Human review queue; replication check against the paper's own numbers |
| Scope creep | One asset class until M6 is done |
| Spending real money too early | The go-live checklist; tiny size; money you can lose |

---

## 11. Learning track (alongside the build)
- Investing basics: Bogle, *The Little Book of Common Sense Investing*; Malkiel, *A Random Walk Down Wall Street*.
- Markets: Harris, *Trading and Exchanges*.
- Quant practice: Chan, *Quantitative Trading*; López de Prado, *Advances in Financial Machine Learning*.
- Validation: Masters, *Permutation and Randomization Tests for Trading System Development*;
  Bailey & López de Prado on the Deflated Sharpe Ratio and PBO.
- Practice: WorldQuant BRAIN (free alpha mining; possible paid consultant track).

---

## 12. Core reading list, by pipeline stage

### Frontier: the newest work (scanned 2026-10-17, covering roughly the last 10 weeks)
*arXiv preprints aren't peer-reviewed, and these notes come from abstracts, not full papers. The verdicts
are about usefulness to our lab, not about whether the authors are right.*

**Adopt now (these change our design):**
1. Vidovich & Zerjal (Oct 15, 2026). *Where Backtests and Live Trading Part Ways: Six Defects from One
   Intraday System.* arXiv 2610.21846. One live intraday system drifted from its backtest six ways: a
   default feed carrying a small fraction of opening volume, dividend-adjusted prices that live orders
   never see, live-only data, sizing above broker limits, UTC read as New York time, and settings that
   never reached the order code. The drift didn't show in summary statistics. Becomes an M2/M7 checklist.
2. Zheng (Oct 6, 2026). *On the Boundary of Admission Gates: An Injected-Truth Study of
   Falsification-First Selection.* arXiv 2610.07701. Gates beat a random-admission control only when
   signals are weak, adopting just 1–7%. Criteria on absolute returns reject even true signals. Code on
   GitHub. Shapes M3 calibration.
3. Wang & Ventre (Oct 3, 2026). *FactorBench: A Portfolio-Aware Benchmark for Automated Factor Mining.*
   arXiv 2610.06947. Benchmarks ~5,000 factors from nine mining methods (GP, RL, generative, LLM agents)
   across five markets, after costs. No paradigm consistently dominates. M5: start simple, benchmark, and
   don't bet on one method.
4. Messoudi & Mansar (Oct 14, 2026). *Calibration and Risk-Budgeted Decisions with System One Models.*
   arXiv 2610.19811. Naive thresholds broke the risk target in about half the settings; Learn-Then-Test
   in 7.8%. Not a trading paper, but it is exactly how to pick Jev's confidence threshold (M7).
5. Chen et al. (Sep 17, 2026). *Does Training on Future Data Pay? Look-Ahead Bias in Forecasting with
   Pretrained Models.* arXiv 2609.20554. Models trained past the forecast date violate the information
   set but generally did *not* forecast better (higher error in 18 of 20 US cases). Keep forbidding
   them, and test with point-in-time model vintages.

**Reality checks (set expectations):**
6. Zhu & Cai (Sep 4, 2026). *AI in Equity and Crypto Markets: Progress, Profitability Evidence, and the
   Limits of Automated Investing.* arXiv 2609.04917. Reviews work through Aug 31, 2026: real progress
   upstream (prediction, text, portfolio design), but no AI architecture shown to deliver persistent,
   cross-regime, capacity-aware net alpha.
7. Luo et al. (Aug–Sep 2026). *FORESIGHT-9: Prospective and Process-Aware Evaluation of Adaptive Trading
   Agents.* arXiv 2608.29372. Across nine counterfactual futures from a July 2026 cutoff, a fixed
   equal-weight policy beat 31 of 36 agent runs, and process logs exposed a failure the returns hid.
   We add process telemetry to observability.
8. *Agentic Quantitative Trading: A Survey of Workflows, Systems, and Evaluation* (Aug 31, 2026).
   arXiv 2608.31041. Strong forecasting doesn't reliably produce live trading performance.

**Watch list (interesting designs; claims unverified, costs often unstated):**
9. AlphaRLM (Oct 10, 2026; arXiv 2610.14959). An LLM aligned to turn financial literature into symbolic
   factors. A natural bridge between Pipeline A and Pipeline B.
10. The Jul–Oct 2026 wave of LLM and evolutionary factor miners: AlphaPilot (2610.15488), EvoQuantLab
    (2610.17130), AlphaPADI (2610.04959), *From Search to Research* (2609.35559), and AgonAlpha
    (2608.11250, tested on WorldQuant BRAIN).
11. Kirtac (Sep 20, 2026; arXiv 2609.23703). News-based trading with LLMs under costs, liquidity and
    capacity limits; out-of-sample tests use only news after the models' data-freshness dates. One of the
    more careful designs.
12. Foundation models for markets: a controlled limit-order-book study (2610.20104), KiT (2609.34507),
    DIVINE (2610.02866). Better forecasts; after-cost trading results not shown.
13. A 15-day paper-traded memecoin bot (2606.08232): +117.7%, but removing its top three trades made it
    unprofitable.

**The pattern:** a flood of new "Alpha-X" miners reports strong backtests (mostly Chinese A-shares, costs
often unstated). Meanwhile every independent evaluation from the same months (FactorBench, the admission-gate
study, *What survives honest evaluation?*, FORESIGHT-9, the Aug 2026 review) finds no dominant method and no
persistent net alpha. Use the new methods as idea generators and the independent evaluations as the referee.

### How Pipeline A judges a new paper in two minutes
Score each 0–2: point-in-time data and leakage controls · costs stated and realistic · true out-of-sample or
prospective test · trial count and multiple-testing awareness · code and data available · markets we can
actually trade · independent replication. Deep-read only the top scorers.

### Foundations: read these ten too (the maths the new papers are built on)
1. Arnott, Harvey & Markowitz (2019). *A Backtesting Protocol in the Era of Machine Learning.* J. Financial Data Science 1(1). The checklist our gauntlet follows.
2. Bailey & López de Prado (2014). *The Deflated Sharpe Ratio.* J. Portfolio Management 40(5). Our main N-aware gate.
3. Bailey, Borwein, López de Prado & Zhu (2017). *The Probability of Backtest Overfitting.* J. Computational Finance 20(4). PBO via CSCV.
4. Harvey, Liu & Zhu (2016). *…and the Cross-Section of Expected Returns.* Review of Financial Studies 29(1). Why the t-stat bar should be ~3.
5. White (2000). *A Reality Check for Data Snooping.* Econometrica 68(5). Plus Hansen (2005), *A Test for Superior Predictive Ability*, JBES.
6. López de Prado (2018). *Advances in Financial Machine Learning* (book). Purged/embargoed CV, CPCV, labelling.
7. McLean & Pontiff (2016). *Does Academic Research Destroy Stock Return Predictability?* J. Finance 71(1). Post-publication decay.
8. Hou, Xue & Zhang (2020). *Replicating Anomalies* (RFS), read alongside Jensen, Kelly & Pedersen (2023), *Is There a Replication Crisis in Finance?* (J. Finance). Both sides of the replication debate.
9. Gu, Kelly & Xiu (2020). *Empirical Asset Pricing via Machine Learning.* RFS 33(5). The reference for ML return prediction.
10. Gençay (2026). *What Survives Honest Evaluation?* arXiv 2608.27734. The standard for judging any LLM-discovered strategy.

### M2–M3: backtest validity and multiple testing
- Bailey, Borwein, López de Prado & Zhu (2014). *Pseudo-Mathematics and Financial Charlatanism.* Notices of the AMS. Minimum backtest length.
- Romano & Wolf (2005). *Stepwise Multiple Testing as Formalized Data Snooping.* Econometrica.
- Harvey & Liu (2015). *Backtesting.* J. Portfolio Management. Haircut Sharpe ratios.
- Lo (2002). *The Statistics of Sharpe Ratios.* Financial Analysts Journal. Standard errors, autocorrelation.
- Politis & Romano (1994). *The Stationary Bootstrap.* JASA. Block-bootstrap Monte Carlo.
- Sullivan, Timmermann & White (1999). *Data-Snooping, Technical Trading Rule Performance, and the Bootstrap.* J. Finance.
- Wiecki, Campbell, Lent & Stauth (2016). *All That Glitters Is Not Gold* (Quantopian, 888 algorithms).
- Masters (2020). *Permutation and Randomization Tests for Trading System Development* (book).

### M4, Pipeline A: published strategies, replication, costs
- Chen & Zimmermann (2022). *Open Source Cross-Sectional Asset Pricing.* Critical Finance Review. Our seed library.
- Novy-Marx & Velikov (2016). *A Taxonomy of Anomalies and Their Trading Costs.* RFS. Many die after costs.
- Moskowitz, Ooi & Pedersen (2012). *Time Series Momentum.* JFE. First replication target.
- Hurst, Ooi & Pedersen (2017). *A Century of Evidence on Trend-Following Investing.* JPM.
- Asness, Moskowitz & Pedersen (2013). *Value and Momentum Everywhere.* J. Finance.

### M5, Pipeline B: strategy mining and AI
- Allen & Karjalainen (1999). *Using Genetic Algorithms to Find Technical Trading Rules.* JFE. The cautionary classic: mined rules didn't beat buy-and-hold after costs out of sample.
- Kakushadze (2016). *101 Formulaic Alphas.* Wilmott. The formula language our grammar can mimic.
- Yu et al. (2023). *Generating Synergistic Formulaic Alpha Collections via RL* (AlphaGen). KDD.
- Alpha-GPT (EMNLP 2025 demo) · AlphaAgent (2025) · R&D-Agent(Q) (NeurIPS 2025). LLM-assisted mining designs.
- Glasserman & Lin (2023). *Assessing Look-Ahead Bias in Stock Return Predictions Generated by GPT Sentiment Analysis.* Anonymise entities; evaluate after the model's cutoff.
- FINSABER (KDD 2026) and *From Knowing to Doing* (2026). Long-horizon and memory-controlled reality checks.

### M6: portfolio construction and sizing
- DeMiguel, Garlappi & Uppal (2009). *Optimal Versus Naive Diversification.* RFS. 1/N is hard to beat.
- López de Prado (2016). *Building Diversified Portfolios that Outperform Out of Sample* (HRP). JPM.
- Moreira & Muir (2017). *Volatility-Managed Portfolios.* J. Finance.
- Kelly (1956). *A New Interpretation of Information Rate.* Use fractional Kelly with caps.

### M2 and M7: execution, costs, monitoring
- Almgren & Chriss (2001). *Optimal Execution of Portfolio Transactions.* J. Risk.
- Cont, Kukanov & Stoikov (2014). *The Price Impact of Order Book Events.* J. Financial Econometrics.
- Avellaneda & Stoikov (2008). *High-Frequency Trading in a Limit Order Book.* Quantitative Finance. Only if we ever market-make.
- Gneiting & Raftery (2007). *Strictly Proper Scoring Rules, Prediction, and Estimation.* JASA. Calibration for Jev-like signals.

### Surveys and reality checks for AI trading (read once)
- *Agentic Trading: When LLM Agents Meet Financial Markets* (2026) · *LLM Trading Agents in Production* (2026) ·
  *SoK: Trading Agents or Market Crashers?* (2026).

---

## 13. Links
- A Backtesting Protocol in the Era of ML (SSRN): https://papers.ssrn.com/abstract=3275654
- AlphaGen (KDD 2023): https://arxiv.org/abs/2306.12964
- Glasserman & Lin, look-ahead bias in GPT sentiment: https://arxiv.org/abs/2309.17322
- All That Glitters Is Not Gold (summary): https://quantpedia.com/quantopians-academic-paper-about-in-vs-out-of-sample-performance-of-trading-alg/
- What survives honest evaluation? (Aug 2026): https://arxiv.org/abs/2608.27734
- FINSABER, LLM strategies in the long run (KDD 2026): https://arxiv.org/abs/2505.07078
- Agentic Trading survey (2026): https://arxiv.org/abs/2605.19337
- From Knowing to Doing, memory-controlled benchmark (2026): https://arxiv.org/abs/2605.28359
- LLM trading agents in production (Sep 2026): https://arxiv.org/abs/2609.05663
- SoK: trading agents or market crashers? (Sep 2026): https://arxiv.org/abs/2609.19705
- Backtrader-Bench (2026): https://arxiv.org/abs/2608.11232
- R&D-Agent(Q) (NeurIPS 2025): https://arxiv.org/abs/2505.15155 · code: https://github.com/microsoft/RD-Agent
- Qlib: https://github.com/microsoft/qlib
- Alpha-GPT: https://arxiv.org/abs/2308.00016 · AlphaAgent: https://arxiv.org/abs/2502.16789
- Replicating Anomalies (NBER): https://www.nber.org/papers/w23394
- Is There a Replication Crisis in Finance? (JF 2023): https://doi.org/10.1111/jofi.13249
- pypbo (PBO, PSR, DSR, MinTRL): https://github.com/esvhd/pypbo
- Binance public data: https://github.com/binance/binance-public-data
- Polygon.io is now Massive: https://fisd.net/polygon-io-is-now-massive
- Norgate overview: https://nexusfi.com/d/data-providers/norgate-data/
- Tardis.dev FAQ: https://docs.tardis.dev/faq/general
- Jev on OpenRouter: https://openrouter.ai/typesafe/jev-1.13-20260917
- WorldQuant BRAIN review: https://www.quantt.co.uk/resources/worldquant-brain
- Copy Trading (Apesteguia, Oechssler & Weidenholzer, Management Science 2020): https://bse.eu/research/publications/copy-trading
- Polymarket on copy-traders (COPYCAT): https://news.polymarket.com/p/copycat
- Smart-wallet copy bot, 10-day paper test with no edge: https://github.com/005Jan/Polymarket-smart-money-tracker
