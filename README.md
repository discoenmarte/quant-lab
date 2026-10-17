# quant-lab

[![CI](https://github.com/discoenmarte/quant-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/discoenmarte/quant-lab/actions/workflows/ci.yml)

An evidence-first quant research lab that runs on one machine. It builds a data lake from free sources,
backtests strategies with realistic costs, puts every candidate through a 10-stage validation gauntlet,
reads new research papers with a local model, mines strategies on a budget, and shows all of it in a web
interface. Every backtest is recorded in a trial ledger, so the statistics stay honest about how many things
were tried.

> Research and paper trading only. Nothing in this repository places real-money orders.

## Quick start

```bash
make setup     # Python env (uv) + web dependencies (npm)
make test      # tests
make serve     # build the web app and serve everything on http://127.0.0.1:8765
```

Development with hot reload: `make dev-api` in one terminal, `make dev-web` in another (http://127.0.0.1:5173).

## Architecture

Simple, strong, and able to grow. One Python package, one database file, one API, one static web app. Each
layer has a narrow contract, data flows one way, and the scale-up path is written down before it's needed.

```mermaid
flowchart LR
  subgraph Sources
    S1[Binance public data]
    S2[Stooq / Yahoo ETFs]
    S3[Ken French factors]
    S4[arXiv q-fin]
  end
  subgraph Lab["qlab (Python)"]
    D[data<br/>ingest · validate · store]
    B[backtest<br/>strategies · engine · costs · metrics]
    G[gauntlet<br/>10 stages · calibration]
    I[inbox<br/>papers → specs, local LLM]
    M[mining<br/>budgeted search]
    L[(ledger + audit<br/>DuckDB)]
    P[(Parquet<br/>curated series)]
  end
  API[Lab API<br/>FastAPI]
  WEB[Web app<br/>React · ECharts · Lightweight Charts]
  S1 & S2 & S3 --> D --> P --> B --> G
  S4 --> I --> B
  M --> B
  B --> L
  G --> L
  L --> API
  P --> API
  API --> WEB
```

### Layers and their contracts

| Layer | Package | Contract |
|---|---|---|
| Data | `qlab.data` | Each series is a Parquet file `curated/<source>/<symbol>/<interval>.parquet` with columns `ts` (UTC), `open, high, low, close, volume` (+ `adj_close` when the source has it). Ingestion is idempotent and verifies checksums when the source publishes them. Quality issues go to the `quality_issues` table. |
| Backtest | `qlab.backtest` | A strategy is a pure function `positions = f(bars, **params)` that may only use data up to each bar. The engine applies positions from the next bar, charges costs on every change, and returns daily returns, positions and trades. |
| Gauntlet | `qlab.gauntlet` | Takes a strategy, its parameter grid and the bars. Returns a report card: ten stages, each with value, threshold, pass/fail and chart data. Thresholds are versioned in the database. |
| Ledger | `qlab.ledger`, `qlab.audit` | Append-only. Every backtest is a trial on a research question; every action is an audit event. |
| Inbox | `qlab.inbox` | Papers become structured `StrategySpec` records through an LLM provider interface (local Ollama by default). |
| Mining | `qlab.mining` | Budgeted search over a strategy grammar. Every candidate is a ledger trial, and the significance bar rises with N. |
| API | `qlab.api` | Thin HTTP layer, no business logic. The web app, the CLI and (later) a Telegram bot all use it. |
| Web | `web/` | Static React app that reads the API only. Every page belongs to a milestone. |

### Principles

- **Honesty is structural.** Look-ahead is prevented by the strategy contract and tested by changing future
  data. The trial count N sits next to every Sharpe ratio. The lockbox holdout is used once.
- **Data flows one way:** sources → raw → curated → backtests → gauntlet → registry → paper. No layer reads
  from the layer above it.
- **Time is UTC** in storage. The UI shows exchange time next to it.
- **Reproducible.** Seeds are fixed for simulations, inputs are fingerprinted, and dependencies are locked
  (`uv.lock`, `package-lock.json`).
- **Safe by default.** The server binds to 127.0.0.1, secrets stay in `.env` (ignored), and no code path can
  send a real order. From M7 on, reducing risk is one click and increasing it needs a reason and a delay.

### Scaling path (taken only when a limit is actually hit)

| Limit | Step |
|---|---|
| Long jobs block the API | Worker process + job queue |
| Concurrent writers | Postgres / TimescaleDB instead of the DuckDB file |
| Mining too slow on 15 cores | Process pool, then Ray; measured hot loops in Rust ([decision 0001](docs/decisions/0001-python-first-rust-where-measured.md)) |
| Many scheduled jobs | Dagster ([decision 0003](docs/decisions/0003-orchestration-and-ui-stack.md)) |
| Years of minute bars | Parquet partitioned by year |

## Milestones

| | Milestone | Delivers | Status |
|---|---|---|---|
| M0 | Foundations | Package, ledger, audit log, Lab API, web shell, CI | done |
| M1 | Data lake | Free sources (ETFs, BTC/ETH, factors), quality checks, Data Health page | next |
| M2 | Backtest engine | Strategy library, cost model, metrics, Backtest Studio | planned |
| M3 | Validation gauntlet | 10 stages, calibration on synthetic data, Gauntlet Console, Ledger page | planned |
| M4 | Research inbox | arXiv feed, local-LLM triage and specs, replication checks | planned |
| M5 | Strategy mining | Grammar search, genetic programming, rising significance bar | planned |
| M6 | Registry and alerts | Strategy board, portfolio, alert rules | planned |
| M7 | Paper trading | Paper broker on live public data, trading desk, kill switch | planned |
| M8 | Go-live gate | Evidence checklist, limits outside any model | planned |
| M9 | Intraday mode | Validated intraday library, morning selector, replay | planned |

The full plan, with each milestone's screens, costs and the research it rests on, is in
[ROADMAP.md](ROADMAP.md). Implementation state and next steps are in [docs/HANDOFF.md](docs/HANDOFF.md).

## Repository layout

```
src/qlab/          Python package (data, backtest, gauntlet, inbox, mining, api, ledger, audit)
tests/             pytest suite; each test gets its own data folder
web/               React app (Vite + TypeScript)
docs/STYLE.md      docstring and commit conventions
docs/decisions/    architecture decision records
docs/HANDOFF.md    implementation status and how to continue
data/              local data lake and database (not committed)
```

## Decisions

1. [Python first, Rust where a measurement says so](docs/decisions/0001-python-first-rust-where-measured.md)
2. [Reverse-engineering is an audit tool, not an alpha source](docs/decisions/0002-rea-scope.md)
3. [Keep the stack small](docs/decisions/0003-orchestration-and-ui-stack.md)
