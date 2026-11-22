#!/usr/bin/env python3
"""Live site ka asli speed-test (stdlib only). GitHub Action `speedtest` ise chalata hai.
Har model + sawal ke liye: pehla token kitni der mein, total, kitne chunks, sab se bada gap (atkna), mode, jawab ki lambai."""
import json
import sys
import time
import urllib.request

SITE = (sys.argv[1] if len(sys.argv) > 1 else "http://137.23.39.13:8000").rstrip("/")
CASES = [
    ("mus-7.01-flash", "what is the capital of france"),
    ("mus-7.01", "explain how a bloom filter works in simple words"),
    ("mus-7.01-deep", "write a short email to my landlord about a leaking roof"),
    ("mus-7.01-deep", "create a simple restaurant website with html and css"),
    ("mus-7.01-code", "write a python function that checks if a string is a palindrome"),
    ("mus-7.01-polyglot", "اسلام آباد پاکستان کا دارالحکومت کیوں ہے؟"),
]


def get(path, t=20):
    t0 = time.time()
    try:
        with urllib.request.urlopen(SITE + path, timeout=t) as r:
            return r.status, r.read()[:600].decode("utf8", "replace"), time.time() - t0
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}", time.time() - t0


def chat(model, q, timeout=200):
    body = json.dumps({"message": q, "private": True, "model": model, "history": []}).encode()
    req = urllib.request.Request(SITE + "/api/chat", body, {"Content-Type": "application/json", "X-Client-Id": "speedtest-%d" % int(time.time() * 1000)})
    t0 = time.time()
    first = None
    last = t0
    gap_max = 0.0
    chunks = 0
    text = ""
    mode = steps = ""
    ev = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf8", "replace").strip()
                if line.startswith("event:"):
                    ev = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                now = time.time()
                try:
                    d = json.loads(line[5:].strip())
                except Exception:  # noqa: BLE001
                    continue
                if ev == "token" and isinstance(d, str):
                    if first is None:
                        first = now - t0
                    else:
                        gap_max = max(gap_max, now - last)
                    last = now
                    chunks += 1
                    text += d
                elif ev == "replace" and isinstance(d, str):
                    text = d
                elif ev == "step":
                    steps += (d.get("msg", "") if isinstance(d, dict) else str(d))[:60] + " | "
                elif ev == "done" and isinstance(d, dict):
                    mode = d.get("mode", "")
    except Exception as e:  # noqa: BLE001
        return {"model": model, "q": q[:40], "error": f"{type(e).__name__}: {e}", "t": round(time.time() - t0, 1)}
    return {"model": model, "q": q[:40], "first_s": None if first is None else round(first, 1), "total_s": round(time.time() - t0, 1),
            "chunks": chunks, "max_gap_s": round(gap_max, 1), "chars": len(text), "mode": mode, "steps": steps[:200], "head": text[:90].replace("\n", " ")}


print("SITE", SITE)
for p in ("/api/health", "/api/mesh/status"):
    c, b, t = get(p)
    print(f"GET {p}: {c} {t:.2f}s {b[:260]}")
for m, q in CASES:
    print(json.dumps(chat(m, q), ensure_ascii=False))
c, b, t = get("/api/mesh/status")
print(f"AFTER mesh/status: {c} {t:.2f}s {b[:300]}")
