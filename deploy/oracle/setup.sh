#!/usr/bin/env bash
# Sets up Nightshift hosted on a fresh Oracle Cloud "Always Free" server:
# Ubuntu 24.04 on Ampere (arm64), 2 OCPU / 12 GB. Run it once, as the default `ubuntu` user:
#
#     curl -fsSL https://raw.githubusercontent.com/dhyey3333/QA-testing-using-AI-ML/main/deploy/oracle/setup.sh -o setup.sh
#     bash setup.sh                      # serves https://<this server's IP>.sslip.io
#     DOMAIN=qa.example.com bash setup.sh  # or your own domain, pointed at this server
#
# What it installs: Caddy (HTTPS from Let's Encrypt), the app in /opt/nightshift as its own
# user, Chromium for Playwright, and Ollama (the model itself runs on Ollama Cloud's free plan).
# Data lives in /var/lib/nightshift. Safe to run again: each step checks what is there.
set -euo pipefail

REPO="${REPO:-https://github.com/dhyey3333/QA-testing-using-AI-ML.git}"
APP_DIR=/opt/nightshift
DATA_DIR=/var/lib/nightshift
APP_USER=nightshift
UV="/home/$APP_USER/.local/bin/uv"

IP="$(curl -fsS https://api.ipify.org)"
DOMAIN="${DOMAIN:-${IP//./-}.sslip.io}"   # sslip.io answers <a-b-c-d>.sslip.io with that IP: a free name for HTTPS
echo "== Nightshift on https://$DOMAIN"

echo "== packages: git, Caddy"
sudo apt-get update -q
sudo apt-get install -y -q git curl gnupg debian-keyring debian-archive-keyring apt-transport-https iptables-persistent
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -q && sudo apt-get install -y -q caddy
fi

echo "== firewall: Oracle's Ubuntu image blocks every port but 22 in iptables, besides the cloud's own rules"
for port in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT 6 -p tcp --dport "$port" -m state --state NEW -j ACCEPT
done
sudo netfilter-persistent save

echo "== the app: its own user, the code, Python, Chromium"
id "$APP_USER" >/dev/null 2>&1 || sudo useradd --system --create-home --home-dir "/home/$APP_USER" --shell /usr/sbin/nologin "$APP_USER"
sudo mkdir -p "$APP_DIR" "$DATA_DIR"
sudo chown "$APP_USER:" "$APP_DIR" "$DATA_DIR"
sudo chmod 700 "$DATA_DIR"   # test secrets and reports live here
if [ -d "$APP_DIR/.git" ]; then sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only; else sudo -u "$APP_USER" git clone -q "$REPO" "$APP_DIR"; fi
[ -x "$UV" ] || curl -LsSf https://astral.sh/uv/install.sh | sudo -u "$APP_USER" env HOME="/home/$APP_USER" sh -s -- -q
sudo -u "$APP_USER" env HOME="/home/$APP_USER" "$UV" --directory "$APP_DIR" sync --no-dev -q
sudo "$APP_DIR/.venv/bin/playwright" install-deps chromium            # system libraries, needs root
sudo -u "$APP_USER" env HOME="/home/$APP_USER" "$APP_DIR/.venv/bin/playwright" install chromium

echo "== Ollama: forwards model calls to Ollama Cloud (sign in once, see below)"
command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma4:31b-cloud >/dev/null 2>&1 || true   # a small manifest; works once signed in

echo "== settings and services"
if [ ! -f /etc/nightshift.env ]; then
  sudo tee /etc/nightshift.env >/dev/null <<EOF
PUBLIC_URL=https://$DOMAIN
MODEL_NAME=gemma4:31b-cloud
MODEL_TIMEOUT=120
EOF
  sudo chmod 640 /etc/nightshift.env
  sudo chown root:"$APP_USER" /etc/nightshift.env
fi
sudo cp "$APP_DIR/deploy/oracle/nightshift.service" /etc/systemd/system/nightshift.service
sed "s/{DOMAIN}/$DOMAIN/" "$APP_DIR/deploy/oracle/Caddyfile" | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now nightshift
sudo systemctl reload caddy || sudo systemctl restart caddy

cat <<EOF

Done. Two steps left, both need you:
  1. Sign this server in to Ollama (free plan):   ollama signin
     Open the link it prints on your laptop and approve it.
  2. Create your admin login (asks for a password):
     sudo -u $APP_USER $APP_DIR/.venv/bin/nightshift hosted add-user --data $DATA_DIR --email you@youragency.example --admin

Then open https://$DOMAIN
EOF
