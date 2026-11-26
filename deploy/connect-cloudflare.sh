#!/usr/bin/env bash
# Talashly VM par chalayein: Cloudflare Workers AI (free, open-source models) ko overflow lane banata hai.
# Istemal: bash deploy/connect-cloudflare.sh ACCOUNT_ID API_TOKEN   (token ko sirf "Workers AI: Read/Edit" ki ijazat dein)
set -euo pipefail
ACCT="${1:-}"; TOK="${2:-}"
case "$ACCT" in *[!A-Za-z0-9]*|"") echo "ACCOUNT_ID sirf huroof/ankon ka hona chahiye"; exit 1;; esac
[ -n "$TOK" ] || { echo "API_TOKEN missing"; exit 1; }
cd "$(dirname "$0")/.."
touch .env
MODEL="${CF_MODEL:-@cf/google/gemma-4-26b-a4b-it}"
out=$(curl -s -w '\n%{http_code}' --max-time 60 -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Say OK\"}],\"max_tokens\":8}" \
  "https://api.cloudflare.com/client/v4/accounts/$ACCT/ai/v1/chat/completions" || true)
code=$(printf '%s' "$out" | tail -n1)
if [ "$code" != "200" ]; then
  echo "Cloudflare ne $code diya. Jawab (token nahi dikhta):"; printf '%s' "$out" | head -n -1 | head -c 400; echo
  echo "401/403 = token ya permission, 400/404 = model ka naam. Kuch badla nahi."; exit 1
fi
grep -v '^CF_' .env > .env.tmp || true
{ echo "CF_ACCOUNT_ID=$ACCT"; echo "CF_API_TOKEN=$TOK"; echo "CF_MAX_PER_DAY=${CF_MAX_PER_DAY:-120}"; } >> .env.tmp
mv .env.tmp .env && chmod 600 .env
sudo docker compose up -d
echo "Cloudflare Workers AI connected (roz max ${CF_MAX_PER_DAY:-120} jawab, free 10,000 neurons ke andar)."
