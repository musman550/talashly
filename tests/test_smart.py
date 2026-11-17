"""Smart layer: website builder, LLM direct path (fake provider), private mode, needllm, timeouts."""
import json

import httpx
from fastapi.testclient import TestClient

from engine import llm as llm_mod
from engine.builder import _name, build_site, wants_site
from tests.test_chat import build, chat, ev, names


def test_builder_names_and_xss():
    assert _name("make a website for Noor Bakery in green") == "Noor Bakery"
    assert _name("create a landing page for my gym called FitHub") == "FitHub"
    assert _name("bana do Ali Clinic ki website") == "Ali Clinic"
    assert not wants_site("what is a website")
    page = build_site('make a blue website for my restaurant called Evil"><script>alert(1)</script>')
    assert "<script>alert(1)" not in page and "cdn.tailwindcss.com" in page and "text-blue" in page.replace("bg-blue", "text-blue")


def test_website_request_without_llm_builds_tailwind_page(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        _, evs = chat(c, "can you make a website for Noor Bakery?")
        text = "".join(ev(evs, "token"))
        assert ev(evs, "done")[0]["mode"] == "builder" and "```html" in text and "Noor Bakery" in text
        assert "cdn.tailwindcss.com" in text and "sources" not in names(evs)


def test_custom_task_without_llm_is_honest_not_random_text(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        _, evs = chat(c, "write a poem about autumn rain")
        assert ev(evs, "done")[0]["mode"] == "needllm"
        assert "security" not in "".join(ev(evs, "token")).lower()


def test_weak_match_gives_no_answer_instead_of_junk(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        _, evs = chat(c, "polite crawler zebra quantum saxophone banana")
        assert ev(evs, "done")[0]["mode"] in ("none", "extractive")


def _fake_llm(monkeypatch, calls):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((str(request.url), request.headers.get("authorization"), body))
        chunks = ["Sure! ", "Here you go."]
        sse = "".join("data: " + json.dumps({"choices": [{"delta": {"content": t}}]}) + "\n\n" for t in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})
    real = httpx.AsyncClient

    class Fake(real):
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **k)
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", Fake)


def test_llm_direct_path_for_tasks_with_key_and_history(tmp_path, monkeypatch):
    calls = []
    _fake_llm(monkeypatch, calls)
    cfg, app = build(tmp_path)
    cfg["llm"].update(enabled=True, base_url="https://llm.example", api_key="k-123", model="m1", path="/v1/chat/completions")
    with TestClient(app) as c:
        r = c.post("/api/chat", json={"message": "write a short thank you note", "private": True,
                                      "history": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello!"}]},
                   headers={"X-Client-Id": "p" * 24})
        assert "Sure! " in r.text and "llm-direct" in r.text
    url, auth, body = calls[0]
    assert url == "https://llm.example/v1/chat/completions" and auth == "Bearer k-123" and body["model"] == "m1"
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "user"] and "Tailwind" in body["messages"][0]["content"]
    assert "k-123" not in r.text


def test_llm_failure_falls_back_to_builder(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(500)
    real = httpx.AsyncClient

    class Fake(real):
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **k)
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", Fake)
    cfg, app = build(tmp_path)
    cfg["llm"].update(enabled=True, base_url="https://llm.example", api_key="x")
    with TestClient(app) as c:
        _, evs = chat(c, "make a website for Noor Bakery")
        assert ev(evs, "done")[0]["mode"] == "builder"


def test_private_mode_saves_nothing(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        assert c.get("/api/models").json()["private"] is True
        r = c.post("/api/chat", json={"message": "hello", "private": True}, headers={"X-Client-Id": "q" * 24})
        assert r.status_code == 200 and "Hello! How can I help you today?" in r.text
        assert c.get("/api/conversations", headers={"X-Client-Id": "q" * 24}).json()["conversations"] == []


def test_think_blocks_are_never_shown():
    import asyncio

    async def run(parts):
        async def gen():
            for x in parts:
                yield x
        return "".join([t async for t in llm_mod._strip_think(gen())])
    assert asyncio.run(run(["<think>secret plan</think>", "Hello ", "there"])) == "Hello there"
    assert asyncio.run(run(["<thi", "nk>hid", "den</th", "ink>\n\nAnswer: 2 < 3"])) == "Answer: 2 < 3"
    assert asyncio.run(run(["plain text"])) == "plain text"
    assert asyncio.run(run(["a <b> c"])) == "a <b> c"


def _fb(c, cid, q, a, vote):
    return c.post("/api/feedback", json={"question": q, "answer": a, "vote": vote}, headers={"X-Client-Id": cid * 24}).json()["result"]


def test_feedback_learning_needs_three_voters_and_ignores_links(tmp_path):
    cfg, app = build(tmp_path)
    ans = "A polite crawler obeys robots.txt and waits between requests to the same host."
    q = "What does a polite crawler do?"
    with TestClient(app) as c:
        assert _fb(c, "a", q, ans, "up") == "saved"
        _, e = chat(c, q)
        assert ev(e, "done")[0]["mode"] != "learned"            # ek vote kafi nahi
        _fb(c, "a", q, ans, "up")                               # wahi banda dobara: ginti nahi barhti
        _fb(c, "b", q, ans, "up")
        _, e = chat(c, q)
        assert ev(e, "done")[0]["mode"] != "learned"
        _fb(c, "c", q, ans, "up")
        _, e = chat(c, "what does a  POLITE crawler do")        # spaces/case ka farq nahi
        assert ev(e, "done")[0]["mode"] == "learned" and "robots.txt" in "".join(ev(e, "token"))
        _, e = chat(c, q, model="mus-7.01-deep")
        assert ev(e, "done")[0]["mode"] != "learned"            # Deep hamesha research
        _fb(c, "d", q, ans, "down"); _fb(c, "e", q, ans, "down")  # down > up/2: wapas band
        _, e = chat(c, q)
        assert ev(e, "done")[0]["mode"] != "learned"
        assert _fb(c, "f", "free money here", "Visit https://evil.example now to win big prizes today", "up") == "ignored"
        assert _fb(c, "f", "x", "a" * 30, "up") == "ignored"
        assert c.post("/api/feedback", json={"question": q, "vote": "sideways"}).status_code == 422


def test_feedback_down_creates_gap_and_static_is_cacheable(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        assert _fb(c, "g", "how do volcanoes form exactly", "some answer text that is long enough", "down") == "noted"
        from engine.db import DB
        db = DB(cfg["general"]["db_path"])
        assert any("volcanoes" in r["q"] for r in db.q("SELECT q FROM gaps"))
        db.conn.close()
        assert "max-age" in c.get("/static/app.js").headers["cache-control"]


def test_grounded_llm_path_sends_key_and_hides_think(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.headers.get("authorization"))
        chunks = ["<think>plan</think>", "A polite crawler waits between requests. [1]"]
        sse = "".join("data: " + json.dumps({"choices": [{"delta": {"content": t}}]}) + "\n\n" for t in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    real = httpx.AsyncClient

    class Fake(real):
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **k)
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", Fake)

    async def alive(url):
        return True
    import engine.brain as brain_mod
    monkeypatch.setattr(brain_mod, "llm_alive", alive)
    cfg, app = build(tmp_path)
    cfg["llm"].update(enabled=True, base_url="http://ai.example:8081", api_key="sekret", model="")
    with TestClient(app) as c:
        _, evs = chat(c, "what does a polite crawler do", model="mus-7.01-flash")
        text = "".join(ev(evs, "token"))
        assert "plan" not in text and "sekret" not in text
    assert "Bearer sekret" in calls
