#!/usr/bin/env bash
# Oracle Cloud Always Free VM (Ubuntu 22.04/24.04, ARM ya AMD) par Talashly chalane ki script.
# Chalane ka tareeqa (VM par SSH karke):
#   curl -fsSL https://raw.githubusercontent.com/musman550/talashly/main/deploy/oracle-setup.sh | bash
# Optional: PUBLIC_URL=https://aap-ka-domain.example  (SEO/sitemap ke liye)
set -euo pipefail
REPO="${REPO:-https://github.com/musman550/talashly}"
DIR="${DIR:-$HOME/talashly}"
PUBLIC_URL="${PUBLIC_URL:-}"

echo "[1/5] Docker install..."
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo systemctl enable --now docker

echo "[2/5] Code download..."
sudo apt-get install -y git >/dev/null
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone --depth 1 "$REPO" "$DIR"; fi
cd "$DIR"
mkdir -p data && sudo chown -R 1000:1000 data

echo "[3/5] Settings (.env)..."
{
  echo "SS_SITE_URL=${PUBLIC_URL}"
  echo "SS_TRUST_PROXY=0"
} > .env

echo "[4/5] Firewall (Ubuntu iptables, port 8000)..."
if ! sudo iptables -C INPUT -p tcp --dport 8000 -j ACCEPT 2>/dev/null; then
  sudo iptables -I INPUT 1 -p tcp --dport 8000 -j ACCEPT
  command -v netfilter-persistent >/dev/null 2>&1 && sudo netfilter-persistent save || \
    (sudo apt-get install -y iptables-persistent >/dev/null 2>&1 && sudo netfilter-persistent save) || true
fi

echo "[5/5] Start (24/7, restart on reboot)..."
sudo docker compose up -d --build
sleep 8
if curl -fsS http://127.0.0.1:8000/api/health | grep -q '"status"'; then
  echo "OK: Talashly chal raha hai. Ab Oracle console mein VCN Security List mein TCP 8000 ingress allow karein,"
  echo "phir browser mein http://<VM-public-IP>:8000 kholein."
else
  echo "Server abhi jawab nahi de raha. Logs: sudo docker compose logs --tail 80"; exit 1
fi
