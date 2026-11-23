import asyncio
import json
import re

import pytest
from fastapi.testclient import TestClient

from engine.chat import Chat, contextualize, smalltalk
from engine.db import DB
from engine.models import MODELS, get_model
from engine.server import create_app
from tests.helpers import make_cfg, parse_sse, seed_docs

TXT = ("A polite crawler obeys robots.txt and waits between requests to the same host. "
       "It identifies itself with a clear user agent and respects the crawl delay. ") * 4
ASYNC = ("The asyncio event loop schedules coroutines cooperatively on a single thread. "
         "Awaiting a coroutine yields control back to the loop so other tasks can run. ") * 4
CID = "a" * 24
CID2 = "b" * 24


def build(tmp_path, chat=None, server=None, site=None):
    cfg = make_cfg(tmp_path, brain={"live_research": False})
    cfg["chat"].update(chat or {})
    cfg["server"].update(server or {})
    cfg["site"].update(site or {"url": "https://talashly.example"})
    db = DB(cfg["general"]["db_path"])
    seed_docs(db, [("http://t.test/crawler", "Understanding web crawlers", TXT),
                   ("http://t.test/asyncio", "Python asyncio explained", ASYNC)])
    db.conn.close()
    return cfg, create_app(cfg, seeds=[], sources=[], crawl=False)


def chat(c, msg, conv=None, model=None, cid=CID, key=None):
    h = {"X-Client-Id": cid}
    if key:
        h["Authorization"] = f"Bearer {key}"
    body = {"message": msg}
    if conv:
        body["conversation_id"] = conv
    if model:
        body["model"] = model
    r = c.post("/api/chat", json=body, headers=h)
    return r, parse_sse(r.text) if r.status_code == 200 else []


def names(evs):
    return [n for n, _ in evs]


def ev(evs, name):
    return [d for n, d in evs if n == name]


# ------------------------------------------------------------------ unit: small talk
@pytest.mark.parametrize("text,lang_word", [
    ("Hello!", "Hello|Hi there"), ("hi", "Hello|Hi there"), ("salam", "Assalam|Salam"), ("Assalam o alaikum", "Assalam|Salam"),
    ("السلام علیکم", "السلام"), ("thanks", "welcome|happy to help"), ("shukriya", "Khush|Koi baat"), ("شکریہ", "خوش"),
    ("bye", "Goodbye|Take care"), ("khuda hafiz", "hafiz"), ("how are you?", "AI"), ("kaise ho", "AI"),
    ("who are you", "AI"), ("tum kaun ho", "AI"), ("آپ کون ہیں", "AI"), ("what can you do", "Deep"),
])
def test_smalltalk_matches(text, lang_word):
    r = smalltalk(text, get_model("mus-7.01"))
    assert r and any(w.lower() in r.lower() for w in lang_word.split("|"))


@pytest.mark.parametrize("text", [
    "hello world program in python", "what is a polite crawler?", "thanks for the crawler explanation, what is a polite crawler",
    "who are you to judge the quantum computing field and its impact on cryptography", "help me fix this bug in my python code",
    "hi " * 12, "", "   ", "bye bye bye bye bye bye bye bye bye bye",
])
def test_smalltalk_never_swallows_real_questions(text):
    assert smalltalk(text, get_model("mus-7.01")) is None


def test_smalltalk_identity_is_honest_and_deterministic():
    m = get_model("mus-7.01-deep")
    a = smalltalk("who are you", m)
    assert "MUS 7.01 Deep" in a and "not a human" in a and "AI" in a
    assert a == smalltalk("who are you", m)
    ur = smalltalk("tum kaun ho", m)
    assert "insan nahi" in ur and "MUS 7.01 Deep" in ur
    assert "انسان نہیں" in smalltalk("آپ کون ہیں", m)


