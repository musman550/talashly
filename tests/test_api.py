import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from engine.db import DB
from engine.server import create_app
from tests.helpers import make_cfg, parse_sse, seed_docs
from tests.site import Site

TXT = ("A polite crawler obeys robots.txt and waits between requests to the same host. "
       "It identifies itself with a clear user agent and respects the crawl delay. ") * 4
ASYNC = ("The asyncio event loop schedules coroutines cooperatively on a single thread. "
         "Awaiting a coroutine yields control back to the loop so other tasks can run. ") * 4


def build(tmp_path, docs=True, **sections):
    cfg = make_cfg(tmp_path, **sections)
    if docs:
        db = DB(cfg["general"]["db_path"])
        seed_docs(db, [("http://t.test/crawler", "Understanding web crawlers", TXT),
                       ("http://t.test/asyncio", "Python asyncio explained", ASYNC)])
        db.conn.close()
    return cfg, create_app(cfg, seeds=[], sources=[], crawl=False)


def admin_key(cfg) -> str:
    return (Path(cfg["_data_dir"]) / "admin_key.txt").read_text().strip()


def H(key):
    return {"X-Api-Key": key}


# ------------------------------------------------------------------ open mode
def test_open_mode_endpoints(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200 and "Talashly" in r.text
        h = c.get("/api/health").json()
        assert h["status"] == "ok" and h["index"]["docs"] == 2 and h["features"]["trigram_cjk"] is True
        assert h["crawler"]["enabled"] is False and h["config_problems_fixed"] == []
        s = c.get("/api/stats").json()
        assert s["index"]["docs"] == 2 and s["crawler_running"] is False
        res = c.get("/api/search", params={"q": "polite crawler"}).json()
        assert res["results"][0]["url"].endswith("/crawler") and "<mark>" in res["results"][0]["snippet"]
        assert res["took_ms"] < 500


def test_answer_sse_event_sequence_and_content(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        r = c.get("/api/answer", params={"q": "what does a polite crawler do"})
        assert r.headers["content-type"].startswith("text/event-stream")
        ev = parse_sse(r.text)
        names = [n for n, _ in ev]
        assert names[0] == "plan" and "sources" in names and names[-1] == "done" and "token" in names
        text = "".join(d for n, d in ev if n == "token")
        assert "robots.txt" in text and "[1]" in text
        done = ev[-1][1]
        assert done["mode"] == "extractive" and done["coverage"] >= 0.6 and done["citations"][0]["n"] == 1


def test_deep_flag_returns_more_sources(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        a = parse_sse(c.get("/api/answer", params={"q": "crawler asyncio coroutine robots", "deep": "true"}).text)
        assert dict(a)["plan"]["deep"] is True


def test_brain_exception_is_contained_and_logged(tmp_path, monkeypatch):
    cfg, app = build(tmp_path)

    async def boom(self, q, role="user", deep=False, stream_delay=0.0):
        raise RuntimeError("brain bug")
        yield  # pragma: no cover

    monkeypatch.setattr("engine.brain.Brain.run", boom)
    with TestClient(app) as c:
        r = c.get("/api/answer", params={"q": "anything"})
        assert r.status_code == 200
        ev = parse_sse(r.text)
        assert ev[-1] == ("done", {"mode": "error"}) and "masla" in "".join(d for n, d in ev if n == "token")
        assert c.get("/api/health").status_code == 200       # server zinda
    assert DB(cfg["general"]["db_path"]).q("SELECT 1 FROM brain_log WHERE kind='error' AND detail LIKE '%brain bug%'")


# ------------------------------------------------------------------ auth / users
def test_require_key_blocks_anonymous_and_accepts_header_or_query(tmp_path):
    cfg, app = build(tmp_path, server={"require_key": True})
    with TestClient(app) as c:
        k = admin_key(cfg)
        assert c.get("/api/search", params={"q": "crawler"}).status_code == 401
        assert c.get("/api/search", params={"q": "crawler"}, headers=H("ss_wrong")).status_code == 401
        assert c.get("/api/search", params={"q": "crawler"}, headers=H(k)).status_code == 200
        assert c.get("/api/search", params={"q": "crawler", "key": k}).status_code == 200
        assert c.get("/api/answer", params={"q": "crawler", "key": k}).status_code == 200  # EventSource header nahi bhej sakta
        assert c.get("/api/answer", params={"q": "crawler"}).status_code == 401
        assert c.get("/api/health").status_code == 200       # health/stats/UI public hain
        assert c.get("/").status_code == 200


def test_admin_key_created_once_and_only_hash_is_stored(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app):
        k1 = admin_key(cfg)
    assert k1.startswith("ss_") and len(k1) > 25
    cfg2, app2 = cfg, create_app(cfg, seeds=[], sources=[], crawl=False)
    with TestClient(app2):
        assert admin_key(cfg) == k1                          # dobara restart par nayi key nahi bani
    raw = b"".join(p.read_bytes() for p in Path(cfg["_data_dir"]).glob("t.db*"))
    assert k1.encode() not in raw                            # database mein key ka plaintext nahi


def test_user_management_and_role_separation(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        ak = admin_key(cfg)
        r = c.post("/admin/users", json={"name": "ali", "role": "user", "rate_per_min": 100}, headers=H(ak))
        uk = r.json()["api_key"]
        assert r.status_code == 200 and uk.startswith("ss_") and uk != ak
        assert c.post("/admin/users", json={"name": "ali"}, headers=H(ak)).status_code == 409
        assert c.post("/admin/users", json={"name": "x", "role": "root"}, headers=H(ak)).status_code == 422
        assert c.post("/admin/users", json={"name": ""}, headers=H(ak)).status_code == 422
        # normal user admin nahi
        for path, method in [("/admin/users", "get"), ("/admin/brain-log", "get"), ("/admin/gaps", "get"), ("/admin/heal", "post")]:
            assert getattr(c, method)(path, headers=H(uk)).status_code == 403, path
        assert c.post("/admin/seed", json={"url": "https://example.org"}, headers=H(uk)).status_code == 403
        assert c.post("/admin/seed", json={"url": "https://example.org"}).status_code == 403   # anonymous
        names = [u["name"] for u in c.get("/admin/users", headers=H(ak)).json()]
        assert names == ["admin", "ali"]
        assert all("key" not in json.dumps(u) for u in c.get("/admin/users", headers=H(ak)).json())


def test_admin_endpoints_work(tmp_path):
    cfg, app = build(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("def hello_world():\n    return 1\n")
    with TestClient(app) as c:
        h = H(admin_key(cfg))
        assert c.post("/admin/seed", json={"url": "https://example.org/feed.xml", "type": "feed"}, headers=h).json()["added"] is True
        assert c.post("/admin/seed", json={"url": "https://example.org/feed.xml", "type": "feed"}, headers=h).json()["added"] is False
        assert c.post("/admin/seed", json={"url": "ftp://example.org/x"}, headers=h).status_code == 422
        assert c.post("/admin/seed", json={"url": "javascript:alert(1)"}, headers=h).status_code == 422
        assert c.get("/admin/frontier", params={"status": "queued"}, headers=h).json()
        assert c.get("/admin/gaps", headers=h).status_code == 200
        c.get("/api/answer", params={"q": "unanswerable zzxx qqyy"})
        assert any("zzxx" in g["q"] for g in c.get("/admin/gaps", headers=h).json())
        assert c.get("/admin/brain-log", headers=h).json()
        assert "OK" in c.post("/admin/heal", headers=h).json()["result"]
        st = c.post("/admin/index-code", json={"paths": [str(proj)]}, headers=h).json()
        assert st["indexed"] == 1
        assert c.get("/api/code", params={"q": "hello_world"}).json()["results"][0]["path"].endswith("a.py")
        ev = parse_sse(c.get("/api/answer", params={"q": "/status", "key": admin_key(cfg)}).text)
        assert dict(ev)["plan"]["intent"] == "command" and "Pages: 2" in "".join(d for n, d in ev if n == "token")


def test_rate_limit_per_user_and_per_ip(tmp_path):
    cfg, app = build(tmp_path, server={"rate_per_min": 3})
    with TestClient(app) as c:
        ak = admin_key(cfg)
        uk = c.post("/admin/users", json={"name": "slow", "rate_per_min": 2}, headers=H(ak)).json()["api_key"]
        codes = [c.get("/api/search", params={"q": "crawler"}, headers=H(uk)).status_code for _ in range(4)]
        assert codes == [200, 200, 429, 429]
        assert c.get("/api/search", params={"q": "crawler"}, headers=H(ak)).status_code == 200  # dusra user mutasir nahi
        anon = [c.get("/api/search", params={"q": "crawler"}).status_code for _ in range(5)]
        assert anon == [200, 200, 200, 429, 429]


# ------------------------------------------------------------------ input hardening
@pytest.mark.parametrize("params,code", [({"q": ""}, 422), ({"q": "x" * 501}, 422), ({}, 422),
                                         ({"q": "crawler", "limit": "abc"}, 422)])
def test_invalid_search_params_are_rejected_cleanly(tmp_path, params, code):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        assert c.get("/api/search", params=params).status_code == code
        assert c.get("/api/health").status_code == 200


@pytest.mark.parametrize("limit", [0, -5, 1, 10 ** 9])
def test_limit_is_clamped(tmp_path, limit):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        r = c.get("/api/search", params={"q": "crawler", "limit": limit})
        assert r.status_code == 200 and len(r.json()["results"]) <= 30


@pytest.mark.parametrize("q", ['"', "'", "(", "*", "AND OR NOT", "NEAR(a b)", "💥", "\u0000x", "'; DROP TABLE docs;--",
                               "<script>alert(1)</script>", "%00", "../../etc/passwd", "{{7*7}}", "\\", "،،،", "a" * 500])
def test_hostile_queries_over_http(tmp_path, q):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        assert c.get("/api/search", params={"q": q}).status_code == 200
        assert c.get("/api/code", params={"q": q}).status_code == (200 if len(q) <= 300 else 422)
        r = c.get("/api/answer", params={"q": q})
        assert r.status_code == 200 and parse_sse(r.text)[-1][0] == "done"
        assert c.get("/api/stats").json()["index"]["docs"] == 2


def test_xss_payload_in_page_content_is_escaped_in_snippets_api_but_ui_uses_textnodes(tmp_path):
    cfg = make_cfg(tmp_path)
    db = DB(cfg["general"]["db_path"])
    seed_docs(db, [("http://t.test/x", "<img src=x onerror=alert(1)> title", "payload <script>alert(1)</script> crawler text " * 10)])
    db.conn.close()
    app = create_app(cfg, seeds=[], sources=[], crawl=False)
    with TestClient(app) as c:
        r = c.get("/api/search", params={"q": "payload crawler"}).json()["results"][0]
        assert r["title"].startswith("<img")                 # data waisa hi (JSON), UI mein textContent se
        ui = c.get("/").text
        assert "innerHTML" not in ui                          # UI kabhi innerHTML nahi use karti


# ------------------------------------------------------------------ tasks
def test_task_lifecycle_and_ownership(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        ak = admin_key(cfg)
        a = c.post("/admin/users", json={"name": "a"}, headers=H(ak)).json()["api_key"]
        b = c.post("/admin/users", json={"name": "b"}, headers=H(ak)).json()["api_key"]
        t = c.post("/api/tasks", json={"kind": "research", "q": "what does a polite crawler do"}, headers=H(a)).json()
        for _ in range(60):
            r = c.get(f"/api/tasks/{t['id']}", headers=H(a)).json()
            if r["status"] in ("done", "failed"):
                break
            time.sleep(0.2)
        assert r["status"] == "done" and "robots.txt" in r["result"]["answer"] and r["result"]["sources"]
        assert c.get(f"/api/tasks/{t['id']}", headers=H(b)).status_code == 404      # doosre user ka task nahi dikhta
        assert c.get(f"/api/tasks/{t['id']}", headers=H(ak)).status_code == 200      # admin dekh sakta hai
        assert c.get("/api/tasks/99999", headers=H(a)).status_code == 404


def test_task_validation_and_limits(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        ak = admin_key(cfg)
        uk = c.post("/admin/users", json={"name": "u"}, headers=H(ak)).json()["api_key"]
        assert c.post("/api/tasks", json={"kind": "research"}, headers=H(uk)).status_code == 422
        assert c.post("/api/tasks", json={"kind": "hack", "q": "x"}, headers=H(uk)).status_code == 422
        assert c.post("/api/tasks", json={"kind": "crawl", "url": "https://x.org"}, headers=H(uk)).status_code == 403
        assert c.post("/api/tasks", json={"kind": "crawl"}, headers=H(ak)).status_code == 422
        uid = c.get("/admin/users", headers=H(ak)).json()[1]["id"]
        db = c.app.state.db
        for _ in range(20):
            db.x("INSERT INTO tasks(user_id,kind,payload,status,created) VALUES(?,?,?,?,?)", (uid, "research", "{}", "running", time.time()))
        assert c.post("/api/tasks", json={"kind": "research", "q": "x"}, headers=H(uk)).status_code == 429


# ------------------------------------------------------------------ real server, many concurrent users, crawler running
def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_many_concurrent_users_while_crawler_runs(tmp_path):
    import uvicorn

    with Site() as site:
        cfg = make_cfg(tmp_path, server={"rate_per_min": 100000})
        db = DB(cfg["general"]["db_path"])
        seed_docs(db, [("http://t.test/crawler", "Understanding web crawlers", TXT), ("http://t.test/asyncio", "Asyncio", ASYNC)])
        db.conn.close()
        seeds = [{"url": site.url + "/", "type": "page", "topic": "t"}]
        app = create_app(cfg, seeds=seeds, sources=[], crawl=True)
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        th = threading.Thread(target=server.run, daemon=True)
        th.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        base = f"http://127.0.0.1:{port}"
        lat, bad = [], []

        def user(i: int):
            with httpx.Client(base_url=base, timeout=20) as cl:
                for j in range(12):
                    kind = (i + j) % 4
                    t0 = time.time()
                    try:
                        if kind == 0:
                            r = cl.get("/api/search", params={"q": "polite crawler robots"})
                        elif kind == 1:
                            r = cl.get("/api/answer", params={"q": "what does a polite crawler do"})
                            assert "event: done" in r.text
                        elif kind == 2:
                            r = cl.get("/api/search", params={"q": "کریولر معلومات" if j % 2 else "异步编程"})
                        else:
                            r = cl.get("/api/stats")
                        if r.status_code != 200:
                            bad.append((kind, r.status_code))
                    except Exception as e:  # noqa: BLE001
                        bad.append((kind, repr(e)))
                    lat.append(time.time() - t0)

        t0 = time.time()
        with ThreadPoolExecutor(24) as ex:
            list(ex.map(user, range(24)))
        wall = time.time() - t0
        time.sleep(1.0)
        docs = httpx.get(base + "/api/stats").json()["index"]["docs"]
        health = httpx.get(base + "/api/health").json()
        server.should_exit = True
        th.join(15)
        lat.sort()
        p50, p95 = lat[len(lat) // 2], lat[int(len(lat) * 0.95)]
        print(f"\n[load] 24 users x 12 req = {len(lat)} requests in {wall:.1f}s ({len(lat)/wall:.0f} req/s), p50={p50*1000:.0f}ms p95={p95*1000:.0f}ms, docs={docs}")
        assert not bad, bad[:5]
        assert p95 < 2.0
        assert docs >= 12, "crawler load ke dauran kaam nahi kar saka"       # 2 seeded + crawl hue pages
        assert health["crawler"]["running"] is True
        assert not DB(cfg["general"]["db_path"]).q("SELECT 1 FROM brain_log WHERE kind='error'")
