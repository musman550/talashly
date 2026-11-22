#!/bin/bash
# Oracle A1 VM pehli baar boot hote hi ye chalta hai (cloud-init, root). __MESH_SECRET__ workflow bharta hai.
exec > /var/log/talashly-init.log 2>&1
set -x
export HOME=/root MESH_SECRET="__MESH_SECRET__"
for i in 1 2 3 4 5 6; do curl -fsSL https://raw.githubusercontent.com/musman550/talashly/main/deploy/oracle-setup.sh -o /root/oracle-setup.sh && break; sleep 20; done
bash /root/oracle-setup.sh
cd /root/talashly
# Apna 24/7 open-source Qwen (A1: 4 core / 24 GB): search ko 1 core, LLM ko 3
for kv in "LLM_HF=unsloth/Qwen3-4B-GGUF:Q4_K_M" "LLM_THREADS=3" "LLM_MEM=8g" "LLM_CTX=8192"; do
  k=${kv%%=*}; grep -q "^$k=" .env || echo "$kv" >> .env
done
bash deploy/enable-llm.sh
echo A1_READY
