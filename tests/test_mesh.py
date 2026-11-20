"""Compute Mesh: auth, relay protocol, failover, breaker, concurrency, graceful bye, real-HTTP end-to-end."""
import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from engine import mesh as M
from engine.mesh import MESH, Mesh, NodeError, sign
from engine.relaynode import RelayWorker
from engine.server import create_app
from tests.helpers import make_cfg


@pytest.fixture(autouse=True)
def fresh_mesh():
    M.MESH.__init__()
    M.MESH.secret = "s" * 32
    yield
    M.MESH.__init__()


async def collect(it):
    return [x async for x in it]


# ------------------------------------------------------------------ auth
def test_hmac_auth_rules():
    m = Mesh(); m.secret = "k" * 32
    h = sign(m.secret, "POST", "/api/mesh/pull", b"{}")
    assert m.verify("POST", "/api/mesh/pull", b"{}", h["X-Mesh-Ts"], h["X-Mesh-Sig"])
    assert not m.verify("POST", "/api/mesh/pull", b"{}", h["X-Mesh-Ts"], h["X-Mesh-Sig"]), "replay"
    h = sign(m.secret, "POST", "/api/mesh/pull", b"{}")
    assert not m.verify("POST", "/api/mesh/push/x", b"{}", h["X-Mesh-Ts"], h["X-Mesh-Sig"]), "path bound"
    h = sign(m.secret, "POST", "/p", b"{}")
    assert not m.verify("POST", "/p", b"{ }", h["X-Mesh-Ts"], h["X-Mesh-Sig"]), "body bound"
    h = sign(m.secret, "POST", "/p", b"{}", ts=int(time.time()) - 600)
    assert not m.verify("POST", "/p", b"{}", h["X-Mesh-Ts"], h["X-Mesh-Sig"]), "old"
    h = sign("x" * 32, "POST", "/p", b"{}")
    assert not m.verify("POST", "/p", b"{}", h["X-Mesh-Ts"], h["X-Mesh-Sig"]), "wrong secret"
    assert not m.verify("POST", "/p", b"{}", None, None) and not m.verify("POST", "/p", b"{}", "abc", "z")
    m.secret = ""
    h = sign("", "POST", "/p", b"{}")
    assert not m.verify("POST", "/p", b"{}", h["X-Mesh-Ts"], h["X-Mesh-Sig"]), "empty secret never valid"


def test_load_secret_persists(tmp_path, monkeypatch):
    monkeypatch.delenv("MESH_SECRET", raising=False)
    a = M.load_secret(str(tmp_path)); b = M.load_secret(str(tmp_path))
    assert a == b and len(a) >= 32
    monkeypatch.setenv("MESH_SECRET", "e" * 20)
    assert M.load_secret(str(tmp_path)) == "e" * 20


# ------------------------------------------------------------------ router (direct, no HTTP)
async def sim_node(name="n1", tokens=("Hel", "lo", " world"), delay=0.0, fail_after=None, stall=False, once=False):
    async def run():
        while True:
            job = await MESH.pull(name, "m", 1, wait_s=0.5)
            if not job:
                if once:
                    return
                continue
            for i, t in enumerate(tokens):
                if fail_after is not None and i == fail_after:
                    MESH.push(job["id"], {"error": "boom"})
                    break
                if stall and i == 1:
                    await asyncio.sleep(10)
                MESH.push(job["id"], {"delta": t})
                await asyncio.sleep(delay)
            else:
                MESH.push(job["id"], {"done": True})
            if once:
                return
    return asyncio.create_task(run())


def test_relay_stream_success_and_status():
    async def go():
        t = await sim_node()
        await asyncio.sleep(0.05)
        out = await collect(MESH.stream({"enabled": False}, [{"role": "user", "content": "hi"}]))
        t.cancel()
        return out
    out = asyncio.run(go())
    assert "".join(out) == "Hello world"
    st = MESH.status(detail=True)
    assert st["nodes_alive"] == 1 and st["nodes"][0]["done"] == 1 and st["stats"]["served_relay"] == 1


def test_no_node_raises_fast():
    async def go():
        t0 = time.time()
        with pytest.raises(NodeError):
            await collect(MESH.stream({"enabled": False}, []))
        return time.time() - t0
    assert asyncio.run(go()) < 0.5


