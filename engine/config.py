from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# Har setting ka safe default: config.toml mein koi cheez ghalat/gum ho to system gir nahi jata.
DEFAULTS: dict[str, Any] = {
    "general": {"db_path": "data/index.db", "user_agent": "TalashlyBot/2.0 (polite crawler)"},
    "crawler": {
        "concurrency": 8, "per_domain_delay": 2.0, "request_timeout": 20, "max_page_bytes": 2_000_000,
        "max_depth": 2, "max_pages_per_domain": 2000, "respect_robots": True, "min_text_chars": 400, "use_sitemaps": True,
        "active_hours": ["00:00-24:00"], "timezone_offset_hours": 5,
    },
    "recrawl": {"news_minutes": 30, "default_hours": 24, "max_hours": 168},
    "search": {"default_limit": 8, "recency_half_life_days": 30},
    "llm": {"enabled": False, "base_url": "http://127.0.0.1:8081"},
    "server": {"host": "127.0.0.1", "port": 8000, "require_key": False, "workers": 2, "rate_per_min": 60,
               "cors": True},
    "site": {"name": "Talashly", "url": "http://127.0.0.1:8000"},
    # chat: max_concurrent = ek waqt mein kitne jawab ban sakte hain; queue_max = intezar mein kitne; 0 quota = unlimited
    "chat": {"max_concurrent": 16, "queue_max": 200, "max_message_chars": 4000, "max_convs_per_owner": 100,
             "max_msgs_per_conv": 400, "anon_rate_per_min": 20, "key_rate_per_min": 60, "daily_quota_anon": 200,
             "daily_quota_key": 5000, "signup_enabled": True, "signup_per_hour_per_ip": 5},
    "brain": {
        "live_research": True, "live_timeout_s": 12, "live_fetch_max": 6, "weak_coverage": 0.6,
        "auto_expand": True, "expand_votes": 5, "max_domains": 300, "gap_retries": 3,
    },
    "maintenance": {"max_db_mb": 2048, "backup_keep": 7, "check_every_s": 600, "stuck_after_s": 300},
    "code": {"paths": [], "max_file_kb": 500},
}

# (section, key): (min, max)  -> galat value ho to clamp ho jati hai
LIMITS = {
    ("crawler", "concurrency"): (1, 64), ("crawler", "per_domain_delay"): (0.0, 120.0),
    ("crawler", "request_timeout"): (3, 120), ("crawler", "max_depth"): (0, 10),
    ("server", "port"): (1, 65535), ("server", "workers"): (1, 16),
    ("brain", "live_timeout_s"): (2, 60), ("brain", "live_fetch_max"): (0, 20),
    ("brain", "weak_coverage"): (0.1, 1.0),
    ("chat", "max_concurrent"): (1, 256), ("chat", "queue_max"): (0, 10000), ("chat", "max_message_chars"): (50, 20000),
    ("chat", "anon_rate_per_min"): (1, 100000), ("chat", "key_rate_per_min"): (1, 100000),
    ("chat", "daily_quota_anon"): (0, 10_000_000), ("chat", "daily_quota_key"): (0, 10_000_000),
    ("chat", "signup_per_hour_per_ip"): (1, 10000), ("chat", "max_convs_per_owner"): (1, 10000),
    ("chat", "max_msgs_per_conv"): (2, 100000),
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def validate(cfg: dict) -> list[str]:
    """Ghalat values ko theek karta hai; kya badla uski list wapas deta hai."""
    fixed: list[str] = []
    for (sec, key), (lo, hi) in LIMITS.items():
        v = cfg[sec].get(key)
        d = DEFAULTS[sec][key]
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            cfg[sec][key] = d
            fixed.append(f"{sec}.{key}: bad type -> {d}")
        elif not lo <= v <= hi:
            cfg[sec][key] = min(max(v, lo), hi)
            fixed.append(f"{sec}.{key}: {v} -> {cfg[sec][key]}")
    if not isinstance(cfg["crawler"]["active_hours"], list) or not cfg["crawler"]["active_hours"]:
        cfg["crawler"]["active_hours"] = ["00:00-24:00"]
        fixed.append("crawler.active_hours reset")
    return fixed


def load_config(path: str | None = None) -> dict[str, Any]:
    p = Path(path or os.environ.get("SS_CONFIG", ROOT / "config.toml"))
    user: dict = {}
    problem = None
    if p.exists():
        try:
            with open(p, "rb") as f:
                user = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:  # file kharab: defaults par chalo, system na rukay
            problem = f"config.toml parse error ({e}); defaults use ho rahe hain"
    cfg = _merge(DEFAULTS, user)
    if os.environ.get("SS_SITE_URL", "").startswith(("http://", "https://")):  # hosting par public address env se
        cfg["site"]["url"] = os.environ["SS_SITE_URL"].strip()
    cfg["_problems"] = ([problem] if problem else []) + validate(cfg)
    db = Path(cfg["general"]["db_path"])
    if not db.is_absolute():
        db = ROOT / db
    db.parent.mkdir(parents=True, exist_ok=True)
    cfg["general"]["db_path"] = str(db)
    cfg["_data_dir"] = str(db.parent)
    return cfg


def _load_list(env: str, default: str, key: str) -> list[dict[str, Any]]:
    p = Path(os.environ.get(env, ROOT / default))
    if not p.exists():
        return []
    try:
        with open(p, "rb") as f:
            return tomllib.load(f).get(key, [])
    except tomllib.TOMLDecodeError:
        return []


def load_seeds(path: str | None = None) -> list[dict[str, Any]]:
    if path:
        os.environ["SS_SEEDS"] = path
    return _load_list("SS_SEEDS", "seeds.toml", "seed")


def load_sources() -> list[dict[str, Any]]:
    return _load_list("SS_SOURCES", "sources.toml", "source")
