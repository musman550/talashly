"""Asli browser (Chromium) mein poori chat website ka end-to-end test. Playwright na ho to skip."""
import asyncio
import socket
import threading
import time

import pytest

pw = pytest.importorskip("playwright.sync_api")

import uvicorn  # noqa: E402

from engine.db import DB  # noqa: E402
from engine.server import create_app  # noqa: E402
from tests.helpers import make_cfg, seed_docs  # noqa: E402

TXT = ("A polite crawler obeys robots.txt and waits between requests to the same host. "
       "It identifies itself with a clear user agent and respects the crawl delay. ") * 4
EVIL = ("The zorbulator widget guide explains the zorbulator configuration in detail for operators. "
        "A zorbulator must be calibrated before use and the zorbulator manual lists the steps. ") * 4


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ui")
    cfg = make_cfg(tmp, brain={"live_research": False})
    cfg["chat"]["anon_rate_per_min"] = 1000
    cfg["chat"]["private_default"] = False  # history-wale tests; private mode alag test mein
    cfg["server"]["rate_per_min"] = 1000
    db = DB(cfg["general"]["db_path"])
    seed_docs(db, [("http://t.test/crawler", "Understanding web crawlers", TXT),
                   ("http://t.test/evil", "<img src=x onerror=window.__xss=1>Zorbulator <b>guide</b>", EVIL)])
    db.conn.close()
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    cfg["site"]["url"] = f"http://127.0.0.1:{port}"
    app = create_app(cfg, seeds=[], sources=[], crawl=False)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}", app
    srv.should_exit = True
    t.join(5)


@pytest.fixture()
def page(server):
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1200, "height": 800})
        pg = ctx.new_page()
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
        pg.dialogs = []
        pg.on("dialog", lambda d: (pg.dialogs.append(d.message), d.dismiss()))
        yield pg
        b.close()


def ask(pg, text):
    pg.fill("#in", text)
    pg.click("#send")
    pg.wait_for_selector("#send:has-text('Send')", timeout=15000)


def test_home_loads_clean(server, page):
    url, _ = server
    page.goto(url)
    page.wait_for_selector("#model option", state="attached")
    assert page.locator("#model option").count() == 5
    assert page.locator("h1").inner_text().startswith("What do you want")
    assert page.title().startswith("Talashly")
    assert page.errors == []


def test_chat_stream_citations_history_and_reload(server, page):
    url, _ = server
    page.goto(url)
    page.wait_for_selector("#model option", state="attached")
    ask(page, "what does a polite crawler do, with sources")
    body = page.locator(".msg.bot .body").last
    assert "robots.txt" in body.inner_text()
    cites = page.locator(".msg.bot a.cite")
    assert cites.count() >= 1
    assert cites.first.get_attribute("target") == "_blank" and "noopener" in cites.first.get_attribute("rel")
    assert page.locator(".srcs a.src").count() >= 1
    assert "mus-7.01" in page.locator(".msg.bot .foot").last.inner_text()
    assert page.locator(".conv").count() == 1
    # reload: history sidebar se wapas
    page.reload()
    page.wait_for_selector(".conv .t")
    page.click(".conv .t")
    page.wait_for_selector(".msg.bot .body")
    assert page.locator(".msg.user").count() == 1 and "robots.txt" in page.locator(".msg.bot .body").inner_text()
    # follow-up in same conversation
    ask(page, "why is that important? with sources")
    assert page.locator(".msg.user").count() == 2
    # delete
    page.hover(".conv")
    page.click(".conv .x")
    page.wait_for_selector("#welcome:not([hidden])")
    assert page.locator(".conv").count() == 0
    assert page.errors == []


def test_small_talk_and_no_answer_are_honest(server, page):
    url, _ = server
    page.goto(url)
    page.wait_for_selector("#model option", state="attached")
    ask(page, "hy")
    assert page.locator(".msg.bot .body").last.inner_text().strip() == "Hello! How can I help you today?"
    ask(page, "salam")
    assert page.locator(".srcs").count() == 0
    assert any(w in page.locator(".msg.bot .body").last.inner_text() for w in ("Assalam", "Salam"))
    ask(page, "qwertyuiop asdfghjkl unknownthing")
    assert "good answer" in page.locator(".msg.bot .body").last.inner_text()