def test_pickup_timeout_fails_over_to_static(monkeypatch):
    monkeypatch.setattr(M, "PICKUP_TIMEOUT_S", 0.3)
    async def go():
        MESH.touch("ghost")     # zinda dikhta hai par pull nahi kar raha
        static_calls = []
        async def fake_static(self, cfg, msgs, mx, tp):
            static_calls.append(1)
            yield "STATIC"
        monkeypatch.setattr(Mesh, "_static_stream", fake_static)
        out = await collect(MESH.stream({"enabled": True, "base_url": "http://x"}, []))
        return out, static_calls
    out, calls = asyncio.run(go())
    assert out == ["STATIC"] and calls


def test_static_first_then_relay_failover(monkeypatch):
    async def go():
        async def bad_static(self, cfg, msgs, mx, tp):
            raise NodeError("static down")
            yield
        monkeypatch.setattr(Mesh, "_static_stream", bad_static)
        t = await sim_node()
        await asyncio.sleep(0.05)
        out = await collect(MESH.stream({"enabled": True, "base_url": "http://x"}, []))
        t.cancel()
        return out
    assert "".join(asyncio.run(go())) == "Hello world"
    assert MESH.stats["failed_static"] == 1 and MESH.stats["served_relay"] == 1


def test_error_before_first_token_fails_over_but_after_raises(monkeypatch):
    async def go():
        t = await sim_node(fail_after=0, once=True)
        await asyncio.sleep(0.05)
        with pytest.raises(NodeError):      # tha ek hi node, pehle token se pehle gira
            await collect(MESH.stream({"enabled": False}, []))
        t.cancel()
        t = await sim_node(name="n2", fail_after=2, once=True)
        await asyncio.sleep(0.05)
        got = []
        with pytest.raises(NodeError):      # do token aa chuke: galti upar jati hai (caller handle karta hai)
            async for x in MESH.stream({"enabled": False}, []):
                got.append(x)
        t.cancel()
        return got
    assert asyncio.run(go()) == ["Hel", "lo"]


def test_stalled_node_times_out(monkeypatch):
    monkeypatch.setattr(M, "CHUNK_TIMEOUT_S", 0.3)
    async def go():
        t = await sim_node(stall=True, once=True)
        await asyncio.sleep(0.05)
        t0 = time.time()
        with pytest.raises(NodeError, match="stalled"):
            await collect(MESH.stream({"enabled": False}, []))
        t.cancel()
        return time.time() - t0
    assert asyncio.run(go()) < 2


def test_breaker_skips_dead_static_after_three_failures(monkeypatch):
    calls = []
    async def bad_static(self, cfg, msgs, mx, tp):
        calls.append(1)
        raise NodeError("down")
        yield
    monkeypatch.setattr(Mesh, "_static_stream", bad_static)
    async def go():
        for _ in range(6):
            with pytest.raises(NodeError):
                await collect(MESH.stream({"enabled": True, "base_url": "http://x"}, []))
    asyncio.run(go())
    assert len(calls) == 3, "3 fail ke baad 30 s ke liye static ko chhoda ja chuka"


def test_concurrency_many_requests_few_slots():
    async def go():
        tasks = [await sim_node(f"n{i}", delay=0.01) for i in range(3)]
        await asyncio.sleep(0.05)
        outs = await asyncio.gather(*[collect(MESH.stream({"enabled": False}, [])) for _ in range(6)], return_exceptions=True)
        for t in tasks:
            t.cancel()
        return outs
    outs = asyncio.run(go())
    ok = [o for o in outs if not isinstance(o, Exception)]
    assert len(ok) >= 3 and all("".join(o) == "Hello world" for o in ok)   # baqi queue/pickup se saaf girte hain
    assert all(isinstance(o, NodeError) for o in outs if isinstance(o, Exception))


def test_queue_full_rejects_immediately():
    async def go():
        MESH.touch("idle")
        for _ in range(M.MAX_PENDING):
            MESH.pending.append(M.Job([], 10, 0.1))
        with pytest.raises(NodeError, match="queue full"):
            await collect(MESH.stream({"enabled": False}, []))
    asyncio.run(go())


