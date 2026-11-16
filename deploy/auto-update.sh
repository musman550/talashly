#!/usr/bin/env bash
# VM par har 5 minute chalta hai (systemd timer): GitHub se naya code aaye to rebuild + restart.
# Data (./data) kabhi nahi chhuta. Fail ho to purana container chalta rehta hai.
set -uo pipefail
DIR="${DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$DIR" || exit 0
git fetch --quiet origin main 2>/dev/null || exit 0
LOCAL="$(git rev-parse HEAD)"; REMOTE="$(git rev-parse origin/main)"
if [ "$LOCAL" != "$REMOTE" ]; then
  echo "$(date -u +%FT%TZ) update $LOCAL -> $REMOTE"
  git reset --hard origin/main --quiet
  docker compose up -d --build 2>&1 | tail -5
  docker image prune -f >/dev/null 2>&1
fi
# Container band ho gaya ho to wapas chalu (health fail ya crash)
if ! curl -fsS --max-time 10 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  echo "$(date -u +%FT%TZ) health fail: restart"
  docker compose up -d 2>&1 | tail -3
fi
