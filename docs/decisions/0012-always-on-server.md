# 0012. The lab on a small always-on server: plain timers, one database writer, two watchdogs

- Date: 2026-10-22
- Status: accepted

## Context

Until now the lab ran on the owner's Mac. Five scheduled Claude sessions did the daily work:

- the nightly paper job
- the morning fill check
- nightly mining
- the weekly research inbox
- a one-off enrolment

That worked while the Mac was awake. It also used the owner's Claude plan for steps that are deterministic, and
nothing noticed a night that never ran. The owner asked for a server that computes and searches around the clock,
"as cheap as possible but powerful enough to make real money".

The first week of broker paper trading also exposed two defects that a server would have repeated every day:

1. **Missed decisions.** At 20:30 New York on 2026-10-19 and 2026-10-21, Yahoo's full-history query still ended on
   the previous session, four hours after the close. The book decided nothing on 2 of 4 nights, and a missed
   decision can't be made up.
2. **Part-filled auctions.** On 2026-10-21 ten of fifteen market-on-open orders ended unfilled or part-filled. That
   left the sleeve 13% invested against a 71% target. Alpaca's paper engine part-fills about one order in ten on
   purpose, and an `opg` order's remainder expires once the auction is over.

## Decisions

### 1. Fix the two defects first

**Missed decisions:**

- `yahoo.fetch` also asks for a five-day range, which Yahoo serves fresher, and appends any later sessions.
- The appended bars are rescaled onto the full history's dividend basis, so an ex-date on the newest day can't
  create a false return.
- The nightly job can wait for a late close: `--wait-minutes` re-ingests Yahoo every 20 minutes until the latest
  finished session is stored. The report says when it waited.
- On the server it starts at 17:00 New York and waits up to five hours.

**Part-filled auctions:**

- `execution.catch_up` runs after the open (09:50 New York). It sends each remainder once, as a day order, within
  the same limits.
- These orders have purpose `catchup` and are flagged late.
- Their fills count in slippage against the open, so the cost of missing the auction is measured, not hidden.

### 2. Plain programs on systemd timers, not scheduled AI sessions

Every scheduled step is now a `qlab` command with no judgement left in it. The mining rotation, written for the
scheduled session as instructions, moved into `qlab mine nightly`, with the same rules:

- it mines only while mined trials count at least twice
- it skips a night when the machine is busy
- it picks the least-mined scope, then the weekday's paradigm
- it judges a candidate only when it clears the luck bar
- it never raises a budget

The server runs them from systemd timers, in the exchange's timezone, so daylight saving is handled by the calendar
and not by hand. Times are in New York time:

| Unit | When | Command |
|---|---|---|
| `qlab-api` | always | `qlab serve` (127.0.0.1 only): the one process that holds the database |
| `qlab-bot` | always | `qlab bot run` (see 4) |
| `qlab-nightly` | Mon-Fri 17:00 | `qlab paper nightly --wait-minutes 300` |
| `qlab-morning` | Mon-Fri 09:50 | `qlab broker catchup` |
| `qlab-mining` | daily 01:30 | `qlab mine nightly` |
| `qlab-inbox` | Sun 19:00 | `qlab inbox run` (only once a model is configured) |
| `qlab-backup` | daily 04:30 | `qlab backup --keep 3` |
| `qlab-heartbeat` | every 10 min | `qlab heartbeat` |

A failed job starts `qlab-failed@<unit>`, which sends a Telegram alert without needing the database.

### 3. One database writer: every job goes through the API

DuckDB lets one process write. On the server that process is `qlab serve`. The paper job, mining, the inbox, the
catch-up and the snapshot all send their work to the API when it answers, and run in-process only when it doesn't.

Snapshots use `COPY FROM DATABASE`, one consistent transaction inside the writer. A copy of the 1.1 GB database
took 5.8 s on the Mac and 9 s on the test server, and is 840 MB.

### 4. A Telegram bot with the web app's asymmetry, and two watchdogs

**The bot** (`qlab bot run`):