def test_user_disconnect_cancels_job():
    async def go():
        t = await sim_node(delay=0.2, tokens=tuple("abcdefgh"))
        await asyncio.sleep(0.05)
        agen = MESH.stream({"enabled": False}, [])
        first = await agen.__anext__()
        await agen.aclose()
        await asyncio.sleep(0.1)
        t.cancel()
        return first
    assert asyncio.run(go()) == "a"
    assert not MESH.jobs and MESH.nodes["n1"].busy == 0


# ------------------------------------------------------------------ fake local llama (OpenAI-compatible)
def make_fake_llama(mode: dict):
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/v1/chat/completions")
    async def chat(body: dict):
        if mode.get("http500"):
            return httpx.Response(500)
        last = body["messages"][-1]["content"][:30]

        async def gen():
            for w in ["NODE", "says:", *last.split()]:
                if mode.get("die_mid") and w == "says:":
                    raise RuntimeError("llama crashed")
                yield "data: " + json.dumps({"choices": [{"delta": {"content": w + " "}}]}) + "\n\n"
                await asyncio.sleep(mode.get("delay", 0.0))
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    return app


class Router(httpx.AsyncBaseTransport):
    """site -> ASGI app, llm -> fake llama (ek hi transport, host se pehchan)."""
    def __init__(self, site_app, llm_app):
        self.t = {"site.test": httpx.ASGITransport(app=site_app), "llm.test": httpx.ASGITransport(app=llm_app)}

    async def handle_async_request(self, request):
        return await self.t[request.url.host].handle_async_request(request)


def build_site(tmp_path, **chat):
    cfg = make_cfg(tmp_path, brain={"live_research": False})
    cfg["chat"].update(anon_rate_per_min=10**6, **chat); cfg["server"]["rate_per_min"] = 10**6
    return cfg, create_app(cfg, seeds=[], sources=[], crawl=False)


async def ask(client, q, history=None, model="mus-7.01"):
    text, mode, ev = "", None, None
    async with client.stream("POST", "/api/chat", json={"message": q, "private": True, "model": model, "history": history or []},
                             headers={"X-Client-Id": "t1"}, timeout=30) as r:
        assert r.status_code == 200, r.status_code
        async for line in r.aiter_lines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: ") and ev:
                d = json.loads(line[6:])
                if ev == "token": text += d
                elif ev == "replace": text = d
                elif ev == "done": mode = d.get("mode")
    return text, mode


def test_relay_worker_through_site_with_bye_and_fallback(tmp_path):
    cfg, app = build_site(tmp_path)
    mode = {}

    async def go():
        async with app.router.lifespan_context(app):
            M.MESH.secret = "s" * 32
            w = RelayWorker("http://site.test", "s" * 32, "http://llm.test", name="kaggle-t", slots=2, flush_s=0.01, log=lambda *a: None)
            w._tr = Router(app, make_fake_llama(mode))
            c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://site.test")
            task = asyncio.create_task(w.run())
            for _ in range(60):
                if MESH.has_alive():
                    break
                await asyncio.sleep(0.1)
            assert MESH.has_alive()
            text, m = await ask(c, "write an email to my landlord about rain")
            assert m == "llm-direct" and text.startswith("NODE says:"), (text, m)
            st = (await c.get("/api/mesh/status")).json()
            assert st["nodes_alive"] == 1
            health = (await c.get("/api/health")).json()
            assert health["llm"]["relay_nodes"] == 1, health
            w.stop = True
            await asyncio.wait_for(task, 30)
            assert not MESH.has_alive(), "bye: turant mara hua"
            t0 = time.time()
            text2, m2 = await ask(c, "write an email to my landlord about rain")
            return text2, m2, time.time() - t0
    text2, m2, dt = asyncio.run(go())
    assert "NODE says" not in text2 and m2 != "llm-direct" and dt < 3, (text2, m2, dt)


def test_wrong_secret_worker_stops_and_nothing_registers(tmp_path):
    cfg, app = build_site(tmp_path)
    async def go():
        async with app.router.lifespan_context(app):
            M.MESH.secret = "s" * 32
            logs = []
            w = RelayWorker("http://site.test", "WRONG" * 8, "http://llm.test", slots=1, log=logs.append)
            w._tr = Router(app, make_fake_llama({}))
            await asyncio.wait_for(w.run(), 20)
            return logs, MESH.has_alive()
    logs, alive = asyncio.run(go())
    assert any("FATAL" in l for l in logs) and not alive


