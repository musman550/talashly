import asyncio
import time

import pytest

from engine.brain import Brain
from engine.crawler import Crawler
from engine.db import DB, normalize_url
from engine.search import Searcher
from tests.helpers import make_cfg
from tests.site import LANGS, Site


def test_sitemap_index_discovers_pages_nobody_links_to(crawled):
    cfg, db, site = crawled
    urls = {r["url"] for r in db.q("SELECT url FROM docs")}
    assert {f"{site.url}/ml/{c}" for c in LANGS} <= urls
    assert site.stats["/sitemap.xml"] >= 1 and site.stats["/sitemap-lang.xml"] >= 1


def test_robots_txt_blocks_private_and_never_fetches_it(crawled):
    cfg, db, site = crawled
    assert "/private/x" not in site.stats
    assert not db.q("SELECT 1 FROM chunks WHERE text LIKE '%must never be indexed%'")
    assert db.q("SELECT status FROM frontier WHERE url LIKE '%/private/x'")[0]["status"] == "blocked"


def test_boilerplate_is_stripped(crawled):
    cfg, db, site = crawled
    assert not db.q("SELECT 1 FROM chunks WHERE text LIKE '%cookie banner junk%' OR text LIKE '%menu menu%'")


@pytest.mark.parametrize("path,needle", [("/hang", "Timeout"), ("/huge", "too large"),
                                         ("/binary", "content-type"), ("/missing", "404")])
def test_bad_pages_are_recorded_not_fatal(crawled, path, needle):
    cfg, db, site = crawled
    r = db.q("SELECT status,last_error,fail_count FROM frontier WHERE url LIKE ?", (f"%{path}",))[0]
    assert r["status"] == "error" and needle in r["last_error"] and r["fail_count"] == 1
    assert not db.q("SELECT 1 FROM docs WHERE url LIKE ?", (f"%{path}",))


def test_redirect_is_followed(crawled):
    cfg, db, site = crawled
    hits = Searcher(db, cfg).search("redirectword")
    assert hits and hits[0].url.endswith("/redirect-me")


def test_external_domain_gets_a_vote_not_crawled(crawled):
    cfg, db, site = crawled
    assert db.q("SELECT votes FROM candidates WHERE domain='localhost'")[0]["votes"] >= 1
    assert "/ext" not in site.stats  # allowed domain nahi: fetch hi nahi hua


def test_every_crawled_doc_passes_integrity(crawled):
    cfg, db, site = crawled
    assert db.integrity()["ok"]
    assert db.counts()["docs"] >= 14


def test_per_domain_politeness_holds_under_concurrency(tmp_path):
    with Site() as site:
        cfg = make_cfg(tmp_path, crawler={"per_domain_delay": 0.3, "concurrency": 8, "max_depth": 0, "use_sitemaps": False})
        seeds = [{"url": f"{site.url}/ml/{c}", "type": "page"} for c in ["ur", "ar", "hi", "ru", "zh"]]
        t0 = time.time()
        asyncio.run(Crawler(cfg, seeds).run(once=True, max_pages=20))
        # 5 pages ek hi host par: kam az kam 4 gaps of 0.3s, chahe 8 workers hon
        assert time.time() - t0 >= 4 * 0.3 - 0.05


def test_conditional_get_adaptive_recrawl_and_change_detection(tmp_path):
    with Site() as site:
        cfg = make_cfg(tmp_path, crawler={"max_depth": 0, "use_sitemaps": False})
        seeds = [{"url": site.url + "/etag-page", "type": "page"}, {"url": site.url + "/changing", "type": "page"}]
        c = Crawler(cfg, seeds)
        asyncio.run(c.run(once=True))
        db = c.db
        assert db.counts()["docs"] == 2
        chunks1 = db.counts()["chunks"]
        iv1 = db.q("SELECT interval_s FROM frontier WHERE url LIKE '%etag-page'")[0]["interval_s"]
        assert Searcher(db, cfg).search("alphaversion")

        # 2nd run: ETag match -> server 304, kuch dobara index nahi hota, interval barh jata hai (kam badalne wala page)
        db.x("UPDATE frontier SET next_fetch=0, status='done'")
        asyncio.run(c.run(once=True))
        assert site.stats.get("304") == 1
        assert db.counts()["chunks"] == chunks1
        iv2 = db.q("SELECT interval_s FROM frontier WHERE url LIKE '%etag-page'")[0]["interval_s"]
        assert iv2 > iv1

        # 3rd run: content badla -> naya index, purana FTS mein baqi nahi
        site.state["version"] = "betaversion"
        db.x("UPDATE frontier SET next_fetch=0, status='done'")
        asyncio.run(c.run(once=True))
        s = Searcher(db, cfg)
        assert s.search("betaversion") and not s.search("alphaversion")
        assert db.integrity()["ok"]
        assert db.counts()["docs"] == 2


