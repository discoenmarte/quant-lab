# quant-lab: notes for Claude

Evidence-first quant research lab. Python package in `src/qlab`, web app in `web/`, plan in `ROADMAP.md`.

## Commands

- `make setup`: install Python and web dependencies
- `make test` / `make lint`: pytest and ruff
- `make serve`: build the web app and serve everything on http://127.0.0.1:8765
- `make dev-api` + `make dev-web`: hot-reload development (web on :5173, proxies /api)

## Conventions

- Docstrings and comments follow `docs/STYLE.md`: a signature block on every function (summary, Args,
  Returns, Raises), precise and plain.
- One feature per commit, Conventional Commits, pushed directly to `main`. Tag each finished milestone
  (`m0`, `m1`, ...).
- The only author is discoenmarte. Never add `Co-Authored-By` trailers or "Generated with" lines to
  commits, pull requests, docs or code.
- Architecture decisions go in `docs/decisions/` as short numbered records.
- Store time in UTC; show exchange time next to it in the UI.

## Research rules (never break these)

- Every backtest goes through `qlab.ledger.record_trial`, including manual runs, mining and calibration.
- Signals may only use data available at decision time. Positions are applied from the next bar.
- No code path places real-money orders. Paper trading only, until the go-live gate (M8) exists.
