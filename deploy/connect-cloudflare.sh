#!/usr/bin/env bash
# Talashly VM par chalayein: Cloudflare Workers AI (free, open-source models) ko overflow lane banata hai.
# Istemal: bash deploy/connect-cloudflare.sh ACCOUNT_ID API_TOKEN   (token ko sirf "Workers AI: Read/Edit" ki ijazat dein)
set -euo pipefail
ACCT="${1:-}"; TOK="${2:-}"
case "$ACCT" in *[!A-Za-z0-9]*|"") echo "ACCOUNT_ID sirf huroof/ankon ka hona chahiye"; exit 1;; esac
[ -n "$TOK" ] || { echo "API_TOKEN missing"; exit 1; }
cd "$(dirname "$0")/.."
touch .env
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Say OK"}],"max_tokens":5}' \
  "https://api.cloudflare.com/client/v4/accounts/$ACCT/ai/v1/chat/completions" || true)
[ "$code" = "200" ] || { echo "Cloudflare ne $code diya: account id/token/permission check karein. Kuch badla nahi."; exit 1; }
grep -v '^CF_' .env > .env.tmp || true
{ echo "CF_ACCOUNT_ID=$ACCT"; echo "CF_API_TOKEN=$TOK"; echo "CF_MAX_PER_DAY=${CF_MAX_PER_DAY:-120}"; } >> .env.tmp
mv .env.tmp .env && chmod 600 .env
sudo docker compose up -d
echo "Cloudflare Workers AI connected (roz max ${CF_MAX_PER_DAY:-120} jawab, free 10,000 neurons ke andar)."
