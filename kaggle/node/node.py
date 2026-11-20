# Kaggle notebook = Talashly ka LLM relay-node. Sirf outbound HTTP: koi tunnel/khula port nahi.
# GPU mile to CUDA llama.cpp + bara Qwen3-8B; warna CPU llama.cpp + chhota (tez) Qwen3-1.7B. Qwen open models ko pehli tarjeeh.
# llama-server (127.0.0.1:8081) -> engine.relaynode (Talashly se kaam pull, token push). Workflow placeholders (__...__) bharta hai.
import asyncio
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

SITE = "__SITE__"
SECRET = "__SECRET__"
OWN_MODEL = "__MODEL_URL__"          # Talashly ka apna trained model (Release), khali ho sakta hai
HOURS = float("__HOURS__")
NAME = "__SLUG__"
W = "/kaggle/working"
T0 = time.time()
HF = "https://huggingface.co/unsloth"
CPU_MODELS = [OWN_MODEL, f"{HF}/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf", f"{HF}/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf"]
GPU_MODELS = [f"{HF}/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf", f"{HF}/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf"]


def log(*a):
    print(f"[{(time.time() - T0) / 60:5.1f}m]", *a, flush=True)


def sh(cmd, check=True):
    log("$", cmd[:150])
    return subprocess.run(cmd, shell=True, check=check)


def fetch(url, dest, tries=4, deadline_s=None):
    """Network tootne par (Connection reset...) dobara koshish, har baar shuru se."""
    for i in range(1, tries + 1):
        log("download", url[:110], f"(koshish {i})" if i > 1 else "")
        try:
            t_start = time.time()
            req = urllib.request.Request(url, headers={"User-Agent": "talashly-node"})
            with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
                    if deadline_s and time.time() - t_start > deadline_s:
                        raise TimeoutError(f"{deadline_s}s se zyada lag raha hai")
            return
        except Exception as e:  # noqa: BLE001
            log("download fail:", type(e).__name__, str(e)[:100])
            if os.path.exists(dest):
                os.remove(dest)
            if i == tries:
                raise
            time.sleep(3 * i)


def has_gpu() -> bool:
    return subprocess.run("nvidia-smi -L", shell=True, capture_output=True).returncode == 0


def releases():
    return json.load(urllib.request.urlopen(urllib.request.Request(
        "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=15", headers={"User-Agent": "talashly-node"}), timeout=30))


def install_llama(kind: str, with_rt: bool = False) -> str:
    """kind: cpu | cuda. Naam/format badalta rehta hai -> naye releases mein pehla jo match kare. Server ka raasta lautata hai."""
    pat = r"^llama-.*-bin-ubuntu-x64\.(tar\.gz|zip)$" if kind == "cpu" else r"^llama-.*-bin-ubuntu-cuda-12\.\d+-x64\.(tar\.gz|zip)$"
    rt_pat = r"^cudart-llama-.*-bin-ubuntu-cuda-12\.\d+-x64\.(tar\.gz|zip)$"
    d = f"{W}/llama_{kind}"
    os.makedirs(d, exist_ok=True)
    for r in releases():
        names = {a["name"]: a for a in r.get("assets", [])}
        main = next((a for n, a in names.items() if re.match(pat, n)), None)
        if not main:
            continue
        todo = [main] + ([a for n, a in names.items() if re.match(rt_pat, n)][:1] if kind == "cuda" and with_rt else [])
        for k, a in enumerate(todo):
            arc = f"{W}/{a['name']}"
            try:
                fetch(a["browser_download_url"], arc, deadline_s=240 if k else None)
                sh(f"cd {W} && " + (f"unzip -q -o {arc} -d {d}" if arc.endswith(".zip") else f"tar -xzf {arc} -C {d}"))
                os.remove(arc)
            except Exception as e:  # noqa: BLE001
                if k == 0:
                    raise          # asli server-build zaroori hai
                log("cudart nahi mila (Kaggle image ki apni CUDA lib chalegi):", str(e)[:80])
        server = glob.glob(f"{d}/**/llama-server", recursive=True)
        if server:
            os.chmod(server[0], 0o755)
            return server[0]
    raise RuntimeError(f"llama.cpp {kind} build nahi mili")