- It long-polls Telegram, so the server needs no open port, domain or certificate.
- It answers only the owner's chat id.
- **Reads:** `/status`, `/pnl`, `/report`, `/board`, `/alerts`, `/mining`, `/golive`.
- **Reduces risk:** `/pause` works at once; `/kill` needs a four-digit code typed back within two minutes.
- Nothing that adds risk exists in the bot. Resuming, enrolling and loosening limits stay on the web app, with a
  reason and 24 hours.

**The watchdogs:**

- **The bot, inside:** every five minutes it checks that the API answers, and that a daily report arrived within
  ten hours of the last US close. Either failing sends one message straight to Telegram.
- **healthchecks.io, outside:** a dead server can't report itself. `qlab heartbeat` runs every ten minutes and makes
  the same two checks, plus disk and memory. It pings `QLAB_HEALTHCHECK_URL`, a free healthchecks.io check, with
  success or `/fail`. When the pings stop, healthchecks.io emails the owner.

### 5. The machine: the smallest ARM server that measured enough

Measured on a 4-core, 4 GB ARM Ubuntu 24.04 container that ran the real deploy and every job on a copy of the lab:

- **The API server** holds about 500 MB.
- **A mining night** (250 trials on `crypto_top30`, 3 workers) took 31 s.
- **The nightly paper job** took 51 s.
- **The memory peak**, file cache included, was 3.3 GB.

| Option (Oct 2026, EU, with IPv4) | Cores / memory | Per month | Verdict |
|---|---|---|---|
| Hetzner CAX11 | 2 ARM / 4 GB | about €6.50 | **Chosen.** Enough with the 2 GB swap file the bootstrap adds and one mining worker (a night takes about 90 s). |
| Hetzner CAX21 | 4 ARM / 8 GB | about €11 | The step up if memory alerts appear. Resizing takes a reboot. |
| Hetzner backups | – | +20% | Seven daily images of the whole server. Worth it: about €1.30. |
| Oracle Cloud free tier | 4 ARM / 24 GB | €0 | Capacity is hard to get, and idle accounts get reclaimed. Not dependable enough for a book that can't miss a night. |

Calibration studies are the one heavy job: 50-110 minutes on 14 Mac cores. Their trials belong in the same ledger,
so they run on the server. For an afternoon the server can be resized to a large ARM machine, billed by the hour,
then shrunk back.

**More compute would not find more money.** Every mined trial raises the bar for everything later tested on its
scope, twice from thresholds v5. The budgets of 2,000 trials per question are the limit, not the CPU.

### 6. Security

- **Firewall:** SSH only, with key logins.
- **The web app** listens on 127.0.0.1. It is reached through an SSH tunnel (`make tunnel`), never a public port,
  since it has no login of its own.
- **The user `qlab`** runs the lab and has no sudo. It also reads untrusted paper text through a language model.
  Units are installed over root's SSH key.
- **Secrets** live in `data/lab.env` and the repository's `.env`, both mode 600.

### 7. The language model on the server

The 35-billion-parameter local model doesn't fit a small server. The weekly inbox uses the Claude CLI there instead:

- **Plan token:** `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`, on the owner's plan, no extra cost.
- **Or an API key:** `ANTHROPIC_API_KEY`, billed per token.
- **Budget:** `QLAB_LLM_BUDGET_USD` caps the CLI's reported cost per month.

The inbox timer is enabled only once `QLAB_LLM_PROVIDER` is set. Mining doesn't need a model: the language-model
paradigm isn't calibrated and isn't scheduled.

## Consequences

- **Deploying:** `ops/server/bootstrap.sh` (once, as root) and `ops/server/deploy.sh HOST [--data]` (from the Mac).
  Both were run end to end against an Ubuntu 24.04 container before the first real server existed. That test caught
  a bug: a code copy that could replace the data folder with a link.
- **The move:** after `--data`, the server is the lab. The Mac's scheduled sessions must be switched off, or two
  ledgers diverge.
- **Cost:** about €8 a month with backups. The broker and data stay free; the model is on the owner's plan.
