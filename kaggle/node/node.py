# Kaggle CPU notebook (30 GB RAM, free) = Talashly ka LLM relay-node. Sirf outbound HTTP: koi tunnel/khula port nahi.
# llama.cpp (prebuilt binary) + GGUF model -> local llama-server -> engine.relaynode (Talashly se kaam pull, token push).
# Workflow `kaggle-node` placeholders (__...__) bharta hai aur har ~10.5 ghante mein dobara chalata hai (do slugs alternate).
import glob
import json
import os
import subprocess
import sys
import time
import urllib.request

SITE = "__SITE__"
SECRET = "__SECRET__"
MODEL_URLS = ["__MODEL_URL__", "https://huggingface.co/unsloth/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf"]
HOURS = float("__HOURS__")
NAME = "__SLUG__"
W = "/kaggle/working"
T0 = time.time()


def log(*a):
    print(f"[{(time.time() - T0) / 60:5.1f}m]", *a, flush=True)


def sh(cmd, check=True):
    log("$", cmd[:150])
    return subprocess.run(cmd, shell=True, check=check)


def fetch(url, dest):
    log("download", url[:100])
    req = urllib.request.Request(url, headers={"User-Agent": "talashly-node"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)


def main():
    os.makedirs(W, exist_ok=True)
    sh("pip install -q httpx")
    sh(f"git clone --depth 1 https://github.com/musman550/talashly {W}/repo")
    sys.path.insert(0, f"{W}/repo")
    # 1) llama.cpp prebuilt (CPU)
    rel = json.load(urllib.request.urlopen(urllib.request.Request(
        "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest", headers={"User-Agent": "talashly-node"}), timeout=30))
    asset = next(a for a in rel["assets"] if "bin-ubuntu-x64" in a["name"] and a["name"].endswith(".zip") and "vulkan" not in a["name"] and "rocm" not in a["name"])
    fetch(asset["browser_download_url"], f"{W}/llama.zip")
    sh(f"cd {W} && unzip -q -o llama.zip -d llama")
    server = glob.glob(f"{W}/llama/**/llama-server", recursive=True)[0]
    os.chmod(server, 0o755)
    libdir = os.path.dirname(server)
    # 2) model
    for u in MODEL_URLS:
        try:
            fetch(u, f"{W}/model.gguf")
            break
        except Exception as e:  # noqa: BLE001
            log("model url fail:", e)
    else:
        raise SystemExit("model download nahi hua")
    # 3) local llama-server
    env = dict(os.environ, LD_LIBRARY_PATH=libdir + ":" + os.environ.get("LD_LIBRARY_PATH", ""))
    nproc = os.cpu_count() or 4
    proc = subprocess.Popen([server, "-m", f"{W}/model.gguf", "--host", "127.0.0.1", "--port", "8081", "-c", "4096",
                             "-t", str(nproc), "--parallel", "2"], env=env, stdout=open(f"{W}/llama.log", "w"), stderr=subprocess.STDOUT)
    # 4) relay worker: Talashly se kaam kheencho
    import asyncio
    from engine.relaynode import RelayWorker
    w = RelayWorker(SITE, SECRET, "http://127.0.0.1:8081", name=NAME, model="qwen3", slots=2,
                    max_runtime_s=max(60.0, HOURS * 3600 - (time.time() - T0)), log=log)
    try:
        asyncio.run(w.run())
    finally:
        proc.terminate()
        log("tail llama.log:", open(f"{W}/llama.log").read()[-600:])


if __name__ == "__main__":
    main()
