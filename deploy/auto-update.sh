#!/usr/bin/env bash
# VM par har 5 minute chalta hai (systemd timer). Pehle GitHub ki tayyar image khinchta hai (tez, CPU ka bojh nahi);
# image na mile (private/abhi ban rahi) to code naya aane par VM par hi build karta hai. Data (./data) kabhi nahi chhuta.
set -uo pipefail
DIR="${DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$DIR" || exit 0
git config --global --add safe.directory "$DIR" 2>/dev/null   # root se chalne par "dubious ownership" se git chup-chaap fail na ho
CHANGED=0
# autopilot VM ke bohat chhote CPU ko khata hai: default band (AUTOPILOT_ON=1 ho to chalne do)
[ "${AUTOPILOT_ON:-0}" = "1" ] || docker compose rm -sf autopilot >/dev/null 2>&1 || true
if git fetch --quiet origin main 2>/dev/null; then
  LOCAL="$(git rev-parse HEAD)"; REMOTE="$(git rev-parse origin/main)"
  if [ "$LOCAL" != "$REMOTE" ]; then
    echo "$(date -u +%FT%TZ) code update $LOCAL -> $REMOTE"
    git reset --hard origin/main --quiet && CHANGED=1
  fi
fi
if docker compose pull --quiet search 2>/dev/null; then
  docker compose up -d 2>&1 | tail -3          # sirf tab dobara banta hai jab image ya config badla ho
elif [ "$CHANGED" = 1 ]; then
  echo "$(date -u +%FT%TZ) prebuilt image nahi mili: VM par build"
  docker compose up -d --build 2>&1 | tail -5
fi
docker image prune -f >/dev/null 2>&1
if ! curl -fsS --max-time 15 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  echo "$(date -u +%FT%TZ) health fail: restart"
  docker compose up -d 2>&1 | tail -3
fi
