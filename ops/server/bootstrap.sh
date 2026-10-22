#!/usr/bin/env bash
# Prepare a fresh Ubuntu 24.04 server for quant-lab. Safe to run again.
#
# From the Mac:  ssh root@HOST 'bash -s' < ops/server/bootstrap.sh
#
# What it does, in order:
#   1. security updates on, the clock in UTC, a 2 GB swap file (small machines run out of memory in mining bursts)
#   2. a firewall that lets in SSH only (the lab's web app listens on 127.0.0.1; reach it through an SSH tunnel)
#   3. SSH logins with keys only
#   4. a user `qlab` that runs everything, with the same SSH key as root, and /srv/quant-lab for the checkout
#   5. uv (Python and the lab's packages) and, for the weekly research inbox, the Claude CLI, both for `qlab`
# Nothing here places orders or touches the lab's data.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "== packages and updates"
apt-get update -q
apt-get install -y -q git rsync curl ca-certificates ufw unattended-upgrades
systemctl enable --now unattended-upgrades
timedatectl set-timezone UTC

echo "== swap"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap -q /swapfile
  if swapon /swapfile; then
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  else
    echo "   WARNING: this machine refused a swap file; carrying on without one"; rm -f /swapfile
  fi
fi

echo "== firewall (SSH only)"
ufw allow OpenSSH >/dev/null
ufw --force enable || echo "   WARNING: the firewall couldn't be enabled here; check 'ufw status' on the real server"

echo "== SSH: keys only"
cat > /etc/ssh/sshd_config.d/10-qlab.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
systemctl reload ssh || systemctl reload sshd

echo "== user qlab"
id qlab >/dev/null 2>&1 || useradd --create-home --shell /bin/bash qlab
install -d -m 700 -o qlab -g qlab /home/qlab/.ssh
if [ -f /root/.ssh/authorized_keys ]; then
  install -m 600 -o qlab -g qlab /root/.ssh/authorized_keys /home/qlab/.ssh/authorized_keys
fi
install -d -o qlab -g qlab /srv/quant-lab /srv/quant-lab/data
# qlab gets no sudo: the deploy script installs units and restarts services over root's SSH key, so the user
# that runs research code (and reads untrusted paper text) can't change the system.

echo "== uv and the Claude CLI for qlab"
sudo -u qlab -H bash -lc 'command -v ~/.local/bin/uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh'
sudo -u qlab -H bash -lc 'command -v ~/.local/bin/claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash' \
  || echo "   (the Claude CLI didn't install; the weekly inbox needs it or an Ollama server, see docs/DEPLOY.md)"

echo "== done: $(nproc) cores, $(free -g | awk '/Mem:/ {print $2}') GB memory, $(df -h /srv | awk 'NR==2 {print $4}') free"
echo "   next, on the Mac: ops/server/deploy.sh $(hostname -I | awk '{print $1}') --data"
