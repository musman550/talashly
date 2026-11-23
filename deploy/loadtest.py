#!/usr/bin/env python3
"""N users ek saath (stdlib). Har user alag client-id se mixed sawal bhejta hai. Report: kitne poore, busy/error, p50/p95 pehla token aur total."""
import json
import statistics as st
import sys
import threading
import time
import urllib.request

SITE = (sys.argv[1] if len(sys.argv) > 1 else "http://137.23.39.13:8000").rstrip("/")
N = int(sys.argv[2]) if len(sys.argv) > 2 else 100
MIX = ["hello", "what is todays date", "what is the capital of france", "who wrote Hamlet", "tell me", "is talashly free",
       "create a website for Noor Bakery", "explain how a bloom filter works in simple words", "what is 12*13"]
res, lock = [], threading.Lock()


def one(i):
    q = MIX[i % len(MIX)]
    body = json.dumps({"message": q, "private": True, "model": "mus-7.01-flash", "history": []}).encode()
    req = urllib.request.Request(SITE + "/api/chat", body, {"Content-Type": "application/json", "X-Client-Id": "load-%d-%d" % (i, int(time.time()))})
    t0, first, mode, err = time.time(), None, "", None
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            ev = ""
            for raw in r:
                line = raw.decode("utf8", "replace").strip()
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    if ev == "token" and first is None:
                        first = time.time() - t0
                    elif ev == "error":
                        err = line[5:80]
                    elif ev == "done":
                        try:
                            mode = json.loads(line[5:]).get("mode", "")
                        except ValueError:
                            pass
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"[:80]
    with lock:
        res.append({"q": q, "first": first, "total": time.time() - t0, "mode": mode, "err": err})


T0 = time.time()
ths = [threading.Thread(target=one, args=(i,)) for i in range(N)]
for t in ths:
    t.start()
    time.sleep(0.05)
for t in ths:
    t.join()
ok = [r for r in res if not r["err"] and r["first"] is not None]
print(f"users={N} ok={len(ok)} failed={len(res) - len(ok)} wall={time.time() - T0:.0f}s")


def pct(v, p):
    v = sorted(v)
    return round(v[min(len(v) - 1, int(len(v) * p))], 1) if v else None


for q in MIX:
    rr = [r for r in ok if r["q"] == q]
    print(f"{q[:42]:44} n={len(rr):3} first p50={pct([r['first'] for r in rr], .5)} p95={pct([r['first'] for r in rr], .95)} "
          f"total p95={pct([r['total'] for r in rr], .95)} modes={sorted({r['mode'] for r in rr})}")
errs = {}
for r in res:
    if r["err"]:
        errs[r["err"]] = errs.get(r["err"], 0) + 1
print("errors:", json.dumps(errs))
print("health:", urllib.request.urlopen(SITE + "/api/health", timeout=30).read()[:200].decode())
sys.exit(0)
