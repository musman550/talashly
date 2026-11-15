import asyncio

import pytest

from engine.crawler import Crawler
from tests.helpers import make_cfg
from tests.site import Site


@pytest.fixture(scope="session")
def crawled(tmp_path_factory):
    """Poori nakli website ek dafa crawl (sirf parhne ke liye; mutation wale tests apna alag crawl karte hain)."""
    tmp = tmp_path_factory.mktemp("site")
    with Site() as site:
        cfg = make_cfg(tmp)
        c = Crawler(cfg, [{"url": site.url + "/", "type": "page", "topic": "test"}])
        asyncio.run(c.run(once=True, max_pages=300))
        yield cfg, c.db, site
