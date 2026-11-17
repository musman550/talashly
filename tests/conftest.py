"""Tests ko config.toml ke crawler hours (din ke hisaab se) par depend nahi hona chahiye: warna din ke waqt ke mutabiq
crawler wale tests atak jate hain. Is liye load_config() hamesha 24-ghante active hours deta hai.
(Ye block sab imports se PEHLE hona zaroori hai, kyunke helpers `from engine.config import load_config` karte hain.)"""
import engine.config as _cfg

_orig_load = _cfg.load_config


def _load_config_all_day(*a, **k):
    cfg = _orig_load(*a, **k)
    cfg["crawler"]["active_hours"] = ["00:00-24:00"]
    return cfg


_cfg.load_config = _load_config_all_day

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
