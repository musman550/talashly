#!/usr/bin/env bash
# Apna open-source AI writer (Qwen3 1.7B, Apache-2.0) on karo: koi third-party API ya key nahi.
# Sirf 6 GB+ RAM wali VM par (Oracle A1 ARM). 1 GB Micro VM par chalana nuqsan-deh hai, isliye script mana kar degi.
set -euo pipefail
cd "$(dirname "$0")/.."
RAM=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$RAM" -lt 6000 ]; then
  echo "Is VM mein sirf ${RAM} MB RAM hai. Apna AI writer kam az kam 6 GB maangta hai (Oracle A1: 2 OCPU / 12 GB)."
  echo "Abhi on karne se site bhi slow ho jayegi, isliye main isay on nahi kar raha."; exit 1
fi
mkdir -p data/models && sudo chown -R 1000:1000 data/models || true
touch .env
grep -q '^SS_LLM_URL=' .env || echo 'SS_LLM_URL=http://llm:8081' >> .env
sudo docker compose --profile llm up -d --build
echo "Model pehli dafa download hoga (~1.1 GB); 3-10 minute. Logs: sudo docker compose logs -f llm"
