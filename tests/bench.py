"""Benchmark (pytest nahi): python tests/bench.py   -- sirf local naap, internet ki delay shamil nahi."""
import asyncio
import http.server
import os
import random
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.crawler import Crawler  # noqa: E402
from engine.db import DB, sha  # noqa: E402
from engine.extract import chunk_text  # noqa: E402
from engine.search import Searcher  # noqa: E402
from tests.helpers import make_cfg  # noqa: E402

random.seed(7)
WORDS = ("crawler index search engine python asyncio network database vector memory thread kernel compiler protocol cache latency "
         "throughput storage cluster gateway router packet socket kernel module driver parser lexer token syntax grammar "
         "robots sitemap feed article news weather finance market energy health science history culture travel").split()


def doc_text(i: int, n=420) -> str:
    return " ".join(f"{random.choice(WORDS)}{'' if random.random() < .8 else i % 97}" for _ in range(n)) + f" uniqueterm{i}."


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def bench_index_and_search(n_docs=20000):
    tmp = Path(tempfile.mkdtemp())
    cfg = make_cfg(tmp)
    db = DB(cfg["general"]["db_path"])
    t0 = time.time()
    db.x("BEGIN")
    for i in range(n_docs):
        text = doc_text(i)
        db.upsert_doc(f"http://bench.test/{i}", f"Document {i} about {random.choice(WORDS)}", None, None, "b", None, sha(text), chunk_text(text))
        if i % 500 == 499:
            db.x("COMMIT"); db.x("BEGIN")
    db.x("COMMIT")
    build = time.time() - t0
    c = db.counts()
    size = db.size_mb()
    print(f"INDEX BUILD : {n_docs} docs / {c['chunks']} chunks in {build:.1f}s ({n_docs/build:.0f} docs/s), db {size:.0f} MB")
    s = Searcher(db, cfg)
    qs = ["crawler index", "python asyncio network", "robots sitemap feed", "uniqueterm4242", "market energy health science",
          "database vector memory latency throughput", "kernel compiler protocol"]
    for label, queries in [("English multi-word", qs)]:
        lat = []
        for _ in range(30):
            for q in queries:
                t = time.time(); s.search(q, 8, log=False); lat.append((time.time() - t) * 1000)
        print(f"SEARCH {label:18}: p50={statistics.median(lat):.1f}ms p95={pct(lat, .95):.1f}ms max={max(lat):.1f}ms over {len(lat)} queries on {c['chunks']} chunks")
    # CJK (trigram + LIKE fallback) par bhi
    zh = "异步编程是一种并发模型，事件循环在单个线程上协作地调度协程。" * 6
    db.x("BEGIN")
    for i in range(2000):
        t = zh + f" 独特词{i}。"
        db.upsert_doc(f"http://bench.test/zh{i}", f"中文{i}", None, None, "b", None, sha(t + str(i)), chunk_text(t))
    db.x("COMMIT")
    for label, q in [("CJK 4-char (trigram)", "异步编程"), ("CJK 2-char (LIKE scan)", "协程")]:
        lat = []
        for _ in range(20):
            t = time.time(); s.search(q, 8, log=False); lat.append((time.time() - t) * 1000)
        print(f"SEARCH {label:22}: p50={statistics.median(lat):.1f}ms p95={pct(lat, .95):.1f}ms on {db.counts()['chunks']} chunks")
    return cfg


def bench_crawl(n_pages=400):
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *a): pass
        def do_GET(self):
            p = self.path
            if p == "/robots.txt":
                body = b"User-agent: *\nAllow: /\n"; ct = "text/plain"
            elif p.startswith("/p/"):
                i = int(p[3:])
                links = "".join(f"<a href='/p/{(i * 7 + k) % n_pages}'>x</a>" for k in range(1, 6))
                paras = "".join(f"<p>{doc_text(i, 60)}</p>" for _ in range(8))
                body = f"<html><head><title>Page {i}</title></head><body><nav>menu</nav><article><h1>Page {i}</h1>{paras}</article>{links}</body></html>".encode()
                ct = "text/html; charset=utf-8"
            else:
                links = "".join(f"<a href='/p/{i}'>p{i}</a>" for i in range(0, n_pages, 1))
                body = f"<html><body><article><p>{doc_text(0, 80)}</p></article>{links}</body></html>".encode(); ct = "text/html"
            self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tmp = Path(tempfile.mkdtemp())
    cfg = make_cfg(tmp, crawler={"per_domain_delay": 0.0, "concurrency": 16, "max_depth": 1, "request_timeout": 10, "min_text_chars": 200})
    c = Crawler(cfg, [{"url": f"http://127.0.0.1:{srv.server_port}/", "type": "page"}])
    t0 = time.time()
    asyncio.run(c.run(once=True, max_pages=n_pages + 5))
    dt = time.time() - t0
    n = c.db.counts()["docs"]
    print(f"CRAWL (local, no network latency, 16 workers): {n} pages fetched+extracted+indexed in {dt:.1f}s = {n/dt:.0f} pages/s, db {c.db.size_mb():.1f} MB ({c.db.size_mb()/max(n,1)*1000:.0f} MB per 1000 pages)")
    srv.shutdown()


if __name__ == "__main__":
    bench_index_and_search(int(sys.argv[1]) if len(sys.argv) > 1 else 20000)
    bench_crawl()
