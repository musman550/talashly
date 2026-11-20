#!/usr/bin/env bash
# VM par EK baar chalao (ssh se): git ko root ke liye "safe" karta hai (warna auto-update ka git chup-chaap fail hota tha),
# naya code/compose khinchta hai, containers naye image par chalata hai. Data (./data) aur .env kabhi nahi chhute.
set -u
cd ~/talashly || exit 1
for u in "" "sudo"; do $u git config --global --add safe.directory "$PWD" 2>/dev/null; done
echo "== pehle:  $(git log -1 --format='%h %s' 2>/dev/null)"
cp -n config.toml /tmp/config.toml.vm-backup 2>/dev/null
git fetch -q origin main && git reset --hard -q origin/main
echo "== ab:     $(git log -1 --format='%h %s')"
sudo docker compose pull -q 2>&1 | tail -2
sudo docker compose up -d 2>&1 | tail -4
for i in $(seq 1 30); do
  if curl -fsS -m 10 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then echo "== health OK ($((i*5))s)"; break; fi
  sleep 5
done
curl -s -m 15 -w '\n  [%{time_total}s]\n' http://127.0.0.1:8000/api/mesh/status
sudo systemctl restart talashly-update.timer 2>/dev/null || true
