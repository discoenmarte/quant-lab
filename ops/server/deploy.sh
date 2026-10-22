#!/usr/bin/env bash
# Put this checkout on the server, install its Python packages there and (re)start the lab's services.
#
#   ops/server/deploy.sh HOST           code and services (every later update)
#   ops/server/deploy.sh HOST --data    the first move: also copy the lab's data folder and keys from this Mac
#
# HOST is the server's address. It must have run ops/server/bootstrap.sh once. Root's SSH key installs the systemd
# units; everything else runs as the user qlab. The web app is built here, so the server needs no Node.
#
# After `--data` the server is the lab: switch off the Mac's own scheduled jobs (docs/DEPLOY.md, step 5), or the
# two copies will drift apart.
set -euo pipefail

host="${1:?usage: deploy.sh HOST [--data]}"
mode="${2:-}"
repo="$(cd "$(dirname "$0")/../.." && pwd)"
remote=/srv/quant-lab
# Extra ssh options, e.g. QLAB_SSH_OPTS="-p 2222 -i ~/.ssh/other_key" (bash 3.2 on macOS: no empty-array tricks).
read -r -a extra <<< "${QLAB_SSH_OPTS:-}"
rsh=(ssh -o ConnectTimeout=15 ${extra[@]+"${extra[@]}"})

echo "== build the web app"
(cd "$repo/web" && npm run build --silent)

if [[ "$mode" == "--data" ]]; then
  echo "== check that nothing on this Mac is writing to the lab"
  if pgrep -fl "[q]lab (serve|paper|mine|broker|inbox|gauntlet|calibrate|portfolio)" ; then
    echo "stop these lab processes on this Mac first (and close the desktop app's lab preview)"; exit 1
  fi
  echo "== stop the server's lab services, if any"
  "${rsh[@]}" "root@$host" "systemctl stop 'qlab-*' 2>/dev/null || true"
fi

echo "== copy the code"
rsync -az --delete -e "${rsh[*]}" \
  --exclude .git/ --exclude .venv/ --exclude web/node_modules/ --exclude /data --exclude .env \
  --exclude .claude/ --exclude __pycache__/ --exclude .pytest_cache/ --exclude .ruff_cache/ --exclude .DS_Store \
  "$repo/" "qlab@$host:$remote/"

if [[ "$mode" == "--data" ]]; then
  echo "== copy the data folder ($(du -shL "$repo/data" | cut -f1); local snapshots and logs stay here)"
  rsync -az --stats -e "${rsh[*]}" --exclude backups/ --exclude logs/ "$repo/data/" "qlab@$host:$remote/data/"
  if [[ -f "$repo/.env" ]]; then
    echo "== copy the broker keys (.env, readable by qlab only)"
    rsync -a --chmod=F600 -e "${rsh[*]}" "$repo/.env" "qlab@$host:$remote/.env"
  fi
fi

echo "== install the Python environment"
"${rsh[@]}" "qlab@$host" "cd $remote && ~/.local/bin/uv sync --frozen --no-dev --quiet"

echo "== install and start the services"
"${rsh[@]}" "root@$host" bash -s <<EOF
set -euo pipefail
cp $remote/ops/server/systemd/qlab-* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now qlab-api.service qlab-nightly.timer qlab-morning.timer qlab-mining.timer \
  qlab-backup.timer qlab-heartbeat.timer qlab-radar.timer qlab-radar-deep.timer >/dev/null
systemctl restart qlab-api.service
env_has() { grep -qs "^\$1=." $remote/data/lab.env $remote/.env; }
if env_has QLAB_TELEGRAM_TOKEN && env_has QLAB_TELEGRAM_CHAT; then
  systemctl enable qlab-bot.service >/dev/null && systemctl restart qlab-bot.service && echo "   Telegram bot: on"
else
  systemctl disable --now qlab-bot.service 2>/dev/null || true; echo "   Telegram bot: off (no token and chat yet)"
fi
if env_has QLAB_LLM_PROVIDER; then
  systemctl enable --now qlab-inbox.timer >/dev/null && echo "   weekly research inbox: on"
else
  systemctl disable --now qlab-inbox.timer 2>/dev/null || true; echo "   weekly research inbox: off (no model yet)"
fi
EOF

echo "== check"
"${rsh[@]}" "qlab@$host" "for i in \$(seq 1 20); do curl -fs http://127.0.0.1:8765/api/health && exit 0; sleep 1; done; exit 1" \
  && echo && echo "deployed. Open the lab with: ssh -N -L 8770:127.0.0.1:8765 qlab@$host  then http://127.0.0.1:8770"
"${rsh[@]}" "root@$host" "systemctl list-timers 'qlab-*' --no-pager | head -14"