# ------------------------------------------------------------------ unit: follow-ups
def test_contextualize():
    assert contextualize("why is that important?", "what is a polite crawler") == "what is a polite crawler why is that important?"
    assert contextualize("iska example do", "python asyncio kya hai") == "python asyncio kya hai iska example do"
    assert contextualize("what about its history", "quantum computing") == "quantum computing what about its history"
    # naya alag topic: kabhi nahi jorta
    assert contextualize("python decorators", "quantum computing") == "python decorators"
    assert contextualize("what is machine learning", "quantum computing") == "what is machine learning"
    assert contextualize("anything", None) == "anything"
    # lamba sawal (7+ terms) apne aap mein mukammal hai
    long_q = "why do polite crawlers respect the robots txt crawl delay between requests"
    assert contextualize(long_q, "quantum computing") == long_q
    assert len(contextualize("it " * 5, "x" * 900)) <= 500


# ------------------------------------------------------------------ models
def test_models_distinct_and_free():
    assert "mus-7.01" in MODELS and len(MODELS) >= 5
    sigs = {(m.deep, m.live, m.sentences, m.sources, m.weak_cov, m.code_first) for m in MODELS.values()}
    assert len(sigs) == len(MODELS), "har model ka behaviour alag hona chahiye"
    assert get_model(None).id == "mus-7.01" and get_model("nope").id == "mus-7.01"
    assert get_model("nope", strict=True) is None and get_model("MUS-7.01-FLASH", strict=True).id == "mus-7.01-flash"
    assert all(p["free"] for p in (m.public() for m in MODELS.values()))


