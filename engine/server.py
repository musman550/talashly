from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .brain import Brain
from .config import ROOT, load_config, load_seeds, load_sources
from .db import DB, domain_of, normalize_url
from .llm import llm_alive
from .search import Searcher, snippet, terms
from .supervisor import Supervisor
from .users import RateLimiter, authenticate, create_user, ensure_admin

WEB = Path(ROOT) / "web"


# NOTE: request-body models module level par hone chahiye (create_app ke andar hon to FastAPI unhe query param samajhta hai)
class TaskIn(BaseModel):
    kind: str = Field(pattern="^(research|crawl)$")
    q: str | None = Field(default=None, max_length=500)
    url: str | None = None


class SeedIn(BaseModel):
    url: str
    type: str = Field(default="page", pattern="^(page|feed|sitemap)$")
    topic: str | None = None


class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    role: str = Field(default="user", pattern="^(user|admin)$")
    rate_per_min: int = Field(default=60, ge=1, le=100000)


class CodeIn(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=20)


def _sse(name: str, data: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(cfg: dict | None = None, seeds: list[dict] | None = None, sources: list[dict] | None = None,
               crawl: bool | None = None) -> FastAPI:
    cfg = cfg or load_config()
    db = DB(cfg["general"]["db_path"])
    searcher = Searcher(db, cfg)
    brain = Brain(cfg, db, searcher, sources if sources is not None else load_sources())
    crawl = (os.environ.get("SS_CRAWL", "1") == "1") if crawl is None else crawl
    sup = Supervisor(cfg, db, brain, seeds if seeds is not None else load_seeds(), crawl)
    limiter = RateLimiter()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        key = ensure_admin(db, cfg["_data_dir"])
        if key:
            print(f"\n[!] Admin API key bana di gayi: {Path(cfg['_data_dir']) / 'admin_key.txt'}\n")
        await sup.start()
        if cfg["code"]["paths"]:
            asyncio.get_running_loop().run_in_executor(None, brain._index_code, cfg["code"]["paths"])
        yield
        await sup.stop()

    app = FastAPI(title="Talashly", lifespan=lifespan)
    app.state.db, app.state.brain, app.state.sup, app.state.cfg = db, brain, sup, cfg

    # ---------------------------------------------------------------- auth
    async def current_user(request: Request, x_api_key: str = Header(default=""), key: str = "") -> dict:
        k = x_api_key or key
        if k:
            u = authenticate(db, k)
            if not u:
                raise HTTPException(401, "invalid api key")
        elif cfg["server"]["require_key"]:
            raise HTTPException(401, "api key required")
        else:
            u = {"id": 0, "name": "anonymous", "role": "user", "rate_per_min": cfg["server"]["rate_per_min"]}
        ident = f"u{u['id']}" if u["id"] else f"ip:{request.client.host if request.client else '?'}"
        if not limiter.allow(ident, u["rate_per_min"]):
            raise HTTPException(429, "rate limit: thora ruk kar dobara koshish karein")
        return u

    async def admin_user(u: dict = Depends(current_user)) -> dict:
        if u["role"] != "admin":
            raise HTTPException(403, "admin only")
        return u

    # ---------------------------------------------------------------- public API
    @app.get("/api/search")
    async def api_search(q: str = Query(min_length=1, max_length=500), limit: int = 8, domain: str | None = None,
                         u: dict = Depends(current_user)):
        t0 = time.time()
        hits = searcher.search(q, max(1, min(limit, 30)), domain)
        ts = terms(q)
        return {"query": q, "took_ms": round((time.time() - t0) * 1000, 1),
                "results": [{"title": h.title, "url": h.url, "domain": h.domain, "snippet": snippet(h.passages[0], ts),
                             "published_at": h.published_at, "fetched_at": h.fetched_at, "score": round(h.score, 3)}
                            for h in hits]}

    @app.get("/api/code")
    async def api_code(q: str = Query(min_length=1, max_length=300), lang: str | None = None, limit: int = 10,
                       u: dict = Depends(current_user)):
        hits = searcher.search_code(q, lang, max(1, min(limit, 30)))
        return {"query": q, "results": [{"path": h.path, "lang": h.lang, "start": h.start, "end": h.end,
                                         "text": h.text, "score": round(h.score, 3)} for h in hits]}

    @app.get("/api/answer")
    async def api_answer(q: str = Query(min_length=1, max_length=500), deep: bool = False,
                         u: dict = Depends(current_user)):
        async def gen():
            try:
                async for ev, data in brain.run(q, u["role"], deep=deep):
                    yield _sse(ev, data)
            except Exception as e:  # noqa: BLE001  brain mein bug ho to bhi user ko saaf jawab, aur log mein record
                db.log("error", f"answer failed {type(e).__name__}: {e}")
                yield _sse("token", "Andar koi masla aaya; maine record kar liya hai. Dobara koshish karein.")
                yield _sse("done", {"mode": "error"})
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/tasks")
    async def create_task(t: TaskIn, u: dict = Depends(current_user)):
        if t.kind == "crawl" and u["role"] != "admin":
            raise HTTPException(403, "crawl task admin only")
        if t.kind == "research" and not t.q:
            raise HTTPException(422, "q required")
        if t.kind == "crawl" and not t.url:
            raise HTTPException(422, "url required")
        pending = db.q("SELECT COUNT(*) FROM tasks WHERE user_id=? AND status IN ('queued','running')", (u["id"],))[0][0]
        if pending >= 20:
            raise HTTPException(429, "bahut zyada pending tasks")
        cur = db.x("INSERT INTO tasks(user_id,kind,payload,created) VALUES(?,?,?,?)",
                   (u["id"], t.kind, json.dumps({"q": t.q, "url": t.url}, ensure_ascii=False), time.time()))
        return {"id": cur.lastrowid, "status": "queued"}

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: int, u: dict = Depends(current_user)):
        r = db.q("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not r or (r[0]["user_id"] != u["id"] and u["role"] != "admin"):
            raise HTTPException(404, "task not found")
        r = r[0]
        return {"id": r["id"], "kind": r["kind"], "status": r["status"],
                "result": json.loads(r["result"]) if r["result"] else None,
                "created": r["created"], "finished": r["finished"]}

    @app.get("/api/stats")
    async def api_stats():
        c = db.counts()
        st = {r["k"]: r["v"] for r in db.q("SELECT k,v FROM stats")}
        last = db.q("SELECT MAX(fetched_at) FROM docs")[0][0]
        top = [dict(r) for r in db.q("SELECT domain, COUNT(*) n FROM docs GROUP BY domain ORDER BY n DESC LIMIT 10")]
        running = bool(sup.crawler and sup.crawler.running)
        return {"index": c, "crawler": st, "last_indexed_at": last, "top_domains": top, "crawler_running": running}

    @app.get("/api/health")
    async def api_health():
        beat = db.get_stat("heartbeat", 0)
        c = db.counts()
        llm_ok = await llm_alive(cfg["llm"]["base_url"]) if cfg["llm"]["enabled"] else None
        return {
            "status": "ok", "uptime_s": round(time.time() - sup.started),
            "crawler": {"enabled": sup.do_crawl, "running": bool(sup.crawler and sup.crawler.running),
                        "heartbeat_age_s": round(time.time() - beat) if beat else None, "restarts": sup.restarts},
            "index": {"docs": c["docs"], "chunks": c["chunks"], "size_mb": round(db.size_mb(), 1)},
            "llm": {"enabled": cfg["llm"]["enabled"], "alive": llm_ok},
            "features": {"trigram_cjk": db.has_trigram, "code_search": c["code_chunks"] > 0},
            "config_problems_fixed": cfg.get("_problems", []),
        }

    # ---------------------------------------------------------------- admin API
    @app.post("/admin/seed")
    async def admin_seed(s: SeedIn, a: dict = Depends(admin_user)):
        url = normalize_url(s.url)
        if not url.startswith("http"):
            raise HTTPException(422, "valid http(s) url chahiye")
        db.x("INSERT OR IGNORE INTO allowed_domains(domain) VALUES(?)", (domain_of(url),))
        return {"added": db.add_url(url, 0, s.type, s.topic), "url": url}

    @app.post("/admin/users")
    async def admin_create_user(body: UserIn, a: dict = Depends(admin_user)):
        try:
            key = create_user(db, body.name, body.role, body.rate_per_min)
        except Exception:  # noqa: BLE001  name pehle se maujood
            raise HTTPException(409, "user already exists")
        return {"name": body.name, "role": body.role, "api_key": key,
                "note": "Ye key sirf abhi dikhai ja rahi hai; save kar lein."}

    @app.get("/admin/users")
    async def admin_users(a: dict = Depends(admin_user)):
        return [dict(r) for r in db.q("SELECT id,name,role,rate_per_min,created FROM users ORDER BY id")]

    @app.get("/admin/brain-log")
    async def admin_log(limit: int = 50, a: dict = Depends(admin_user)):
        return [dict(r) for r in db.q("SELECT at,kind,detail FROM brain_log ORDER BY id DESC LIMIT ?", (min(limit, 500),))]

    @app.get("/admin/gaps")
    async def admin_gaps(a: dict = Depends(admin_user)):
        return [dict(r) for r in db.q("SELECT q,n,tries,resolved FROM gaps ORDER BY resolved, n DESC LIMIT 100")]

    @app.get("/admin/frontier")
    async def admin_frontier(status: str = "error", limit: int = 50, a: dict = Depends(admin_user)):
        return [dict(r) for r in db.q(
            "SELECT url,status,fail_count,last_error,next_fetch FROM frontier WHERE status=? LIMIT ?", (status, limit))]

    @app.post("/admin/index-code")
    async def admin_index_code(body: CodeIn, a: dict = Depends(admin_user)):
        return await asyncio.to_thread(brain._index_code, body.paths)

    @app.post("/admin/heal")
    async def admin_heal(a: dict = Depends(admin_user)):
        return {"result": await sup.heal_now()}

    @app.get("/")
    async def home():
        return FileResponse(WEB / "index.html")

    return app


def run() -> None:
    from .__main__ import main

    main(["serve"])


if __name__ == "__main__":
    run()
