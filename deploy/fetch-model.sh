#!/usr/bin/env bash
# Kaggle par train hua GGUF model (GitHub Release `model-latest`) utha kar llama.cpp server se chalata hai.
# Chalayein (bade-RAM wale AI node par, jahan llm-node-setup.sh chal chuki ho): bash deploy/fetch-model.sh
set -euo pipefail
DIR="${DIR:-$HOME/talashly-model}"; mkdir -p "$DIR"
URL="https://github.com/musman550/talashly/releases/download/model-latest/talashly-qwen3-1.7b-Q4_K_M.gguf"
curl -fL --retry 3 -o "$DIR/model.gguf" "$URL"
echo "Model: $DIR/model.gguf  ($(du -h "$DIR/model.gguf" | cut -f1))"
echo "llama-server -m $DIR/model.gguf --host 0.0.0.0 --port 8081 -c 4096 -t \$(nproc) --parallel 2 --api-key <KEY>"
echo "Phir Oracle VM par: bash deploy/connect-llm.sh (url + key) - Talashly turant naya model use karega."
