#!/usr/bin/env bash
# Talashly VM par chalayein: alag AI machine se jodta hai. Istemal: bash connect-llm.sh http://IP:8081 KEY
set -euo pipefail
URL="${1:-}"; KEY="${2:-}"
case "$URL" in http://*|https://*) ;; *) echo "Istemal: bash connect-llm.sh http://IP:8081 KEY"; exit 1;; esac
[ -n "$KEY" ] || { echo "KEY missing"; exit 1; }
cd "$(dirname "$0")/.."
touch .env
grep -v '^SS_LLM_' .env > .env.tmp || true
{ echo "SS_LLM_URL=$URL"; echo "SS_LLM_KEY=$KEY"; } >> .env.tmp
mv .env.tmp .env && chmod 600 .env
if curl -fsS --max-time 15 -H "Authorization: Bearer $KEY" "$URL/health" >/dev/null 2>&1; then echo "AI machine jawab de rahi hai."; else echo "WARNING: AI machine abhi jawab nahi de rahi (model download ho raha ho sakta hai, ya firewall port 8081 band hai)."; fi
sudo docker compose up -d
echo "Connected. Talashly ab kaam aur kamzor sawalon ke liye us AI se baat karegi."
