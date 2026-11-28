"""Project ZIPs: match, asli zip, download endpoint, templates chalte hain, hints, naye smalltalk patterns."""
import io
import os
import subprocess
import sys
import zipfile

from fastapi.testclient import TestClient

from engine import projects
from engine.chat import smalltalk
from engine.hints import extra
from engine.models import get_model
from tests.test_agent import build, chat, ev


def test_match_topics():
    assert projects.match("make a youtube video downloader in python") == "yt"
    assert projects.match("mujy youtube downloader bnao zip do") == "yt"
    assert projects.match("video editing bot bana do python") == "video"
    assert projects.match("write a web scraper script") == "scraper"
    assert projects.match("build me a shopify style ecommerce website") == "shop"
    assert projects.match("make a shop website for Noor Store") == "shop"
    assert projects.match("what is the capital of france") is None
    assert projects.match("how do i download a file") is None
    assert projects.match("youtube kya hai") is None
    assert projects.match("what does a polite crawler do") is None
    assert projects.match("what is web scraping") is None


def test_build_zip_valid_and_token_expiry(monkeypatch):
    r = projects.build("build a youtube downloader in python")
    z = zipfile.ZipFile(io.BytesIO(projects.get(r["url"].rsplit("/", 1)[1])[0]))
    names = z.namelist()
    assert any(n.endswith("downloader.py") for n in names) and any(n.endswith("README.md") for n in names)
    assert z.testzip() is None
    tok = r["url"].rsplit("/", 1)[1]
    monkeypatch.setattr(projects, "TTL_S", -1)
    t2 = projects.put("x.zip", b"abc")
    assert projects.get(t2) is None and projects.get(tok) is not None  # tok abhi tak valid (purani TTL)
    assert projects.get("nope") is None


def test_shop_uses_brand_and_html_valid():
    r = projects.build("make a shopify ecommerce website for Noor Store")
    z = zipfile.ZipFile(io.BytesIO(projects.get(r["url"].rsplit("/", 1)[1])[0]))
    idx = [n for n in z.namelist() if n.endswith("index.html")][0]
    html = z.read(idx).decode()
    assert "Noor Store" in html and "Your Brand" not in html and "app.js" in z.namelist()[0] + "".join(z.namelist())


def test_python_templates_compile_and_smoke(tmp_path):
    for q in ("web scraper script", "video editing bot python"):
        r = projects.build("make a " + q)
        d = tmp_path / q.split()[0]
        zipfile.ZipFile(io.BytesIO(projects.get(r["url"].rsplit("/", 1)[1])[0])).extractall(d)
        root = next(d.iterdir())
        for p in root.rglob("*.py"):
            compile(p.read_text(), str(p), "exec")


def test_chat_returns_download_and_endpoint(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        evs = chat(c, "mujy youtube video downloader python mein bnao zip do")[1]
        dl = ev(evs, "download")
        assert dl and dl[0]["url"].startswith("/api/dl/") and dl[0]["name"].endswith(".zip")
        assert ev(evs, "done")[0]["mode"] == "project"
        r = c.get(dl[0]["url"])
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        assert zipfile.ZipFile(io.BytesIO(r.content)).testzip() is None
        assert c.get("/api/dl/bogus").status_code == 404


def test_hints():
    assert "yt-dlp" in extra("youtube video download script")
    assert "Roman Urdu" in extra("mujy code bnao kasy kru")
    assert extra("what is 2+2") == ""


def test_roman_faq():
    M = get_model(None)
    for q in ("tum kya kar sakty ho", "kya ye open source hai", "mujhse roman urdu mein baat karo"):
        assert smalltalk(q, M), q


def test_hints_same_language_note_and_load_cap(tmp_path):
    assert "same language" in extra("Erkläre inflation ausführlich: die Grundidee, ein Beispiel und mehr bitte der die das")
    assert "same language" in extra("एक छोटे व्यवसाय के लिए योजना बताइए")
    assert "same language" not in extra("explain recursion")
    cfg, app = build(tmp_path)
    ch = app.state.chat if hasattr(app.state, "chat") else None
    if ch is not None:
        ch.waiting = 0
        assert ch._load_cap(700) == 700
        ch.waiting = 15
        assert ch._load_cap(700) == 450 and ch._load_cap(4000) == 4000
        ch.waiting = 50
        assert ch._load_cap(700) == 300
        ch.waiting = 0


def test_long_question_with_busy_llm_gets_honest_busy_not_fake_nosent(tmp_path, monkeypatch):
    from tests.test_agent import _cfg_llm
    from engine import chat as chat_mod

    async def boom(*a, **k):
        raise RuntimeError("all nodes busy")
        yield ""
    monkeypatch.setattr(chat_mod, "stream_chat", boom)
    cfg, app = build(tmp_path)
    _cfg_llm(cfg)
    with TestClient(app) as c:
        evs = chat(c, "Explique en détail comment fonctionne le chiffrement à clé publique, avec un exemple concret et deux erreurs courantes.")[1]
    d = ev(evs, "done")[0]
    assert d["mode"] == "busy" and "related information" not in "".join(ev(evs, "token"))