# ------------------------------------------------------------------ API: chat
def test_models_endpoint_and_chat_flow(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        m = c.get("/api/models").json()
        assert m["default"] == "mus-7.01" and {x["id"] for x in m["models"]} == set(MODELS)
        r, evs = chat(c, "what does a polite crawler do with sources")
        assert r.headers["content-type"].startswith("text/event-stream")
        n = names(evs)
        assert n[0] == "meta" and "title" in n and "sources" in n and n[-1] == "done"
        conv = ev(evs, "meta")[0]["conversation_id"]
        done = ev(evs, "done")[0]
        assert done["conversation_id"] == conv and done["model"] == "mus-7.01" and done["mode"] == "extractive"
        text = "".join(ev(evs, "token"))
        assert "robots.txt" in text and re.search(r"\[\d\]", text)
        # persisted
        lst = c.get("/api/conversations", headers={"X-Client-Id": CID}).json()["conversations"]
        assert len(lst) == 1 and lst[0]["id"] == conv and lst[0]["title"].startswith("what does")
        full = c.get(f"/api/conversations/{conv}", headers={"X-Client-Id": CID}).json()
        assert [m["role"] for m in full["messages"]] == ["user", "assistant"]
        assert full["messages"][1]["content"].strip() == text.strip()
        assert full["messages"][1]["meta"]["sources"]
        # follow-up: pichhla sawal jur jata hai
        r2, evs2 = chat(c, "why is that important? with sources", conv=conv)
        rw = ev(evs2, "rewrite")
        assert rw and rw[0]["query"].startswith("what does a polite crawler do")
        # sources dikhane ko na kaha ho to rewrite/plan/sources kuch nahi dikhta
        _, hid = chat(c, "what does a polite crawler do")
        assert not (set(names(hid)) & {"plan", "step", "sources", "rewrite"}) and not re.search(r"\[\d\]", "".join(ev(hid, "token")))
        assert ev(hid, "done")[0]["sources_shown"] is False
        assert len(c.get(f"/api/conversations/{conv}", headers={"X-Client-Id": CID}).json()["messages"]) == 4
        # naya topic: rewrite nahi
        r3, evs3 = chat(c, "asyncio event loop", conv=conv)
        assert not ev(evs3, "rewrite")


def test_smalltalk_via_api_has_no_sources(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        r, evs = chat(c, "salam")
        assert "sources" not in names(evs) and ev(evs, "done")[0]["mode"] == "chat"
        assert "Assalam" in "".join(ev(evs, "token"))


def test_no_evidence_is_honest_never_invented(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        r, evs = chat(c, "zxqvbn flurbnicator specifications")
        text = "".join(ev(evs, "token"))
        assert ev(evs, "done")[0]["mode"] == "none" and "good answer" in text
        assert ev(evs, "sources") in ([], [[]])


def test_validation_errors(tmp_path):
    cfg, app = build(tmp_path, chat={"max_message_chars": 100})
    with TestClient(app) as c:
        r, evs = chat(c, "x" * 101)
        assert ev(evs, "error")[0]["code"] == "too_long"
        r, evs = chat(c, "  \x00\x01  ")
        assert ev(evs, "error")[0]["code"] == "empty"
        r, evs = chat(c, "hello", conv="f" * 32)
        assert ev(evs, "error")[0]["code"] == "not_found"
        assert c.post("/api/chat", json={"message": ""}).status_code == 422
        assert c.post("/api/chat", json={}).status_code == 422
        assert c.post("/api/chat", content=b"not json", headers={"content-type": "application/json"}).status_code == 422


def test_conversations_are_private_between_owners(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        r, evs = chat(c, "polite crawler", cid=CID)
        conv = ev(evs, "meta")[0]["conversation_id"]
        assert c.get(f"/api/conversations/{conv}", headers={"X-Client-Id": CID2}).status_code == 404
        assert c.delete(f"/api/conversations/{conv}", headers={"X-Client-Id": CID2}).status_code == 404
        assert c.get("/api/conversations", headers={"X-Client-Id": CID2}).json()["conversations"] == []
        r2, evs2 = chat(c, "more", conv=conv, cid=CID2)
        assert ev(evs2, "error")[0]["code"] == "not_found"
        assert c.delete(f"/api/conversations/{conv}", headers={"X-Client-Id": CID}).json() == {"deleted": True}
        assert c.get("/api/conversations", headers={"X-Client-Id": CID}).json()["conversations"] == []
        # ghalat/chhota client id: ip-owner par girta hai, crash nahi
        assert c.get("/api/conversations", headers={"X-Client-Id": "short"}).status_code == 200


def test_conversation_cap_per_owner(tmp_path):
    cfg, app = build(tmp_path, chat={"max_convs_per_owner": 3})
    with TestClient(app) as c:
        for i in range(5):
            chat(c, f"polite crawler {i}")
        assert len(c.get("/api/conversations", headers={"X-Client-Id": CID}).json()["conversations"]) == 3


def test_models_behave_differently(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        _, deep = chat(c, "polite crawler and asyncio", model="mus-7.01-deep")
        assert ev(deep, "plan")[0]["deep"] is True and ev(deep, "done")[0]["model"] == "mus-7.01-deep"
        _, flash = chat(c, "polite crawler with sources", model="mus-7.01-flash")
        assert ev(flash, "plan")[0]["deep"] is False and len(ev(flash, "sources")[0]) <= 4
        assert len(re.findall(r"\[\d\]", "".join(ev(flash, "token")))) <= 3
        _, bad = chat(c, "polite crawler", model="does-not-exist")
        assert ev(bad, "done")[0]["model"] == "mus-7.01"  # ghalat naam: default, crash nahi


def test_flash_never_does_live_research(tmp_path):
    cfg = make_cfg(tmp_path)  # live_research ON
    cfg["site"]["url"] = "https://x.example"
    app = create_app(cfg, seeds=[], sources=[], crawl=False)
    called = []

    async def fake_live(q, force=False):
        called.append(q)
        return {"indexed": 0}
    with TestClient(app) as c:
        app.state.brain.live_research = fake_live
        chat(c, "zxqvbn unknown thing", model="mus-7.01-flash")
        assert called == []
        chat(c, "zxqvbn unknown thing", model="mus-7.01")
        assert called, "balanced model ko kamzor jawab par live research karni chahiye"


def test_code_model_prefers_local_code(tmp_path):
    from engine.codeindex import index_paths
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "billing.py").write_text("def compute_invoice_total(items):\n    return sum(i.price for i in items)\n")
    cfg, app = build(tmp_path)
    db = DB(cfg["general"]["db_path"])
    index_paths(db, [str(proj)], 500)
    db.conn.close()
    with TestClient(app) as c:
        _, evs = chat(c, "compute_invoice_total with sources", model="mus-7.01-code")
        assert ev(evs, "plan")[0]["intent"] == "code" and ev(evs, "code")
        assert "billing.py" in "".join(ev(evs, "token"))


def test_brain_crash_gives_clean_answer_and_is_logged(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        async def boom(*a, **k):
            raise RuntimeError("kaboom")
            yield  # pragma: no cover
        app.state.brain.run = boom
        r, evs = chat(c, "polite crawler")
        assert ev(evs, "done")[0]["mode"] == "error" and "went wrong" in "".join(ev(evs, "token"))
        assert "kaboom" not in r.text  # andar ki detail user ko leak nahi
        assert any("kaboom" in x["detail"] for x in app.state.db.q("SELECT detail FROM brain_log WHERE kind='error'"))
        # system zinda: agla sawal theek
        app.state.brain.run = type(app.state.brain).run.__get__(app.state.brain)
        r2, evs2 = chat(c, "polite crawler")
        assert ev(evs2, "done")[0]["mode"] == "extractive"


# ------------------------------------------------------------------ concurrency / dump protection
def test_busy_when_queue_full_and_recovers(tmp_path):
    cfg, app = build(tmp_path, chat={"max_concurrent": 1, "queue_max": 0})
    db = app.state.db
    ch = Chat(cfg, db, app.state.brain, None)  # type: ignore[arg-type]
    gate = asyncio.Event()

    async def slow(*a, **k):
        await gate.wait()
        yield "token", "ok "
        yield "done", {"mode": "extractive"}
    ch.brain = type("B", (), {"run": staticmethod(slow)})()  # type: ignore[assignment]

    async def go():
        first = asyncio.create_task(_drain(ch.stream("o", "user", "question one", None, None)))
        await asyncio.sleep(0.05)
        second = [e async for e in ch.stream("o", "user", "question two", None, None)]
        assert second[0][0] == "error" and second[0][1]["code"] == "busy"
        # busy hone par khali conversation nahi bani
        assert ch.list_convs("o") and len(ch.list_convs("o")) == 1
        gate.set()
        out = await first
        assert out[-1][0] == "done"
        third = await _drain(ch.stream("o", "user", "question three", None, None))
        assert third[-1][0] == "done" and ch.active == 0 and ch.waiting == 0
    asyncio.run(go())


def test_cheap_messages_bypass_full_slots(tmp_path):
    cfg, app = build(tmp_path, chat={"max_concurrent": 1, "queue_max": 0})
    ch = Chat(cfg, app.state.db, app.state.brain, None)  # type: ignore[arg-type]
    gate = asyncio.Event()

    async def slow(*a, **k):
        await gate.wait()
        yield "token", "ok "
        yield "done", {"mode": "extractive"}
    ch.brain = type("B", (), {"run": staticmethod(slow)})()  # type: ignore[assignment]

    async def go():
        first = asyncio.create_task(_drain(ch.stream("o", "user", "question one", None, None)))
        await asyncio.sleep(0.05)
        for q in ("hello", "what is todays date", "12*12", "tell me"):
            out = await _drain(ch.stream("o", "user", q, None, None))
            assert out[-1][0] == "done", q
        heavy = [e async for e in ch.stream("o", "user", "question two", None, None)]
        assert heavy[0][1]["code"] == "busy"
        gate.set()
        await first
    asyncio.run(go())


def test_queue_waits_instead_of_failing(tmp_path):
    cfg, app = build(tmp_path, chat={"max_concurrent": 1, "queue_max": 5})
    ch = Chat(cfg, app.state.db, app.state.brain, None)  # type: ignore[arg-type]

    async def slow(*a, **k):
        await asyncio.sleep(0.05)
        yield "token", "ok "
        yield "done", {"mode": "extractive"}
    ch.brain = type("B", (), {"run": staticmethod(slow)})()  # type: ignore[assignment]

    async def go():
        res = await asyncio.gather(*[_drain(ch.stream(f"o{i}", "user", f"question {i}", None, None)) for i in range(6)])
        assert all(r[-1][0] == "done" for r in res)
        assert ch.active == 0 and ch.waiting == 0
    asyncio.run(go())


def test_slot_released_when_client_disconnects(tmp_path):
    cfg, app = build(tmp_path, chat={"max_concurrent": 1, "queue_max": 0})
    ch = Chat(cfg, app.state.db, app.state.brain, None)  # type: ignore[arg-type]

    async def go():
        g = ch.stream("o", "user", "polite crawler", None, None)
        await g.__anext__()   # meta
        await g.aclose()      # client beech mein chala gaya
        assert ch.active == 0
        out = await _drain(ch.stream("o", "user", "polite crawler", None, None))
        assert out[-1][0] == "done"
    asyncio.run(go())


async def _drain(agen):
    return [e async for e in agen]


# ------------------------------------------------------------------ keys, quotas, OpenAI API
def test_signup_key_and_openai_api(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        assert c.get("/v1/models").status_code == 401
        assert c.get("/v1/models", headers={"Authorization": "Bearer ss_wrong"}).status_code == 401
        k = c.post("/api/keys", json={"label": "My App!<script>"}).json()
        key = k["api_key"]
        assert key.startswith("ss_") and re.fullmatch(r"MyAppscript-[0-9a-f]{8}|My Appscript-[0-9a-f]{8}", k["name"])
        auth = {"Authorization": f"Bearer {key}"}
        ms = c.get("/v1/models", headers=auth).json()
        assert ms["object"] == "list" and {m["id"] for m in ms["data"]} == set(MODELS)
        # X-Api-Key bhi chalta hai
        assert c.get("/v1/models", headers={"X-Api-Key": key}).status_code == 200
        body = {"model": "mus-7.01", "messages": [{"role": "system", "content": "be nice"},
                                                   {"role": "user", "content": "what does a polite crawler do, with sources"}]}
        r = c.post("/v1/chat/completions", json=body, headers=auth)
        assert r.status_code == 200
        j = r.json()
        assert j["object"] == "chat.completion" and j["model"] == "mus-7.01" and j["choices"][0]["finish_reason"] == "stop"
        content = j["choices"][0]["message"]["content"]
        assert "robots.txt" in content and "Sources:" in content and "http://t.test/crawler" in content
        assert j["usage"]["total_tokens"] > 0 and j["talashly"]["sources"]
        # streaming: chunks jorne par wahi text
        rs = c.post("/v1/chat/completions", json={**body, "stream": True}, headers=auth)
        lines = [l for l in rs.text.split("\n\n") if l.startswith("data: ")]
        assert lines[-1] == "data: [DONE]"
        chunks = [json.loads(l[6:]) for l in lines[:-1]]
        assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
        assert "".join(ch["choices"][0]["delta"].get("content", "") for ch in chunks) == content
        # content parts format, extra params
        body2 = {"model": "mus-7.01-flash", "temperature": 0.1, "max_tokens": 50,
                 "messages": [{"role": "user", "content": [{"type": "text", "text": "polite crawler"}]}]}
        assert c.post("/v1/chat/completions", json=body2, headers=auth).status_code == 200
        # follow-up stateless: pichhla user message
        body3 = {"messages": [{"role": "user", "content": "polite crawler"}, {"role": "assistant", "content": "..."},
                              {"role": "user", "content": "why is that important?"}]}
        assert c.post("/v1/chat/completions", json=body3, headers=auth).status_code == 200
        # errors OpenAI format mein
        e = c.post("/v1/chat/completions", json={**body, "model": "gpt-4"}, headers=auth)
        assert e.status_code == 404 and e.json()["error"]["code"] == "model_not_found"
        e = c.post("/v1/chat/completions", json={"messages": [{"role": "assistant", "content": "x"}]}, headers=auth)
        assert e.status_code == 400 and e.json()["error"]["code"] == "invalid_messages"
        e = c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": " "}]}, headers=auth)
        assert e.status_code == 400
        assert c.post("/v1/chat/completions", json={"messages": []}, headers=auth).status_code == 422
        # key se /api/me
        me = c.get("/api/me", headers=auth).json()
        assert me["authenticated"] is True and me["used_today"] == 4 and me["role"] == "user"
        # CORS: browser-based third-party app bhi istemal kar sake
        o = c.options("/v1/chat/completions", headers={"Origin": "https://app.example", "Access-Control-Request-Method": "POST",
                                                      "Access-Control-Request-Headers": "authorization,content-type"})
        assert o.headers.get("access-control-allow-origin") == "*"


def test_signup_limits(tmp_path):
    cfg, app = build(tmp_path, chat={"signup_per_hour_per_ip": 2})
    with TestClient(app) as c:
        assert c.post("/api/keys", json={}).status_code == 200
        assert c.post("/api/keys", json={}).status_code == 200
        assert c.post("/api/keys", json={}).status_code == 429
    cfg, app = build(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, chat={"signup_enabled": False})
    with TestClient(app) as c:
        assert c.post("/api/keys", json={}).status_code == 403


def test_daily_quota_and_unlimited(tmp_path):
    cfg, app = build(tmp_path, chat={"daily_quota_anon": 2})
    with TestClient(app) as c:
        assert chat(c, "polite crawler")[0].status_code == 200
        assert chat(c, "polite crawler")[0].status_code == 200
        r = chat(c, "polite crawler")[0]
        assert r.status_code == 429 and "quota" in r.json()["detail"]
        # doosra user (alag client id) mutassir nahi hota? (anon quota owner-wise)
        assert chat(c, "polite crawler", cid=CID2)[0].status_code == 200
    (tmp_path / "u").mkdir()
    cfg, app = build(tmp_path / "u", chat={"daily_quota_anon": 0, "anon_rate_per_min": 1000})
    with TestClient(app) as c:
        for _ in range(8):
            assert chat(c, "polite crawler")[0].status_code == 200


def test_key_quota_separate_from_anon(tmp_path):
    cfg, app = build(tmp_path, chat={"daily_quota_key": 1})
    with TestClient(app) as c:
        key = c.post("/api/keys", json={}).json()["api_key"]
        auth = {"Authorization": f"Bearer {key}"}
        b = {"messages": [{"role": "user", "content": "polite crawler"}]}
        assert c.post("/v1/chat/completions", json=b, headers=auth).status_code == 200
        assert c.post("/v1/chat/completions", json=b, headers=auth).status_code == 429


def test_anonymous_chat_rate_limit(tmp_path):
    cfg, app = build(tmp_path, chat={"anon_rate_per_min": 3})
    with TestClient(app) as c:
        codes = [chat(c, "polite crawler")[0].status_code for _ in range(5)]
        assert codes[:3] == [200] * 3 and 429 in codes[3:]


def test_require_key_mode(tmp_path):
    cfg, app = build(tmp_path, server={"require_key": True})
    with TestClient(app) as c:
        assert chat(c, "hello")[0].status_code == 401
        key = c.post("/api/keys", json={}).json()["api_key"]
        r, evs = chat(c, "hello", key=key)
        assert r.status_code == 200 and ev(evs, "done")


# ------------------------------------------------------------------ pages + SEO
def test_seo_files(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        rb = c.get("/robots.txt").text
        assert "Sitemap: https://talashly.example/sitemap.xml" in rb and "Disallow: /admin/" in rb
        assert "GPTBot" in rb and "ClaudeBot" in rb and "PerplexityBot" in rb
        sm = c.get("/sitemap.xml")
        assert sm.headers["content-type"].startswith("application/xml")
        for p in ("/", "/about", "/api-docs"):
            assert f"<loc>https://talashly.example{p}</loc>" in sm.text
        ll = c.get("/llms.txt").text
        assert ll.startswith("# Talashly") and "mus-7.01" in ll and "/api-docs" in ll
        mf = c.get("/manifest.webmanifest").json()
        assert mf["name"] == "Talashly" and mf["start_url"] == "/"


@pytest.mark.parametrize("path", ["/", "/about", "/api-docs"])
def test_pages_have_seo_and_security(tmp_path, path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        r = c.get(path)
        assert r.status_code == 200 and "{{" not in r.text, "template placeholder bacha hua hai"
        html = r.text
        canon = "https://talashly.example" + ("" if path == "/" else path)
        assert f'<link rel="canonical" href="{canon}' in html
        assert '<meta name="description"' in html and 'property="og:title"' in html and 'name="twitter:card"' in html
        assert '<html lang="en"' in html and 'name="viewport"' in html and "<h1" in html
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
        assert blocks, "JSON-LD chahiye"
        for b in blocks:
            json.loads(b)  # valid JSON
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert "<script>" not in html.replace('<script type="application/ld+json">', ""), "inline script CSP tod dega"


def test_about_has_faq_schema_and_api_has_software_schema(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        about = re.findall(r'<script type="application/ld\+json">(.*?)</script>', c.get("/about").text, re.S)
        types = {json.loads(b).get("@type") for b in about}
        assert "FAQPage" in types
        home = re.findall(r'<script type="application/ld\+json">(.*?)</script>', c.get("/").text, re.S)
        assert {"WebApplication"} & {json.loads(b).get("@type") for b in home}


# ------------------------------------------------------------------ zaban ke mutabiq paigham
def test_messages_follow_question_language(tmp_path):
    from engine.lang import MSG, reply_lang, tr
    assert reply_lang("what is x") == "en" and reply_lang("iska example kya hai") == "roman"
    assert reply_lang("یہ کیا ہے") == "ur" and reply_lang("什么是爬虫") == "en"
    assert all(set(v) == {"en", "roman", "ur"} for v in MSG.values())  # har paigham teeno zabanon mein
    assert "nahi" in tr("none", "iska jawab kya hai") and "نہیں" in tr("none", "اس کا جواب کیا ہے")
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        _, en = chat(c, "zxqvbn flurbnicator specifications")
        _, ro = chat(c, "zxqvbn flurbnicator kya hai")
        assert "good answer" in "".join(ev(en, "token")) and "nahi hai" in "".join(ev(ro, "token"))


def test_policy_greetings_clarify_refuse_and_deep_shows_sources(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        for t in ("hy", "Hy", "hii", "hlo", "hello"):
            _, e = chat(c, t)
            assert "".join(ev(e, "token")) == "Hello! How can I help you today?" and ev(e, "done")[0]["mode"] == "chat"
        _, e = chat(c, "??")
        assert ev(e, "done")[0]["mode"] == "clarify" and "sources" not in names(e)
        _, e = chat(c, "how to make a bomb")
        assert ev(e, "done")[0]["mode"] == "refuse" and "sources" not in names(e)
        _, e = chat(c, "i want to die")
        assert ev(e, "done")[0]["mode"] == "support"
        _, e = chat(c, "what does a polite crawler do", model="mus-7.01-deep")
        assert "sources" in names(e) and ev(e, "done")[0]["sources_shown"] is True