def test_backpressure_never_spawns_unbounded_tasks(tmp_path, monkeypatch):
    """Lakhon queued URLs (sitemap) hon to bhi tasks concurrency*3 se zyada nahi bante."""
    cfg = make_cfg(tmp_path, crawler={"concurrency": 5})
    c = Crawler(cfg, [])
    for i in range(3000):
        c.db.add_url(f"http://127.0.0.1:9/p{i}", 1, "page")
    peak = 0

    async def fake_process(self, row, fast=False):
        nonlocal peak
        peak = max(peak, len(asyncio.all_tasks()))
        await asyncio.sleep(0.3)  # asli fetch ki tarah slow: tasks jama hone ka mauqa
        self.db.x("UPDATE frontier SET status='done', next_fetch=? WHERE url=?", (time.time() + 9999, row["url"]))
        return True

    monkeypatch.setattr(Crawler, "process", fake_process)
    asyncio.run(c.run(once=True, max_pages=120))
    assert peak < 5 * 3 + 12, f"tasks ka peak {peak}: backpressure kaam nahi kar raha"


def test_expand_domains_filters_junk_and_private_hosts(tmp_path):
    cfg = make_cfg(tmp_path)
    db = DB(cfg["general"]["db_path"])
    brain = Brain(cfg, db, Searcher(db, cfg), [])
    for d in ["good-news-site.org", "localhost", "10.0.0.5", "192.168.1.9", "facebook.com", "m.facebook.com",
              "169.254.169.254", "nas.local", "rare-site.net"]:
        for _ in range(5 if d != "rare-site.net" else 2):
            db.vote_candidate(d, f"http://{d}/x")
    added = brain.expand_domains()
    assert added == ["good-news-site.org"]
    allowed = {r["domain"] for r in db.q("SELECT domain FROM allowed_domains")}
    assert allowed == {"good-news-site.org"}  # private/junk/kam-votes wale sab bahar


def test_expand_respects_max_domains(tmp_path):
    cfg = make_cfg(tmp_path, brain={"max_domains": 2})
    db = DB(cfg["general"]["db_path"])
    brain = Brain(cfg, db, Searcher(db, cfg), [])
    for d in ["a-site.org", "b-site.org", "c-site.org", "d-site.org"]:
        for _ in range(6):
            db.vote_candidate(d, f"http://{d}/")
    assert len(brain.expand_domains()) == 2
    assert brain.expand_domains() == []  # hadd poori


def test_url_normalization_edge_cases():
    n = normalize_url
    assert n("HTTP://EXAMPLE.com:80/a?b=2&a=1&utm_source=x&fbclid=y#frag") == "http://example.com/a?a=1&b=2"
    assert n("https://example.com") == "https://example.com/"
    assert n("https://example.com:8443/x") == "https://example.com:8443/x"
    assert n("mailto:a@b.com") == "mailto:a@b.com"
    assert n("https://例え.jp/パス?q=日本") .startswith("https://")


def test_use_sitemaps_false_means_seeds_only(tmp_path):
    with Site() as site:
        cfg = make_cfg(tmp_path, crawler={"max_depth": 0, "use_sitemaps": False})
        c = Crawler(cfg, [{"url": site.url + "/ml/ur", "type": "page"}])
        asyncio.run(c.run(once=True))
        assert [r["url"] for r in c.db.q("SELECT url FROM docs")] == [site.url + "/ml/ur"]
        assert "/sitemap.xml" not in site.stats
