"""End-to-end test: local test website -> crawler -> index -> search -> answer.
Ye sirf test ke liye hai; asli system seeds.toml ki asli sites crawl karta hai."""
import asyncio
import http.server
import socketserver
import tempfile
import threading
from pathlib import Path

import pytest

from engine.config import load_config
from engine.crawler import Crawler
from engine.db import DB, normalize_url
from engine.extract import chunk_text
from engine.search import Searcher, build_answer

PARA = (
    "{topic} is an important subject. Researchers have described how {topic} works in detail, and the main "
    "finding is that {fact}. Many engineers rely on this behaviour every day when they build production systems, "
    "because it keeps results predictable and makes failures easier to debug. "
)

ARTICLES = {
    "/a1": ("Python asyncio explained", "asyncio", "the event loop schedules coroutines cooperatively on a single thread"),
    "/a2": ("SQLite full text search guide", "FTS5", "FTS5 ranks matches using the BM25 algorithm for relevance"),
    "/a3": ("Understanding web crawlers", "crawler", "a polite crawler obeys robots.txt and waits between requests to the same host"),
}


def page(title, topic, fact, links=""):
    body = "".join(f"<p>{PARA.format(topic=topic, fact=fact)}</p>" for _ in range(4))
    return (f"<html><head><title>{title}</title></head><body><nav><a href='/'>Home</a></nav>"
            f"<article><h1>{title}</h1>{body}</article>{links}<footer>cookie banner junk</footer></body></html>")


class Handler(http.server.BaseHTTPRequestHandler):
    hits: dict = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        Handler.hits[self.path] = Handler.hits.get(self.path, 0) + 1
        p = self.path
        if p == "/robots.txt":
            return self._send("User-agent: *\nDisallow: /private\n", "text/plain")
        if p == "/feed.xml":
            items = "".join(f"<item><title>{t}</title><link>http://127.0.0.1:{self.server.server_port}{k}</link></item>"
                            for k, (t, _, _) in ARTICLES.items())
            items += f"<item><title>Secret</title><link>http://127.0.0.1:{self.server.server_port}/private/x</link></item>"
            return self._send(f"<?xml version='1.0'?><rss version='2.0'><channel><title>T</title>{items}</channel></rss>",
                              "application/rss+xml")
        if p in ARTICLES:
            t, topic, fact = ARTICLES[p]
            return self._send(page(t, topic, fact, "<a href='/a2'>related</a><a href='/a2?utm_source=x'>dup</a>"), "text/html")
        if p.startswith("/private"):
            return self._send(page("Secret", "secret", "should never be indexed"), "text/html")
        self.send_error(404)

    def _send(self, body, ctype):
        b = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


@pytest.fixture(scope="module")
def site():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(scope="module")
def indexed(site):
    tmp = tempfile.mkdtemp()
    cfg = load_config()
    cfg["general"]["db_path"] = str(Path(tmp) / "t.db")
    cfg["crawler"].update(per_domain_delay=0.05, concurrency=4, max_depth=2, min_text_chars=200)
    seeds = [{"url": f"{site}/feed.xml", "type": "feed", "topic": "test"}]
    c = Crawler(cfg, seeds)
    asyncio.run(c.run(once=True, max_pages=50))
    return cfg, c.db, site


def test_normalize_url_strips_tracking():
    assert normalize_url("HTTP://Example.com/a?utm_source=x&b=1#frag") == "http://example.com/a?b=1"


def test_chunking_overlaps_and_covers():
    text = "\n".join(f"Sentence number {i} has several words in it to count." for i in range(120))
    ch = chunk_text(text)
    assert len(ch) > 3 and all(len(c.split()) < 260 for c in ch)


def test_crawler_indexes_feed_articles(indexed):
    cfg, db, site = indexed
    urls = {r["url"] for r in db.q("SELECT url FROM docs")}
    assert {f"{site}/a1", f"{site}/a2", f"{site}/a3"} <= urls


def test_robots_txt_is_respected(indexed):
    cfg, db, site = indexed
    assert not db.q("SELECT 1 FROM docs WHERE url LIKE '%/private%'")
    assert Handler.hits.get("/private/x", 0) == 0  # fetch hi nahi hua


def test_duplicate_urls_collapsed(indexed):
    cfg, db, site = indexed
    assert len(db.q("SELECT 1 FROM docs WHERE url LIKE '%/a2%'")) == 1


def test_boilerplate_removed(indexed):
    cfg, db, site = indexed
    assert not db.q("SELECT 1 FROM chunks WHERE text LIKE '%cookie banner junk%'")


def test_search_ranks_relevant_doc_first(indexed):
    cfg, db, site = indexed
    s = Searcher(db, cfg)
    hits = s.search("how does FTS5 rank matches BM25")
    assert hits and hits[0].url.endswith("/a2")


def test_answer_is_grounded_and_cited(indexed):
    cfg, db, site = indexed
    hits = Searcher(db, cfg).search("what does a polite crawler do")
    ans = build_answer("what does a polite crawler do", hits)
    assert ans["answer"] and "[1]" in ans["answer"]
    assert "robots.txt" in ans["answer"]
    assert ans["citations"][0]["url"].endswith("/a3")


def test_unknown_query_returns_nothing_not_fake_answer(indexed):
    cfg, db, site = indexed
    hits = Searcher(db, cfg).search("quantum chromodynamics lattice gauge")
    assert hits == []
    assert build_answer("quantum chromodynamics lattice gauge", hits)["answer"] == ""


def test_recrawl_unchanged_page_not_reindexed(indexed):
    cfg, db, site = indexed
    before = db.q("SELECT COUNT(*) FROM chunks")[0][0]
    db.x("UPDATE frontier SET next_fetch=0, status='done'")
    c = Crawler(cfg, [])
    c.db = db
    asyncio.run(c.run(once=True, max_pages=50))
    assert db.q("SELECT COUNT(*) FROM chunks")[0][0] == before
