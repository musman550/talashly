"""Tayyar project templates: sawal se match -> asli ZIP (chalne wala code + README + PLAN). LLM ki zaroorat nahi.
ZIP sirf memory mein TTL tak rehta hai (chat/files disk par save nahi hote)."""
from __future__ import annotations

import io
import re
import secrets
import time
import zipfile

from .builder import _name

BUILD = re.compile(r"\b(make|build|create|develop|generate|write|bana|banao|bnao|bna|banado|bnado|chahiye|chahye)\b"
                   r"|script|\bbot\b|\bproject\b|\bzip\b|\bbat\b|source code", re.I)
QUESTION = re.compile(r"^\s*(what|why|how|who|when|where|which|is|are|does|do|can|kya|kiya|kyun|kaise|kese|explain|define)\b", re.I)
TOPICS: list[tuple[str, re.Pattern]] = [
    ("yt", re.compile(r"(you ?tube|\byt\b|ytube).{0,25}(download|saver|grab)|download(er|ing)?.{0,25}(you ?tube|\byt\b)", re.I)),
    ("video", re.compile(r"(video|reels?|shorts?|clips?).{0,30}(\bbot\b|edit|editor|editing|automat|maker|generator|trim|cut)|(\bbot\b|editor).{0,20}video", re.I)),
    ("scraper", re.compile(r"scrap(er|ing|e)\b|crawler|web ?scraping|data extract", re.I)),
    ("shop", re.compile(r"(shopify|e-?commerce|online (shop|store)|\bshop\b|\bstore\b|dukaan|dukan).{0,25}(website|web ?site|\bsite\b|webpage|page)"
                        r"|(website|web ?site|\bsite\b).{0,20}(shopify|e-?commerce|online (shop|store)|\bshop\b|\bstore\b)", re.I)),
]
TTL_S = 1800
MAX_ITEMS = 200
MAX_BYTES = 5_000_000
_STORE: dict[str, tuple[float, bytes, str]] = {}


def match(text: str) -> str | None:
    t = text or ""
    if not BUILD.search(t) or (QUESTION.match(t) and not re.search(r"\b(make|build|create|write|bana|bnao|zip)\b", t, re.I)):
        return None
    for key, rx in TOPICS:
        if rx.search(t):
            return key
    return None


def _files(key: str, text: str) -> tuple[str, dict[str, str]]:
    if key == "yt":
        from .proj.yt_downloader import FILES
        return "youtube-downloader", dict(FILES)
    if key == "video":
        from .proj.video_bot import FILES
        return "video-bot", dict(FILES)
    if key == "scraper":
        from .proj.scraper import FILES
        return "web-scraper", dict(FILES)
    from .proj.shop_site import files
    brand = _name(text)
    brand = "Your Store" if brand == "Your Brand" else brand
    return re.sub(r"[^a-z0-9]+", "-", brand.lower()).strip("-") or "shop-website", files(brand)


def make_zip(folder: str, files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, content in files.items():
            z.writestr(f"{folder}/{path}", content)
    return buf.getvalue()


def _sweep() -> None:
    now = time.time()
    for k in [k for k, v in _STORE.items() if v[0] < now]:
        _STORE.pop(k, None)
    while len(_STORE) >= MAX_ITEMS:
        _STORE.pop(min(_STORE, key=lambda k: _STORE[k][0]), None)


def put(name: str, data: bytes) -> str | None:
    if len(data) > MAX_BYTES:
        return None
    _sweep()
    token = secrets.token_urlsafe(18)
    _STORE[token] = (time.time() + TTL_S, data, name)
    return token


def get(token: str) -> tuple[bytes, str] | None:
    v = _STORE.get(token or "")
    if not v or v[0] < time.time():
        _STORE.pop(token or "", None)
        return None
    return v[1], v[2]


HOW = {
    "yt": ("YouTube downloader (yt-dlp + ffmpeg, web dashboard + command line)",
           "1. ZIP extract karein  2. `run.bat` double-click (Windows) ya `pip install -r requirements.txt` phir `python app.py`  3. Browser: http://127.0.0.1:5000\nSirf apni ya aisi videos download karein jin ki ijazat ho."),
    "video": ("Video editing bot (trim, join, audio, resize, speed, text; ffmpeg saath aata hai)",
              "1. ZIP extract karein  2. `run.bat` (Windows) ya `pip install -r requirements.txt` phir `python app.py`  3. Browser: http://127.0.0.1:5000\nCommand line: `python editor.py --help`"),
    "scraper": ("Polite web scraper (robots.txt ka khayal, CSV/JSON output, dashboard)",
                "1. ZIP extract karein  2. `run.bat` ya `pip install -r requirements.txt` phir `python scraper.py --help`  3. Dashboard: `python app.py`\nSirf wohi sites scrape karein jin ki terms ijazat dein."),
    "shop": ("Shopify-style shop website (HTML + CSS + JavaScript: products, search, cart, checkout form)",
             "1. ZIP extract karein  2. `index.html` double-click, bas.\nProducts `app.js` ke upar `PRODUCTS` mein badlein. Live karne ke liye Netlify/GitHub Pages par folder daal dein."),
}


def build(text: str) -> dict | None:
    """-> {text, url, name, size, files} ya None."""
    key = match(text)
    if not key:
        return None
    folder, files = _files(key, text)
    data = make_zip(folder, files)
    name = f"{folder}.zip"
    token = put(name, data)
    if not token:
        return None
    title, how = HOW[key]
    listing = "\n".join(f"- {p}" for p in sorted(files))
    body = (f"**{title}**\n\nMukammal project tayar hai, neeche ZIP download karein ({len(data) // 1024 + 1} KB, {len(files)} files, tests aur PLAN.md samet).\n\n"
            f"Download: /api/dl/{token}\n\nKaise chalayein:\n{how}\n\nFiles:\n{listing}\n\n"
            "Link 30 minute tak chalta hai. Kuch badalwana ho (naye features, rang, naam) to likh dein.")
    return {"text": body, "url": f"/api/dl/{token}", "name": name, "size": len(data), "files": len(files)}
