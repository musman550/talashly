#!/usr/bin/env bash
# Alag machine (Azure/AWS/Oracle A1, Ubuntu, 3 GB+ RAM) par apna open-source AI server chalao: koi third-party API nahi.
# Isay AI wali machine par chalayein: bash llm-node-setup.sh
# Ye ek lamba API-key khud banata hai (sirf aap ki Talashly VM ko pata hona chahiye) aur aakhir mein connect wali command chhapta hai.
set -euo pipefail
RAM=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$RAM" -lt 2800 ]; then echo "Is machine mein sirf ${RAM} MB RAM hai: Qwen3 1.7B ke liye kam az kam 3 GB chahiye."; exit 1; fi
command -v docker >/dev/null || { curl -fsSL https://get.docker.com | sudo sh; }
sudo systemctl enable --now docker >/dev/null 2>&1 || true
mkdir -p "$HOME/llm-node/models" && cd "$HOME/llm-node"
[ -f .key ] || head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 40 > .key
KEY=$(cat .key)
sudo docker rm -f talashly-llm >/dev/null 2>&1 || true
sudo docker run -d --name talashly-llm --restart unless-stopped -p 8081:8081 \
  -e LLAMA_CACHE=/models -v "$PWD/models:/models" ghcr.io/ggml-org/llama.cpp:server \
  -hf unsloth/Qwen3-1.7B-GGUF:Q4_K_M --host 0.0.0.0 --port 8081 -c 4096 -t "$(nproc)" --parallel 2 --api-key "$KEY"
IP=$(curl -fsS --max-time 8 https://api.ipify.org || echo "<is-machine-ka-public-IP>")
echo
echo "Model pehli dafa download hoga (~1.1 GB). Logs: sudo docker logs -f talashly-llm"
echo "Cloud firewall mein TCP 8081 sirf apni Talashly VM ke IP ke liye kholein (sab ke liye nahi)."
echo
echo "Ab apni Talashly VM par ye chalayein:"
echo "  bash ~/talashly/deploy/connect-llm.sh http://$IP:8081 $KEY"
