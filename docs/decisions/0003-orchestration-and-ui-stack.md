# 0003. Keep the stack small: one API, one database file, a static web app

- Date: 2026-10-17
- Status: accepted

## Context

The roadmap listed Dagster, MLflow and Next.js. Each is a good tool, and each is another service to run,
upgrade and debug. The lab runs on one Mac for one person.

## Decision

- **Records:** the DuckDB file is the single system of record for trials, audit, catalog and gauntlet
  results. No MLflow until there is a need it alone covers.
- **Orchestration:** jobs are plain CLI commands (`qlab ingest`, `qlab gauntlet`, ...) that the API can also
  trigger. Scheduling starts with launchd/cron. Dagster comes in when there are more than ~10 scheduled
  jobs with dependencies between them.
- **Web app:** Vite + React + TypeScript, built to static files and served by the API. No server-side
  rendering is needed for a local app, so Next.js isn't.
- **Charts and grids:** Apache ECharts (statistics), TradingView Lightweight Charts (candles and trade
  markers, with its required attribution), plain tables first, FINOS Perspective when live streaming
  arrives (M7).

## Upgrade path, only when a limit is actually hit

| Limit | Next step |
|---|---|
| Concurrent writers (API + jobs) | Job queue with a worker process; then Postgres/TimescaleDB |
| Mining too slow on 15 cores | Process pool, then Ray; hot loops in Rust (decision 0001) |
| Many scheduled jobs | Dagster |
| Large minute-bar history | Parquet partitioned by year; DuckDB reads it in place |

## Consequences

Setup is `make setup && make serve`. Fewer moving parts to break. The trade-off is that scheduling and
experiment comparison are simpler than in dedicated tools until they're needed.
