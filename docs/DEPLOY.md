# Putting the lab on a server

What you get: the lab running around the clock on a small rented computer for about €8 a month. It records the paper
book's decisions every evening, sends paper orders, mines strategies at night, reads new papers on Sundays and
takes a snapshot every morning. It writes to you on Telegram, and answers you there. Nothing in it can trade real
money (decision 0013). The design is in decision 0012.

Each step below says who does it. "Claude" means a Claude Code session in this repository. You can also run every
command yourself.

## 1. Telegram (you, 5 minutes)

1. In Telegram, open **@BotFather**, send `/newbot`, and pick a name and a username ending in `bot`.
2. In the Mac's terminal, in this folder:

   ```bash
   uv run qlab bot setup
   ```

   Paste the token BotFather gave you (it isn't shown as you paste). When the command says so, open your new bot on
   your phone and press **Start**. The command saves the token and your chat id in `data/lab.env` (readable by you
   only) and sends you a test message.

## 2. An outside alarm (you, 5 minutes, recommended)

A server that dies can't tell you. healthchecks.io can: the server pings it every ten minutes, and if the pings
stop it emails you.

1. Sign up at **healthchecks.io** (free).
2. Add a check named `quant-lab`. Set **Period 15 minutes, Grace 30 minutes**. Copy its ping URL
   (`https://hc-ping.com/...`).
3. Save it in the lab:

   ```bash
   uv run qlab config QLAB_HEALTHCHECK_URL https://hc-ping.com/your-uuid
   ```

4. (Optional) Under **Integrations**, add Telegram, so the alarm reaches your phone too.

## 3. The server (you, 10 minutes)

Claude can't create accounts or enter payment details, so this part is yours.

1. Sign up at **hetzner.com/cloud** and add a payment method.
2. In the Cloud Console: **New project** → `quant-lab` → **Add server**:
   - **Location:** any EU location (Falkenstein, Nuremberg or Helsinki). The ARM servers are EU-only, and Binance
     blocks US addresses.
   - **Image:** Ubuntu 24.04.
   - **Type:** Shared vCPU → **Arm64 (Ampere)** → **CAX11** (2 cores, 4 GB, about €6.50 a month with IPv4).
   - **Networking:** keep IPv4 and IPv6.
   - **SSH key:** add your Mac's public key. Print it with `cat ~/.ssh/id_ed25519.pub` and paste the whole line.
   - **Backups:** on (+20%, about €1.30). Hetzner then keeps seven daily images of the whole server.
   - **Name:** `quant-lab`.
3. Click **Create & Buy now** and copy the server's IPv4 address.

## 4. Move the lab (Claude, or you, 5 minutes)

Give Claude the server's IP address, or run these yourself from this folder:

```bash
ssh root@SERVER_IP 'bash -s' < ops/server/bootstrap.sh
```

```bash
make deploy-first HOST=SERVER_IP
```

The first command prepares the server:

- security updates and a firewall that lets in SSH only
- a user `qlab` for the lab
- Python's tools, and the Claude CLI for the paper reader

The second command builds the web app, copies the code, the data folder (about 1.3 GB) and your keys, installs the
packages, and starts the services and timers. It stops if a lab process is running on the Mac. Close the lab's
browser tab and any `qlab serve` first.

## 5. Switch off the Mac's scheduled jobs (Claude)

From now on the server is the lab. The five scheduled Claude sessions on the Mac (nightly paper job, morning
fills, mining, inbox, enrolment) must be switched off, or the two copies drift apart. Claude does this right after
a successful deploy-first. You can also do it from the desktop app's Scheduled tasks list.

## 6. The paper reader on the server (you, optional)

The weekly research inbox needs a language model. The Mac's local model is too big for this server, so the
server uses the Claude CLI on your plan:

1. On the Mac, run `claude setup-token` and follow the browser sign-in. It prints a long-lived token.
2. Save it on the server, with the provider and a monthly cap:

   ```bash
   ssh qlab@SERVER_IP
   ```

   ```bash
   cd /srv/quant-lab && .venv/bin/qlab config CLAUDE_CODE_OAUTH_TOKEN
   ```

   ```bash
   .venv/bin/qlab config QLAB_LLM_PROVIDER claude && .venv/bin/qlab config QLAB_LLM_BUDGET_USD 10
   ```

3. Back on the Mac, run `make deploy HOST=SERVER_IP`. It turns the Sunday inbox on once it sees the provider.

Without this the inbox stays off; everything else runs.

## Using it

- **From your phone:** send `/status`, `/pnl`, `/report`, `/board`, `/golive`, `/mining`, `/alerts` or `/help` to
  your bot. `/pause` stops new orders at once. `/kill` asks for a code, then stops everything and sells every paper
  sleeve. Nothing on the phone can add risk.
- **The web app:**

  ```bash
  make tunnel HOST=SERVER_IP
  ```

  Then open http://127.0.0.1:8770. It isn't on the internet; the tunnel is your private door.
- **Updates after code changes:**

  ```bash
  make deploy HOST=SERVER_IP
  ```

- **What ran, and when:**

  ```bash
  make server-status HOST=SERVER_IP
  ```

- **A copy of the snapshots and reports on the Mac:**

  ```bash
  make pull HOST=SERVER_IP
  ```

## The schedule (New York time; the server handles daylight saving)

| When | What |
|---|---|
| Mon-Fri 09:50 | Sync paper fills; send once what the opening auction left unfilled |
| Mon-Fri 17:00 | Ingest. Wait up to five hours if Yahoo is late. Decide, score, send paper orders, build the daily report |
| Every day 01:30 | One mining job (250 trials), judged only if it clears the luck bar |
| Every day 04:30 | Database snapshot (three kept) |
| Sunday 19:00 | Research inbox (when the model is set up) |
| Every 10 minutes | Heartbeat to healthchecks.io |

## When something goes wrong

| You see | It means | Do |
|---|---|---|
| Telegram: "Server job qlab-nightly failed" | A step failed; the daily report says which | `make server-status HOST=SERVER_IP`, or ask Claude |
| Telegram: "No daily report since the … close" | The nightly job didn't run or didn't finish | Same as above |
| Email from healthchecks.io | The server, or the lab on it, stopped answering | Check the server in Hetzner's console; reboot it there if needed. The services start by themselves |
| Telegram: "only … GB of disk left" | Snapshots or calibration data grew | Ask Claude to clean old reports, or resize the disk |
| Memory alerts, or mining nights failing | 4 GB is too tight | In Hetzner, power off, rescale to CAX21 (8 GB), power on |

## Costs

| Item | Per month |
|---|---|
| Hetzner CAX11 | about €6.50 |
| Hetzner backups | about €1.30 |
| Telegram, healthchecks.io, Alpaca paper, market data | €0 |
| Claude CLI for the inbox | €0 extra on your plan (capped by `QLAB_LLM_BUDGET_USD`) |
| **Total** | **about €8** |
