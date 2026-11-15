from __future__ import annotations

import time
from urllib import robotparser
from urllib.parse import urlsplit

import httpx

from .db import DB, domain_of


class Robots:
    """robots.txt ka ehtram: har domain ka rulebook cache hota hai (24h)."""

    def __init__(self, db: DB, client: httpx.AsyncClient, user_agent: str, enabled: bool = True):
        self.db, self.client, self.ua, self.enabled = db, client, user_agent, enabled
        self._parsers: dict[str, robotparser.RobotFileParser] = {}

    async def _load(self, url: str) -> robotparser.RobotFileParser:
        dom = domain_of(url)
        if dom in self._parsers:
            return self._parsers[dom]
        row = self.db.q("SELECT robots_txt, robots_at FROM domain_state WHERE domain=?", (dom,))
        text = None
        if row and row[0]["robots_at"] and time.time() - row[0]["robots_at"] < 86400:
            text = row[0]["robots_txt"] or ""
        else:
            s = urlsplit(url)
            try:
                r = await self.client.get(f"{s.scheme}://{s.netloc}/robots.txt", timeout=10)
                text = r.text if r.status_code == 200 else ""
                if r.status_code in (401, 403):
                    text = "User-agent: *\nDisallow: /"
            except Exception:
                text = ""
            self.db.x(
                "INSERT INTO domain_state(domain,robots_txt,robots_at) VALUES(?,?,?) "
                "ON CONFLICT(domain) DO UPDATE SET robots_txt=excluded.robots_txt, robots_at=excluded.robots_at",
                (dom, text, time.time()),
            )
        rp = robotparser.RobotFileParser()
        rp.parse(text.splitlines())
        self._parsers[dom] = rp
        return rp

    async def allowed(self, url: str) -> bool:
        if not self.enabled:
            return True
        rp = await self._load(url)
        return rp.can_fetch(self.ua, url)

    async def sitemaps(self, url: str) -> list[str]:
        if not self.enabled:
            return []
        rp = await self._load(url)
        return list(rp.site_maps() or [])

    async def crawl_delay(self, url: str) -> float | None:
        if not self.enabled:
            return None
        rp = await self._load(url)
        d = rp.crawl_delay(self.ua)
        return float(d) if d else None
