#!/usr/bin/env bash
# Oracle Cloud Always Free VM (Ubuntu 22.04/24.04, ARM ya AMD) par Talashly chalane ki script.
# Chalane ka tareeqa (VM par SSH karke):
#   curl -fsSL https://raw.githubusercontent.com/musman550/talashly/main/deploy/oracle-setup.sh | bash
# Optional: PUBLIC_URL=https://aap-ka-domain.example  (SEO/sitemap ke liye)
set -euo pipefail
REPO="${REPO:-https://github.com/musman550/talashly}"
DIR="${DIR:-$HOME/talashly}"
PUBLIC_URL="${PUBLIC_URL:-}"

if ! command -v apt-get >/dev/null 2>&1; then
  echo "Ye script Ubuntu/Debian ke liye hai (apt-get nahi mila). VM banate waqt image 'Canonical Ubuntu' chunein."; exit 1
fi

# Kam RAM (1 GB Micro VM) par build/chalne ke liye swap
if [ "$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)" -lt 3000 ] && ! swapon --show | grep -q .; then
  echo "[0/5] Kam RAM: 2 GB swap bana raha hoon..."
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

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