def test_xss_in_titles_is_inert(server, page):
    url, _ = server
    page.goto(url)
    page.wait_for_selector("#model option", state="attached")
    ask(page, "zorbulator configuration, with sources")
    assert page.evaluate("window.__xss") is None
    assert page.locator(".srcs img").count() == 0 and page.locator(".msg.bot img").count() == 0
    assert "<img" in page.locator(".srcs").inner_text()  # literal text, markup nahi
    assert page.dialogs == []


def test_model_switch_persists(server, page):
    url, _ = server
    page.goto(url)
    page.wait_for_selector("#model option", state="attached")
    page.select_option("#model", "mus-7.01-flash")
    assert "Index only" in page.locator("#mdesc").inner_text()
    ask(page, "polite crawler")
    assert page.locator(".srcs").count() == 0 and "[1]" not in page.locator(".msg.bot .body").last.inner_text()
    assert "mus-7.01-flash" in page.locator(".msg.bot .foot").last.inner_text()
    page.reload()
    page.wait_for_selector("#model option", state="attached")
    assert page.input_value("#model") == "mus-7.01-flash"


def test_query_param_autosends_and_rtl(server, page):
    url, _ = server
    page.goto(url + "/?q=polite%20crawler")
    page.wait_for_selector(".msg.bot .body p", timeout=15000)
    assert page.locator(".msg.user .bubble").first.get_attribute("dir") == "auto"
    assert page.locator("#in").get_attribute("dir") == "auto"


def test_api_key_dialog_creates_working_key(server, page):
    url, _ = server
    page.goto(url)
    page.click("#keybtn")
    page.click("#keymake")
    page.wait_for_function("document.getElementById('keyin').value.startsWith('ss_')")
    key = page.input_value("#keyin")
    assert "curl" in page.locator("#keyex").inner_text()
    import httpx
    r = httpx.get(url + "/api/me", headers={"Authorization": f"Bearer {key}"})
    assert r.json()["authenticated"] is True
    # ab chat us key se chalti hai
    page.click("#keyclose")
    ask(page, "polite crawler")
    assert "robots.txt" in page.locator(".msg.bot .body").last.inner_text()
    assert page.errors == []


def test_stop_button_aborts_cleanly(server, page):
    url, app = server
    real = type(app.state.brain).run

    async def slow(self, *a, **k):
        yield "token", "partial "
        await asyncio.sleep(5)
        yield "done", {"mode": "extractive"}
    type(app.state.brain).run = slow
    try:
        page.goto(url)
        page.wait_for_selector("#model option", state="attached")
        page.fill("#in", "polite crawler")
        page.click("#send")
        page.wait_for_selector("#send:has-text('Stop')")
        page.wait_for_selector(".msg.bot .body p")
        page.click("#send")
        page.wait_for_selector("#send:has-text('Send')", timeout=5000)
        assert "Stopped" in page.locator(".msg.bot .status").last.inner_text()
        assert "partial" in page.locator(".msg.bot .body").last.inner_text()
        # system zinda: dobara pooch sakte hain
    finally:
        type(app.state.brain).run = real
    ask(page, "polite crawler")
    assert "robots.txt" in page.locator(".msg.bot .body").last.inner_text()


def test_mobile_layout(server):
    url, _ = server
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_context(viewport={"width": 375, "height": 700}, device_scale_factor=1).new_page()
        pg.goto(url)
        pg.wait_for_selector("#model option", state="attached")
        assert pg.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        assert pg.is_visible("#menu") and not pg.is_visible("#side") or pg.evaluate(
            "getComputedStyle(document.getElementById('side')).transform") != "none"
        pg.click("#menu")
        pg.wait_for_timeout(300)
        assert pg.evaluate("document.getElementById('side').getBoundingClientRect().left >= 0")
        pg.click("#new")
        ask(pg, "polite crawler")
        assert pg.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        b.close()


def test_static_pages_render(server, page):
    url, _ = server
    for path in ("/about", "/api-docs"):
        page.goto(url + path)
        assert page.locator("h1").count() == 1
    assert page.errors == []
