"""Multi-user: har user ki apni API key (database mein sirf hash), roles, aur rate limit."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from .db import DB


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_user(db: DB, name: str, role: str = "user", rate_per_min: int = 60) -> str:
    if role not in ("user", "admin"):
        raise ValueError("role must be user or admin")
    key = "ss_" + secrets.token_urlsafe(24)
    db.x("INSERT INTO users(name,key_hash,role,rate_per_min,created) VALUES(?,?,?,?,?)",
         (name, hash_key(key), role, rate_per_min, time.time()))
    return key  # sirf ek baar dikhai jati hai; DB mein dobara nahi milti


def authenticate(db: DB, key: str | None) -> dict[str, Any] | None:
    if not key:
        return None
    h = hash_key(key)
    for r in db.q("SELECT * FROM users WHERE key_hash=?", (h,)):
        if hmac.compare_digest(r["key_hash"], h):
            return dict(r)
    return None


def ensure_admin(db: DB, data_dir: str) -> str | None:
    """Pehli dafa admin key bana kar data/admin_key.txt mein rakh do. Baad mein None."""
    if db.q("SELECT 1 FROM users WHERE role='admin' LIMIT 1"):
        return None
    key = create_user(db, "admin", "admin", 600)
    p = Path(data_dir) / "admin_key.txt"
    p.write_text(key + "\n", encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return key


class RateLimiter:
    """Sliding window, per user/IP. Memory mein; restart par reset."""

    def __init__(self) -> None:
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, ident: str, per_min: int) -> bool:
        now = time.time()
        dq = self.hits[ident]
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= per_min:
            return False
        dq.append(now)
        if len(self.hits) > 20000:  # memory leak se bachao
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] > 120][:10000]:
                self.hits.pop(k, None)
        return True