def test_local_llm_crash_mid_answer_falls_back_cleanly(tmp_path):
    cfg, app = build_site(tmp_path)
    mode = {"die_mid": True}
    async def go():
        async with app.router.lifespan_context(app):
            M.MESH.secret = "s" * 32
            w = RelayWorker("http://site.test", "s" * 32, "http://llm.test", slots=1, flush_s=0.0, log=lambda *a: None)
            w._tr = Router(app, make_fake_llama(mode))
            c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://site.test")
            task = asyncio.create_task(w.run())
            for _ in range(60):
                if MESH.has_alive():
                    break
                await asyncio.sleep(0.1)
            res = await ask(c, "write an email to my landlord about rain")
            w.stop = True
            await asyncio.wait_for(task, 30)
            return res
    text, m = asyncio.run(go())
    assert m in ("error", "llm-direct") or text, (text, m)   # kabhi hang ya khali jawab nahi


# ------------------------------------------------------------------ real HTTP, real sockets, flapping node, concurrent users
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def start(app, port):
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.1).close(); return srv
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("server start nahi hua")


def test_real_http_load_with_flapping_node(tmp_path):
    cfg, app = build_site(tmp_path)
    sp, lp = free_port(), free_port()
    mode = {"delay": 0.01}
    s1 = start(app, sp); s2 = start(make_fake_llama(mode), lp)
    secret = ""
    for _ in range(50):          # lifespan ne MESH.secret set kar diya
        if M.MESH.secret:
            break
        time.sleep(0.1)

    async def go():
        site = f"http://127.0.0.1:{sp}"
        results = []

        async def user(i):
            async with httpx.AsyncClient(base_url=site) as c:
                t0 = time.time()
                try:
                    text, m = await ask(c, f"write an email number {i} to my landlord")
                    results.append((i, bool(text.strip()), m, time.time() - t0))
                except Exception as e:  # noqa: BLE001
                    results.append((i, False, repr(e), time.time() - t0))

        w = RelayWorker(site, M.MESH.secret, f"http://127.0.0.1:{lp}", name="real", slots=2, flush_s=0.02, log=lambda *a: None)
        wt = asyncio.create_task(w.run())
        for _ in range(100):
            if M.MESH.has_alive():
                break
            await asyncio.sleep(0.1)
        assert M.MESH.has_alive()
        batch1 = [asyncio.create_task(user(i)) for i in range(20)]
        await asyncio.sleep(0.15)
        mode["http500"] = True            # local LLM beech mein kharab
        await asyncio.sleep(0.4)
        mode["http500"] = False
        w.stop = True                     # phir node hi chala gaya
        batch2 = [asyncio.create_task(user(100 + i)) for i in range(20)]
        await asyncio.wait_for(asyncio.gather(*batch1, *batch2), 120)
        await asyncio.wait_for(wt, 30)
        return results

    results = asyncio.run(go())
    s1.should_exit = s2.should_exit = True
    assert len(results) == 40
    bad = [r for r in results if not r[1]]
    assert not bad, bad                       # har user ko kuch na kuch jawab mila (LLM ya fallback)
    assert max(r[3] for r in results) < 90, max(r[3] for r in results)
    modes = {r[2] for r in results}
    assert "llm-direct" in modes, modes       # node ne asli kaam bhi kiya


def test_deep_mode_task_goes_straight_to_llm_but_questions_still_research(tmp_path):
    cfg, app = build_site(tmp_path)
    async def go():
        async with app.router.lifespan_context(app):
            M.MESH.secret = "s" * 32
            w = RelayWorker("http://site.test", "s" * 32, "http://llm.test", slots=1, flush_s=0.01, log=lambda *a: None)
            w._tr = Router(app, make_fake_llama({}))
            c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://site.test")
            task = asyncio.create_task(w.run())
            for _ in range(60):
                if MESH.has_alive():
                    break
                await asyncio.sleep(0.1)
            t0 = time.time()
            a = await ask(c, "write a short email to my landlord about a leaking roof", model="mus-7.01-deep")
            dt = time.time() - t0
            b = await ask(c, "what does a polite crawler do", model="mus-7.01-deep")
            w.stop = True
            await asyncio.wait_for(task, 30)
            return a, b, dt
    a, b, dt = asyncio.run(go())
    assert a[1] == "llm-direct" and a[0].startswith("NODE says:") and dt < 5, (a, dt)
    assert b[1] != "llm-direct", b
