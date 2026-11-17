from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import re
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .brain import Brain
from .chat import CLIENT_ID, Chat, format_with_sources
from .config import ROOT, load_config, load_seeds, load_sources
from .db import DB, domain_of, normalize_url
from .lang import tr
from .llm import llm_alive
from .models import MODELS, get_model
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


class HistMsg(BaseModel):
    role: str = Field(max_length=12)
    content: str = Field(max_length=4000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    conversation_id: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    private: bool = False                                          # True: server par kuch save nahi hota
    history: list[HistMsg] | None = Field(default=None, max_length=12)  # private mode mein browser bhejta hai


class KeyIn(BaseModel):
    label: str = Field(default="", max_length=40)


class OAIChat(BaseModel):
    model_config = ConfigDict(extra="allow")  # temperature, max_tokens waghera ignore, error nahi
    model: str | None = None
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    stream: bool = False


def _oai_error(status: int, message: str, code: str, typ: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": typ, "code": code}}, status_code=status)


def _msg_text(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):  # OpenAI content parts
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return ""


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
    chat = Chat(cfg, db, brain, searcher)
    site_url = str(cfg["site"]["url"]).rstrip("/")
    site_name = str(cfg["site"]["name"])

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
    def _key_from(x_api_key: str, authorization: str, key: str) -> str:
        k = x_api_key or key
        if not k and authorization.lower().startswith("bearer "):
            k = authorization[7:].strip()
        return k

    def _resolve(request: Request, k: str, strict: bool) -> dict:
        if k:
            u = authenticate(db, k)
            if not u:
                raise HTTPException(401, "invalid api key")
        elif strict or cfg["server"]["require_key"]:
            raise HTTPException(401, "api key required")
        else:
            u = {"id": 0, "name": "anonymous", "role": "user", "rate_per_min": cfg["server"]["rate_per_min"]}
        ident = f"u{u['id']}" if u["id"] else f"ip:{request.client.host if request.client else '?'}"
        if not limiter.allow(ident, u["rate_per_min"]):
            raise HTTPException(429, "Rate limit reached: please wait a moment and try again.")
        return u

    async def current_user(request: Request, x_api_key: str = Header(default=""),
                           authorization: str = Header(default=""), key: str = "") -> dict:
        return _resolve(request, _key_from(x_api_key, authorization, key), False)

    async def strict_user(request: Request, x_api_key: str = Header(default=""),
                          authorization: str = Header(default="")) -> dict:
        return _resolve(request, _key_from(x_api_key, authorization, ""), True)

    def _owner(u: dict, request: Request, client_id: str) -> str:
        if u["id"]:
            return f"u{u['id']}"
        if client_id and CLIENT_ID.match(client_id):
            return f"c:{client_id}"
        return f"ip:{request.client.host if request.client else '?'}"

    def _quota_limit(u: dict) -> int:
        return int(cfg["chat"]["daily_quota_key" if u["id"] else "daily_quota_anon"])

    def _usage_today(owner: str) -> int:
        r = db.q("SELECT n FROM usage WHERE owner=? AND day=?", (owner, time.strftime("%Y-%m-%d", time.gmtime())))
        return r[0][0] if r else 0

    def _charge(u: dict, owner: str) -> None:
        """Roz ki quota (0 = unlimited). Quota server ko dump hone se bachati hai."""
        day = time.strftime("%Y-%m-%d", time.gmtime())
        lim = _quota_limit(u)
        n = _usage_today(owner)
        if lim and n >= lim:
            raise HTTPException(429, f"Daily quota ({lim}) used up. Try again tomorrow or use your own free API key.")
        db.x("INSERT INTO usage(owner,day,n) VALUES(?,?,1) ON CONFLICT(owner,day) DO UPDATE SET n=n+1", (owner, day))
        if n == 0:  # purane din saaf
            db.x("DELETE FROM usage WHERE day < date('now','-7 day')")

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
                yield _sse("token", tr("error", q))
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

    # ---------------------------------------------------------------- chat API
    @app.get("/api/models")
    async def api_models():
        return {"default": "mus-7.01", "models": [m.public() for m in MODELS.values()],
                "private": bool(cfg["chat"].get("private_default", True))}

    @app.post("/api/chat")
    async def api_chat(body: ChatIn, request: Request, u: dict = Depends(current_user),
                       x_client_id: str = Header(default="")):
        owner = _owner(u, request, x_client_id)
        if not u["id"] and not limiter.allow("chat:" + owner, int(cfg["chat"]["anon_rate_per_min"])):
            raise HTTPException(429, "Rate limit reached: please wait a moment and try again.")
        _charge(u, owner)

        async def gen():
            hist = [h.model_dump() for h in body.history] if body.history else None
            async for ev, data in chat.stream(owner, u["role"], body.message, None if body.private else body.conversation_id,
                                              body.model, persist=not body.private, history=hist):
                yield _sse(ev, data)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/conversations")
    async def api_convs(request: Request, u: dict = Depends(current_user), x_client_id: str = Header(default="")):
        return {"conversations": chat.list_convs(_owner(u, request, x_client_id))}

    @app.get("/api/conversations/{cid}")
    async def api_conv(cid: str, request: Request, u: dict = Depends(current_user), x_client_id: str = Header(default="")):
        owner = _owner(u, request, x_client_id)
        c = chat.get_conv(owner, cid)
        if not c:
            raise HTTPException(404, "conversation not found")
        return {"conversation": c, "messages": chat.messages(cid)}

    @app.delete("/api/conversations/{cid}")
    async def api_conv_delete(cid: str, request: Request, u: dict = Depends(current_user),
                              x_client_id: str = Header(default="")):
        if not chat.delete_conv(_owner(u, request, x_client_id), cid):
            raise HTTPException(404, "conversation not found")
        return {"deleted": True}

    @app.post("/api/keys")
    async def api_make_key(body: KeyIn, request: Request):
        """Self-serve API key: koi bhi apni key bana kar Talashly ko apni automation/app mein istemal kar sakta hai."""
        if not cfg["chat"]["signup_enabled"]:
            raise HTTPException(403, "Key signup is disabled on this server.")
        ip = request.client.host if request.client else "?"
        if not limiter.allow("signup:" + ip, int(cfg["chat"]["signup_per_hour_per_ip"]), window=3600.0):
            raise HTTPException(429, "Too many keys created from this IP in the last hour; try again later.")
        label = re.sub(r"[^A-Za-z0-9 _.-]", "", body.label).strip()[:24]
        name = f"{label or 'key'}-{secrets.token_hex(4)}"
        key = create_user(db, name, "user", int(cfg["chat"]["key_rate_per_min"]))
        return {"api_key": key, "name": name, "rate_per_min": int(cfg["chat"]["key_rate_per_min"]),
                "daily_quota": int(cfg["chat"]["daily_quota_key"]) or "unlimited",
                "note": "This key is shown only once. Save it and do not share it.",
                "docs": f"{site_url}/api-docs"}

    @app.get("/api/me")
    async def api_me(request: Request, u: dict = Depends(current_user), x_client_id: str = Header(default="")):
        owner = _owner(u, request, x_client_id)
        lim = _quota_limit(u)
        return {"authenticated": bool(u["id"]), "name": u["name"], "role": u["role"],
                "rate_per_min": u["rate_per_min"], "used_today": _usage_today(owner),
                "daily_quota": lim or "unlimited"}

    # ---------------------------------------------------------------- OpenAI-compatible API
    @app.get("/v1/models")
    async def v1_models(u: dict = Depends(strict_user)):
        now = int(time.time())
        return {"object": "list", "data": [{"id": m.id, "object": "model", "created": now, "owned_by": "talashly"}
                                           for m in MODELS.values()]}

    @app.post("/v1/chat/completions")
    async def v1_chat(body: OAIChat, request: Request, u: dict = Depends(strict_user)):
        model = get_model(body.model, strict=True)
        if model is None:
            return _oai_error(404, f"The model '{body.model}' does not exist. See GET /v1/models.", "model_not_found")
        users = [(m.get("role"), _msg_text(m)) for m in body.messages if isinstance(m, dict)]
        user_texts = [t for r, t in users if r == "user" and t.strip()]
        if not user_texts or users[-1][0] != "user":
            return _oai_error(400, "The last message must have role 'user' and non-empty content.", "invalid_messages")
        _charge(u, f"u{u['id']}")
        res = await chat.complete(u["role"], user_texts[-1], model.id, user_texts[-2] if len(user_texts) > 1 else None)
        if res["error"]:
            code = res["error"]["code"]
            return _oai_error(503 if code == "busy" else 400, res["error"]["message"], code,
                              "server_busy" if code == "busy" else "invalid_request_error")
        content = format_with_sources(res["text"], res["sources"])
        cid, created = "chatcmpl-" + secrets.token_hex(12), int(time.time())
        ptok = sum(len(t.split()) for _, t in users)
        ctok = len(content.split())
        extra = {"sources": res["sources"], "mode": res["done"].get("mode")}
        if not body.stream:
            return {"id": cid, "object": "chat.completion", "created": created, "model": model.id,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": ptok, "completion_tokens": ctok, "total_tokens": ptok + ctok},
                    "talashly": extra}

        async def gen():
            def chunk(delta: dict, fin: str | None = None) -> str:
                return "data: " + json.dumps({"id": cid, "object": "chat.completion.chunk", "created": created,
                                              "model": model.id, "choices": [{"index": 0, "delta": delta,
                                                                              "finish_reason": fin}]}, ensure_ascii=False) + "\n\n"
            yield chunk({"role": "assistant"})
            for w in re.findall(r"\S+\s*", content):
                yield chunk({"content": w})
            yield chunk({}, "stop")
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---------------------------------------------------------------- pages + SEO
    def page(name: str) -> Response:
        f = WEB / name
        if not f.is_file():
            raise HTTPException(404, "not found")
        html = f.read_text(encoding="utf-8").replace("{{SITE_URL}}", site_url).replace("{{SITE_NAME}}", site_name)
        return Response(html, media_type="text/html; charset=utf-8")

    @app.get("/")
    async def home():
        return page("index.html")

    @app.get("/about")
    async def about():
        return page("about.html")

    @app.get("/api-docs")
    async def api_docs():
        return page("api.html")

    @app.get("/robots.txt")
    async def robots():
        bots = "\n".join(f"User-agent: {b}\nAllow: /\nDisallow: /api/\nDisallow: /admin/\nDisallow: /v1/\n"
                         for b in ("*", "GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended", "CCBot"))
        return PlainTextResponse(f"{bots}\nSitemap: {site_url}/sitemap.xml\n")

    @app.get("/sitemap.xml")
    async def sitemap():
        day = time.strftime("%Y-%m-%d", time.gmtime())
        urls = "".join(f"<url><loc>{site_url}{p}</loc><lastmod>{day}</lastmod></url>" for p in ("/", "/about", "/api-docs"))
        return Response('<?xml version="1.0" encoding="UTF-8"?>'
                        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
                        media_type="application/xml")

    @app.get("/llms.txt")
    async def llms():
        models = "\n".join(f"- {m.id}: {m.tagline}" for m in MODELS.values())
        return PlainTextResponse(
            f"# {site_name}\n\n> {site_name} is an open-source, self-hosted answer engine: its own crawler, its own index, "
            f"and cited answers in many languages. No third-party search or AI APIs.\n\n"
            f"## Pages\n- [App]({site_url}/): chat interface\n- [About]({site_url}/about): what it is, FAQ\n"
            f"- [API docs]({site_url}/api-docs): REST and OpenAI-compatible API\n\n## Models (all free)\n{models}\n\n"
            f"## Source code\nhttps://github.com/musman550/talashly\n")

    @app.get("/manifest.webmanifest")
    async def manifest():
        return JSONResponse({"name": site_name, "short_name": site_name, "start_url": "/", "display": "standalone",
                             "background_color": "#14130f", "theme_color": "#0f766e",
                             "icons": [{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml"}]})

    if (WEB / "static").is_dir():
        app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")

    if cfg["server"]["cors"]:
        app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                           allow_headers=["*"], allow_credentials=False)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if "text/html" in resp.headers.get("content-type", ""):
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            resp.headers.setdefault("Content-Security-Policy",
                                    "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                    "connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'")
        return resp

    return app


def run() -> None:
    from .__main__ import main

    main(["serve"])


if __name__ == "__main__":
    run()
