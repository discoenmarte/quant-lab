# quant-lab: notes for Claude

Evidence-first quant research lab. Python package in `src/qlab`, web app in `web/`, plan in `ROADMAP.md`.

## Commands

- `make setup`: install Python and web dependencies
- `make test` / `make lint`: pytest and ruff
- `make serve`: build the web app and serve everything on http://127.0.0.1:8765
- `make dev-api` + `make dev-web`: hot-reload development (web on :5173, proxies /api)
- `make deploy HOST=…` / `make deploy-first HOST=…`: put the lab on the server (`docs/DEPLOY.md`, decision 0012);
  `make tunnel HOST=…` opens its web app on :8770, `make server-status HOST=…` shows its services and timers

## Where the lab runs

- Once `make deploy-first` has run, the server (`/srv/quant-lab`, user `qlab`) is the lab: its ledger is the one
  that counts. The Mac's `data/` is then a development copy; never run research jobs against both.
- On the server every scheduled step is a plain `qlab` command on a systemd timer, and every job goes through the
  Lab API (DuckDB has one writer). Don't add scheduled Claude sessions for deterministic work.
- The preview tool can't read `~/Documents` on this Mac: serve scratch labs from a shell with the broker keys blanked
  and attach the browser pane by URL.

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
- No code path places real-money orders. The go-live gate (M8, decision 0013) computes the checklist; the live
  order path gets built only when an entry's checklist is all green, and only with the owner's explicit approval.
- Real candidates are pre-registered in `docs/preregistration/` and committed before they run.
