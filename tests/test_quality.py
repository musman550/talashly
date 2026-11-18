"""Jhoote/bekaar jawab (hallucination) ke khilaf: asli browser (Chromium) + API se, ulte-seedhe sawal.
Corpus jaan-boojh kar gandi hai (khabar, legal boilerplate, aam alfaaz) jaise asli crawl mein hoti hai."""
import re
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from engine.db import DB
from engine.search import BOILER, coverage, terms
from engine.server import create_app
from tests.helpers import make_cfg, parse_sse, seed_docs

AI = ("Artificial intelligence (AI) is software that performs tasks that normally need human intelligence, such as "
      "understanding language, recognizing images and making decisions. Modern AI systems learn patterns from large amounts of data. ") * 2
LAKERS = ("“We just gotta play the same way,” Doncic said of the Lakers' problematic lead management in the fourth quarter. "
          "The Lakers' bench was short again and the coach said that defense must be better against the Warriors. ") * 3
LEGAL = ("We undertake no obligation to update, amend or clarify information in the Service or on any related website. "
         "We reserve the right to terminate your use of the Service or any related website for violating the prohibited uses. "
         "We are not liable for any harm related to third-party websites or the purchase of goods. ") * 3
PY = ("Python is a high-level programming language created by Guido van Rossum and first released in 1991. "
      "Python emphasizes readable code and supports multiple programming paradigms. ") * 3


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    import uvicorn
    tmp = tmp_path_factory.mktemp("q")
    cfg = make_cfg(tmp, brain={"live_research": False})
    cfg["chat"]["anon_rate_per_min"] = 10000
    cfg["server"]["rate_per_min"] = 10000
    db = DB(cfg["general"]["db_path"])
    seed_docs(db, [("http://t.test/ai", "What is AI", AI), ("http://t.test/lakers", "Lakers losing fourth quarters", LAKERS),
                   ("http://t.test/legal", "Terms of service", LEGAL), ("http://t.test/py", "About Python", PY)])
    db.conn.close()
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    cfg["site"]["url"] = f"http://127.0.0.1:{port}"
    app = create_app(cfg, seeds=[], sources=[], crawl=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}", cfg, app
    server.should_exit = True
    t.join(5)


def ask(c, q, model="mus-7.01", cid=None):
    r = c.post("/api/chat", json={"message": q, "private": True, "model": model}, headers={"X-Client-Id": (cid or "qq") * 12})
    evs = parse_sse(r.text)
    text = ""
    for n, d in evs:
        if n == "token":
            text += d
        elif n == "replace":
            text = d
    done = [d for n, d in evs if n == "done"][0]
    return text, done


def test_short_terms_match_whole_words_not_inside_other_words(srv):
    """'ai' dikhne ka matlab 'said'/'again' nahi: pehle yahi Lakers ki khabar jawab mein le aata tha."""
    url, cfg, app = srv
    with TestClient(app) as c:
        for q in ("what is ai tell me in short way?", "what is ai?", "AI kya hai"):
            text, done = ask(c, q)
            low = text.lower()
            assert "lakers" not in low and "doncic" not in low and "undertake no obligation" not in low, (q, text)
        text, _ = ask(c, "what is ai tell me in short way?")
        assert "artificial intelligence" in text.lower()


def test_boilerplate_and_generic_words_never_make_an_answer(srv):
    url, cfg, app = srv
    with TestClient(app) as c:
        for q in ("tell me about website related things", "explain the service obligation", "is the way short"):
            text, done = ask(c, q)
            assert "undertake no obligation" not in text and "reserve the right" not in text, (q, text)
        assert BOILER.search("we reserve the right to terminate")


def test_unrelated_questions_say_no_answer_instead_of_stitching_random_text(srv):
    url, cfg, app = srv
    with TestClient(app) as c:
        for q in ("what is the capital of Atlantis", "how tall is the moon landing tower", "zebra quantum saxophone recipe"):
            text, done = ask(c, q)
            assert "good answer" in text or "answers this directly" in text or "rephrase" in text, (q, text)
            assert not re.search(r"Python|Lakers|Artificial", text), (q, text)


def test_every_answer_sentence_really_contains_the_question_words(srv):
    """Property: jawab ka har jumla sawal ke asli alfaaz ka kam az kam aadha rakhta hai."""
    url, cfg, app = srv
    qs = ["who created python", "what is python programming language", "what is artificial intelligence",
          "lakers fourth quarter defense", "python released 1991", "how do ai systems learn from data"]
    with TestClient(app) as c:
        for q in qs:
            text, done = ask(c, q)
            if done["mode"] != "extractive":
                continue
            ts = terms(q)
            need = 1 if len(ts) <= 1 else max(2, (len(ts) + 1) // 2)
            for sent in re.split(r"(?<=[.!?])\s+", text):
                if not sent.strip():
                    continue
                words = set(re.findall(r"[\w'\-]+", sent.lower()))
                got = sum(1 for t in ts if t in words or (len(t) > 3 and any(w.startswith(t) for w in words)))
                assert got >= min(need, len(ts)), (q, sent)


def test_coverage_is_word_based():
    from engine.search import Hit
    h = Hit.__new__(Hit)
    h.passages, h.title = ["Doncic said again that the main plan failed."], "Lakers"
    assert coverage("what is ai", [h]) == 0.0


def test_chromium_user_flow_has_no_junk_and_builds_website(srv):
    pw = pytest.importorskip("playwright.sync_api")
    url, cfg, app = srv
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.goto(url)
        page.wait_for_selector("#model option", state="attached")

        def say(q):
            n = page.locator(".msg.bot").count()
            page.fill("#in", q)
            page.click("#send")
            page.wait_for_selector("#send:has-text('Send')", timeout=20000)
            page.wait_for_function("n => document.querySelectorAll('.msg.bot').length > n", arg=n)
            return page.locator(".msg.bot .body").last.inner_text()

        assert say("hy").strip() == "Hello! How can I help you today?"
        t = say("what is ai tell me in short way?")
        assert "Lakers" not in t and "undertake" not in t and "[1]" not in t
        assert page.locator(".srcs").count() == 0                                # sources chhupe
        t = say("make a website for tech related")
        assert "tailwindcss" in t and "<!doctype html>" in t.lower()
        assert "undertake" not in t
        t = say("what is the capital of Atlantis")
        assert "good answer" in t
        t = say("what is ai with sources")
        assert page.locator(".srcs, .src").count() > 0                           # maangne par sources aate hain
        b.close()


def test_chat_stays_scrolled_to_bottom_after_long_answer(srv):
    pw = pytest.importorskip("playwright.sync_api")
    url, cfg, app = srv
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_context(**p.devices["iPhone 13"]).new_page()
        page.goto(url)
        page.wait_for_selector("#model option", state="attached")
        for q in ("who are you", "make a website for Noor Bakery", "hy"):
            n = page.locator(".msg.bot").count()
            page.fill("#in", q)
            page.press("#in", "Enter")
            page.wait_for_function("n => document.querySelectorAll('.msg.bot').length > n", arg=n)
            page.wait_for_selector("#send:has-text('Send')")
            page.wait_for_timeout(250)
            gap = page.evaluate("(() => { const t = document.querySelector('#thread'); return t.scrollHeight - t.scrollTop - t.clientHeight; })()")
            assert gap <= 40, (q, gap)
        b.close()
