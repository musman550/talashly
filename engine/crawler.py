from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import feedparser
import httpx

from .config import load_config, load_seeds
from .db import DB, domain_of, normalize_url, sha
from .extract import chunk_text, extract_page
from .robots import Robots

log = logging.getLogger("crawler")


def in_active_hours(windows: list[str], tz_offset: float) -> bool:
    now = datetime.now(timezone.utc) + timedelta(hours=tz_offset)
    cur = now.hour * 60 + now.minute
    for w in windows:
        try:
            a, b = w.split("-")
            ah, am = map(int, a.split(":"))
            bh, bm = map(int, b.split(":"))
        except ValueError:
            continue
        if ah * 60 + am <= cur < bh * 60 + bm:
            return True
    return False


class Crawler:
    def __init__(self, cfg: dict | None = None, seeds: list[dict] | None = None):
        self.cfg = cfg or load_config()
        self.seeds = seeds if seeds is not None else load_seeds()
        self.c = self.cfg["crawler"]
        self.db = DB(self.cfg["general"]["db_path"])
        self.db.x("CREATE TABLE IF NOT EXISTS allowed_domains(domain TEXT PRIMARY KEY)")
        self.ua = self.cfg["general"]["user_agent"]
        self.client: httpx.AsyncClient | None = None
        self.robots: Robots | None = None
        self._own_client = False
        self._dom_lock: dict[str, asyncio.Lock] = {}
        self._sitemap_done: set[str] = set()
        self._stop = asyncio.Event()
        self.running = False

    # ---------- lifecycle ----------
    async def _open(self) -> None:
        if self.client is not None:
            return
        limits = httpx.Limits(max_connections=self.c["concurrency"] * 2 + 4)
        self.client = httpx.AsyncClient(follow_redirects=True, timeout=self.c["request_timeout"],
                                        limits=limits, headers={"User-Agent": self.ua})
        self.robots = Robots(self.db, self.client, self.ua, self.c["respect_robots"])
        self._own_client = True

    async def aclose(self) -> None:
        if self.client is not None and self._own_client:
            await self.client.aclose()
        self.client, self.robots, self._own_client = None, None, False

    # ---------- seeds / domains ----------
    def allow_domain(self, domain: str) -> None:
        self.db.x("INSERT OR IGNORE INTO allowed_domains(domain) VALUES(?)", (domain,))

    def load_seeds_into_frontier(self) -> int:
        n = 0
        for s in self.seeds:
            url = normalize_url(s["url"])
            self.allow_domain(domain_of(url))
            kind = s.get("type", "page")
            interval = self.cfg["recrawl"]["news_minutes"] * 60 if kind == "feed" \
                else self.cfg["recrawl"]["default_hours"] * 3600
            if self.db.add_url(url, 0, kind, s.get("topic"), interval_s=interval):
                n += 1
        return n

    def allowed_domains(self) -> set[str]:
        return {r["domain"] for r in self.db.q("SELECT domain FROM allowed_domains")}

    # ---------- politeness ----------
    async def _wait_turn(self, url: str, fast: bool = False) -> None:
        dom = domain_of(url)
        lock = self._dom_lock.setdefault(dom, asyncio.Lock())
        async with lock:
            row = self.db.q("SELECT next_allowed FROM domain_state WHERE domain=?", (dom,))
            nxt = row[0]["next_allowed"] if row else 0
            delay = min(self.c["per_domain_delay"], 0.6) if fast else self.c["per_domain_delay"]
            rd = await self.robots.crawl_delay(url)  # type: ignore[union-attr]
            if rd:
                delay = max(delay, rd)
            now = time.time()
            wait = max(0.0, nxt - now)
            self.db.x(
                "INSERT INTO domain_state(domain,next_allowed) VALUES(?,?) "
                "ON CONFLICT(domain) DO UPDATE SET next_allowed=excluded.next_allowed",
                (dom, max(now, nxt) + delay),
            )
        if wait:
            await asyncio.sleep(wait)

    async def _discover_sitemaps(self, url: str) -> None:
        """Naye domain ki robots.txt se Sitemap: lines dhoondo aur frontier mein daalo (unlimited pages ka raasta)."""
        dom = domain_of(url)
        if not self.c.get("use_sitemaps", True) or dom in self._sitemap_done or not self.robots:
            return
        self._sitemap_done.add(dom)
        try:
            for sm in (await self.robots.sitemaps(url))[:5]:
                self.db.add_url(sm, 1, "sitemap", None, interval_s=86400)
        except Exception as e:  # noqa: BLE001
            log.debug("sitemap discovery failed for %s: %s", dom, e)

    # ---------- one URL ----------
    async def process(self, row, fast: bool = False) -> bool:
        """True agar page asal mein fetch hua (error/blocked nahi)."""
        url, kind = row["url"], row["kind"]
        db = self.db
        try:
            if not await self.robots.allowed(url):  # type: ignore[union-attr]
                db.x("UPDATE frontier SET status='blocked', last_error='robots.txt', next_fetch=? WHERE url=?",
                     (time.time() + 7 * 86400, url))
                db.bump("blocked")
                return False
            await self._discover_sitemaps(url)
            dom_pages = db.q("SELECT pages FROM domain_state WHERE domain=?", (row["domain"],))
            if kind == "page" and dom_pages and dom_pages[0]["pages"] >= self.c["max_pages_per_domain"] \
                    and not db.q("SELECT 1 FROM docs WHERE url=?", (url,)):
                db.x("UPDATE frontier SET status='done', next_fetch=? WHERE url=?", (time.time() + 30 * 86400, url))
                return False

            await self._wait_turn(url, fast)
            headers = {"User-Agent": self.ua,
                       "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5"}
            if row["etag"]:
                headers["If-None-Match"] = row["etag"]
            if row["last_modified"]:
                headers["If-Modified-Since"] = row["last_modified"]
            r = await self.client.get(url, headers=headers)  # type: ignore[union-attr]
            now = time.time()
            db.bump("fetched")

            if r.status_code == 304:
                self._reschedule(row, changed=False)
                return True
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}")
            if len(r.content) > self.c["max_page_bytes"]:
                raise RuntimeError("too large")

            etag, lm = r.headers.get("etag"), r.headers.get("last-modified")
            ctype = r.headers.get("content-type", "").lower()
            body = r.text

            if kind == "feed":
                changed = self._handle_feed(row, body)
            elif kind == "sitemap":
                changed = self._handle_sitemap(row, body)
            else:
                if "html" not in ctype and "text" not in ctype:
                    raise RuntimeError(f"skip content-type {ctype}")
                changed = self._handle_page(row, body)

            db.x("UPDATE frontier SET etag=?, last_modified=?, last_fetch=?, fail_count=0, last_error=NULL WHERE url=?",
                 (etag, lm, now, url))
            self._reschedule(row, changed)
            return True
        except Exception as e:  # network error, timeout, parse error...
            self._mark_error(row, f"{type(e).__name__}: {e}".rstrip(": ") or type(e).__name__)
            log.warning("fail %s: %s", url, e or type(e).__name__)
            return False

    def _mark_error(self, row, msg: str) -> None:
        fc = row["fail_count"] + 1
        backoff = min(6 * 3600, 60 * (2 ** fc))
        self.db.x("UPDATE frontier SET status='error', fail_count=?, last_error=?, next_fetch=? WHERE url=?",
                  (fc, msg[:200], time.time() + backoff, row["url"]))
        self.db.bump("errors")

    def _reschedule(self, row, changed: bool) -> None:
        rc = self.cfg["recrawl"]
        base = row["interval_s"] or rc["default_hours"] * 3600
        if row["kind"] == "feed":
            new = rc["news_minutes"] * 60
        else:
            new = max(base / 2, 1800) if changed else min(base * 1.6, rc["max_hours"] * 3600)
        self.db.x("UPDATE frontier SET status='done', interval_s=?, next_fetch=? WHERE url=?",
                  (new, time.time() + new, row["url"]))

    # ---------- handlers ----------
    def _handle_page(self, row, html: str) -> bool:
        url = row["url"]
        ex = extract_page(html, url)
        if not ex or len(ex.text) < self.c["min_text_chars"]:
            self.db.bump("thin_skipped")
            return False
        chunks = chunk_text(ex.text)
        h = sha(ex.text)
        _, changed = self.db.upsert_doc(url, ex.title, ex.author, ex.lang, row["topic"], ex.published_at, h, chunks)
        if changed:
            self.db.bump("indexed")
            self.db.x(
                "INSERT INTO domain_state(domain,pages) VALUES(?,1) "
                "ON CONFLICT(domain) DO UPDATE SET pages=pages+1", (row["domain"],))
        allowed = self.allowed_domains()
        for link in ex.links:
            d = domain_of(link)
            if d in allowed:
                if row["depth"] < self.c["max_depth"]:
                    self.db.add_url(link, row["depth"] + 1, "page", row["topic"])
            elif d:
                self.db.vote_candidate(d, link)  # bahar ki site: vote (brain tay karega add karna hai ya nahi)
        return changed

    def _handle_feed(self, row, body: str) -> bool:
        parsed = feedparser.parse(body)
        new = 0
        for e in parsed.entries[:60]:
            link = e.get("link")
            if not link:
                continue
            link = normalize_url(link)
            self.allow_domain(domain_of(link))  # feed jo article publish karta hai wo trusted
            if self.db.add_url(link, 1, "page", row["topic"], interval_s=7 * 86400):
                new += 1
        return new > 0

    def _handle_sitemap(self, row, body: str) -> bool:
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return False
        ns = re.match(r"\{.*\}", root.tag)
        pre = ns.group(0) if ns else ""
        new = 0
        for loc in root.iter(f"{pre}loc"):
            u = (loc.text or "").strip()
            if not u:
                continue
            kind = "sitemap" if u.endswith(".xml") or u.endswith(".xml.gz") else "page"
            if kind == "sitemap" and u.endswith(".gz"):
                continue
            if self.db.add_url(u, 1, kind, row["topic"]):
                new += 1
            if new >= 5000:
                break
        return new > 0

    # ---------- on-demand (brain ke liye) ----------
    async def fetch_now(self, url: str, topic: str | None = None, depth: int = 1) -> bool:
        """Foran ek URL fetch+index karo (user ke sawal ke dauran). Politeness aur robots phir bhi lagti hain."""
        await self._open()
        url = normalize_url(url)
        self.allow_domain(domain_of(url))
        self.db.add_url(url, depth, "page", topic)
        rows = self.db.q("SELECT * FROM frontier WHERE url=?", (url,))
        if not rows:
            return False
        self.db.x("UPDATE frontier SET next_fetch=? WHERE url=?", (time.time() + 600, url))
        return await self.process(rows[0], fast=True)

    # ---------- main loop ----------
    async def run(self, once: bool = False, max_pages: int | None = None) -> None:
        self.load_seeds_into_frontier()
        # pichli run/crash mein jo URLs "in-flight" mark thay, unhe wapas queue mein daalo
        self.db.x("UPDATE frontier SET next_fetch=0 WHERE status='queued' AND next_fetch>?", (time.time(),))
        await self._open()
        sem = asyncio.Semaphore(self.c["concurrency"])
        tasks: set[asyncio.Task] = set()
        processed = 0
        self.running = True

        hard_timeout = self.c["request_timeout"] * 3 + 30

        async def worker(r):
            async with sem:
                try:  # koi bhi fetch hamesha ke liye atak nahi sakta
                    await asyncio.wait_for(self.process(r), timeout=hard_timeout)
                except asyncio.TimeoutError:
                    self._mark_error(r, "hard timeout (atka hua fetch cancel kiya)")

        log.info("crawler started (concurrency=%s)", self.c["concurrency"])
        try:
            while not self._stop.is_set():
                self.db.set_stat("heartbeat", time.time())
                if not in_active_hours(self.c["active_hours"], self.c["timezone_offset_hours"]):
                    await asyncio.sleep(30)
                    continue
                room = self.c["concurrency"] * 3 - len(tasks)
                if room <= 0:  # backpressure: lakhon URLs ek saath task na ban jayen
                    await asyncio.sleep(0.2)
                    continue
                batch = self.db.due(room)
                inflight = {getattr(t, "url", None) for t in tasks}
                batch = [r for r in batch if r["url"] not in inflight]
                if not batch and not tasks:
                    if once:
                        break
                    await asyncio.sleep(5)
                    continue
                for r in batch:
                    if max_pages is not None and processed >= max_pages:
                        break
                    self.db.x("UPDATE frontier SET next_fetch=? WHERE url=?", (time.time() + 600, r["url"]))
                    t = asyncio.create_task(worker(r))
                    t.url = r["url"]  # type: ignore[attr-defined]
                    tasks.add(t)
                    t.add_done_callback(tasks.discard)
                    processed += 1
                if max_pages is not None and processed >= max_pages and not tasks:
                    break
                await asyncio.sleep(0.3)
        finally:
            self.running = False
            for t in list(tasks):
                t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self.aclose()

    def stop(self) -> None:
        self._stop.set()


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Talashly crawler")
    ap.add_argument("--once", action="store_true", help="queue khatam hone tak chalao phir ruk jao")
    ap.add_argument("--max-pages", type=int, default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    c = Crawler()
    try:
        asyncio.run(c.run(once=a.once, max_pages=a.max_pages))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
