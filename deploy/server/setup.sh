#!/usr/bin/env bash
# Sets up Nightshift hosted on a fresh Ubuntu 24.04 server: Hetzner, DigitalOcean, Oracle Cloud,
# AWS Lightsail or any other (x86 or arm64, 4 GB RAM or more). Run it once, as a user with sudo:
#
#     curl -fsSL https://raw.githubusercontent.com/dhyey3333/QA-testing-using-AI-ML/main/deploy/server/setup.sh -o setup.sh
#     bash setup.sh                        # serves https://<this server's IP>.sslip.io
#     DOMAIN=qa.example.com bash setup.sh  # or your own domain, pointed at this server
#
# What it installs: Caddy (HTTPS from Let's Encrypt), the app in /opt/nightshift as its own
# user, Chromium for Playwright, and Ollama (the default model runs on Ollama Cloud). Data lives
# in /var/lib/nightshift, nightly backups in /var/backups/nightshift, and the key that encrypts
# client secrets in /etc/nightshift/secret.key. Safe to run again: each step checks what is there.
set -euo pipefail

REPO="${REPO:-https://github.com/dhyey3333/QA-testing-using-AI-ML.git}"
APP_DIR=/opt/nightshift
DATA_DIR=/var/lib/nightshift
BACKUP_DIR=/var/backups/nightshift
KEY_DIR=/etc/nightshift
APP_USER=nightshift
UV="/home/$APP_USER/.local/bin/uv"

IP="$(curl -fsS https://api.ipify.org)"
DOMAIN="${DOMAIN:-${IP//./-}.sslip.io}"   # sslip.io answers <a-b-c-d>.sslip.io with that IP: a free name for HTTPS
echo "== Nightshift on https://$DOMAIN"

echo "== packages: git, Caddy"
sudo apt-get update -q
sudo apt-get install -y -q git curl gnupg debian-keyring debian-archive-keyring apt-transport-https
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -q && sudo apt-get install -y -q caddy
fi

echo "== firewall: open 80 and 443"
# Oracle's Ubuntu image ends its INPUT chain with a REJECT rule: the ports must go in before it.
# Other providers' images have no such rule (their firewall is in the provider's console), and
# ufw, when it is on, is told directly.
if sudo iptables -S INPUT 2>/dev/null | grep -q -- "-j REJECT"; then
  sudo apt-get install -y -q iptables-persistent
  for port in 80 443; do
    if ! sudo iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
      reject_at="$(sudo iptables -L INPUT --line-numbers -n | awk '$2 == "REJECT" {print $1; exit}')"
      sudo iptables -I INPUT "$reject_at" -p tcp --dport "$port" -m state --state NEW -j ACCEPT
    fi
  done
  sudo netfilter-persistent save
fi
if command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active"; then
  sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
fi

echo "== the app: its own user, the code, Python, Chromium"
id "$APP_USER" >/dev/null 2>&1 || sudo useradd --system --create-home --home-dir "/home/$APP_USER" --shell /usr/sbin/nologin "$APP_USER"
sudo install -d -o "$APP_USER" -m 755 "$APP_DIR"
sudo install -d -o "$APP_USER" -m 700 "$DATA_DIR" "$BACKUP_DIR" "$KEY_DIR"   # secrets, reports and backups
if [ -d "$APP_DIR/.git" ]; then sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only; else sudo -u "$APP_USER" git clone -q "$REPO" "$APP_DIR"; fi
[ -x "$UV" ] || curl -LsSf https://astral.sh/uv/install.sh | sudo -u "$APP_USER" env HOME="/home/$APP_USER" sh -s -- -q
sudo -u "$APP_USER" env HOME="/home/$APP_USER" "$UV" --directory "$APP_DIR" sync --no-dev -q
sudo "$APP_DIR/.venv/bin/playwright" install-deps chromium            # system libraries, needs root
sudo -u "$APP_USER" env HOME="/home/$APP_USER" "$APP_DIR/.venv/bin/playwright" install chromium

echo "== the key that encrypts client secrets (kept apart from the data and the backups)"
NEW_KEY=""
if ! sudo test -s "$KEY_DIR/secret.key"; then
  sudo -u "$APP_USER" sh -c "umask 077; '$APP_DIR/.venv/bin/python' -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' > '$KEY_DIR/secret.key'"
  NEW_KEY="(a new key was just made)"
fi

echo "== Ollama: forwards model calls to Ollama Cloud (sign in once, see below)"
command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma4:31b-cloud >/dev/null 2>&1 || true   # a small manifest; works once signed in

echo "== settings and services"
if [ ! -f /etc/nightshift.env ]; then
  sudo tee /etc/nightshift.env >/dev/null <<EOF
PUBLIC_URL=https://$DOMAIN
MODEL_NAME=gemma4:31b-cloud
MODEL_TIMEOUT=120
NIGHTSHIFT_KEY_FILE=$KEY_DIR/secret.key
EOF
  sudo chmod 640 /etc/nightshift.env
  sudo chown root:"$APP_USER" /etc/nightshift.env
elif ! sudo grep -q NIGHTSHIFT_KEY_FILE /etc/nightshift.env; then
  echo "NIGHTSHIFT_KEY_FILE=$KEY_DIR/secret.key" | sudo tee -a /etc/nightshift.env >/dev/null
fi
sudo cp "$APP_DIR/deploy/server/nightshift.service" /etc/systemd/system/nightshift.service
sed "s/{DOMAIN}/$DOMAIN/" "$APP_DIR/deploy/server/Caddyfile" | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable nightshift
sudo systemctl restart nightshift   # also picks up a changed service file on a second run
sudo systemctl reload caddy || sudo systemctl restart caddy

cat <<EOF

Done. Three steps left, all need you:
  1. Sign this server in to Ollama (free plan):   ollama signin
     Open the link it prints on your laptop and approve it.
  2. Create your admin login (asks for a password):
     sudo -u $APP_USER $APP_DIR/.venv/bin/nightshift hosted add-user --data $DATA_DIR --email you@youragency.example --admin
  3. Copy the secrets key somewhere safe OFF this server (a password manager). Without it, a
     backup restores everything except the client secrets. $NEW_KEY
     sudo cat $KEY_DIR/secret.key

Then open https://$DOMAIN, turn on two-factor login under Account, and point an uptime
monitor at https://$DOMAIN/healthz (deploy/server/README.md, "Monitoring").
EOF
