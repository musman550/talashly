from __future__ import annotations

import json
from pathlib import Path

from engine.config import load_config
from engine.db import sha
from engine.extract import chunk_text


def make_cfg(tmp: Path, **sections) -> dict:
    cfg = load_config()
    cfg["general"]["db_path"] = str(tmp / "t.db")
    cfg["_data_dir"] = str(tmp)
    cfg["crawler"].update(per_domain_delay=0.02, concurrency=6, min_text_chars=200, request_timeout=1,
                          max_page_bytes=100_000, max_depth=2)
    for sec, vals in sections.items():
        cfg[sec].update(vals)
    return cfg


def seed_docs(db, docs) -> None:
    """docs: [(url, title, text)] seedha index mein (crawl ke bina)."""
    for url, title, text in docs:
        db.upsert_doc(url, title, None, None, "t", None, sha(text), chunk_text(text))


async def collect(brain, q: str, role: str = "user", deep: bool = False) -> dict:
    out: dict = {"events": [], "text": "", "steps": [], "sources": [], "code": [], "plan": None, "done": None}
    async for ev, data in brain.run(q, role, deep=deep, stream_delay=0):
        out["events"].append(ev)
        if ev == "token":
            out["text"] += data
        elif ev == "replace":
            out["text"] = data
        elif ev == "step":
            out["steps"].append(data["msg"])
        elif ev in ("sources", "code", "plan", "done"):
            out[ev] = data
    return out


def parse_sse(text: str) -> list[tuple[str, object]]:
    evs = []
    for block in text.strip().split("\n\n"):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            evs.append((name, data))
    return evs