def get_model(urls: list[str]) -> str:
    for u in urls:
        if not u or u.startswith("__"):
            continue
        dest = f"{W}/" + re.sub(r"[^A-Za-z0-9._-]", "_", u.split("/")[-1] or "model.gguf")
        try:
            if not os.path.exists(dest):
                fetch(u, dest)
            return dest
        except Exception as e:  # noqa: BLE001
            log("model url fail:", e)
    raise RuntimeError("koi model download nahi hua")


def start_server(server: str, model: str, gpu: bool):
    libdir = os.path.dirname(server)
    env = dict(os.environ, LD_LIBRARY_PATH=libdir + ":" + os.environ.get("LD_LIBRARY_PATH", ""))
    # ctx 8192, 2 slot => har slot 4096 (Deep ke lambe prompt ke liye)
    args = [server, "-m", model, "--host", "127.0.0.1", "--port", "8081", "-c", "8192", "--parallel", "2", "-t", str(os.cpu_count() or 4)]
    if gpu:
        args += ["-ngl", "99"]
    logf = open(f"{W}/llama.log", "a")
    proc = subprocess.Popen(args, env=env, stdout=logf, stderr=subprocess.STDOUT)
    t = time.time()
    while time.time() - t < 420:
        if proc.poll() is not None:
            raise RuntimeError("llama-server band ho gaya: " + open(f"{W}/llama.log").read()[-500:])
        try:
            if urllib.request.urlopen("http://127.0.0.1:8081/health", timeout=3).status == 200:
                log(f"llama-server tayyar ({'GPU' if gpu else 'CPU'}), {time.time() - t:.0f}s")
                return proc
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    proc.terminate()
    raise RuntimeError("llama-server 7 min mein tayyar nahi hua")


def bring_up():
    if has_gpu():
        try:
            log("GPU mili: CUDA build + bara model")
            model = get_model(GPU_MODELS)
            try:   # pehle bina cudart (Kaggle image mein CUDA hota hai)
                return start_server(install_llama("cuda"), model, True), "gpu"
            except Exception as e:  # noqa: BLE001
                log("CUDA server bina cudart nahi chala, cudart ke saath:", str(e)[:200])
                return start_server(install_llama("cuda", with_rt=True), model, True), "gpu"
        except Exception as e:  # noqa: BLE001
            log("GPU raasta fail, CPU par girte hain:", str(e)[:300])
    return start_server(install_llama("cpu"), get_model(CPU_MODELS), False), "cpu"


async def serve(proc, kind):
    from engine.relaynode import RelayWorker
    w = RelayWorker(SITE, SECRET, "http://127.0.0.1:8081", name=NAME, model="qwen3", slots=2,
                    max_runtime_s=max(60.0, HOURS * 3600 - (time.time() - T0)), log=log)

    async def watchdog():   # llama mar jaye to notebook khatam: guard naya chala dega
        while w.should_run():
            if proc.poll() is not None:
                log("llama-server mar gaya - band")
                w.stop = True
            await asyncio.sleep(5)
    wd = asyncio.create_task(watchdog())
    log(f"relay shuru: {kind.upper()} node '{NAME}'")
    await w.run()
    wd.cancel()


def main():
    os.makedirs(W, exist_ok=True)
    sh("pip install -q httpx")
    sh(f"git clone --depth 1 https://github.com/musman550/talashly {W}/repo")
    sys.path.insert(0, f"{W}/repo")
    proc, kind = bring_up()
    try:
        asyncio.run(serve(proc, kind))
    finally:
        proc.terminate()
        log("tail llama.log:", open(f"{W}/llama.log").read()[-600:])


if __name__ == "__main__":
    main()
