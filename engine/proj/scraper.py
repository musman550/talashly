"""Web scraper (requests + BeautifulSoup): robots.txt ka khayal, delay, CSV/JSON, dashboard, run.bat."""

FILES = {}
FILES["requirements.txt"] = "requests>=2.31\nbeautifulsoup4>=4.12\nflask>=3.0\n"

FILES["scraper.py"] = r'''"""Polite scraper. CLI:
  python scraper.py https://example.com --select "h2" --attr text --out result.csv
  python scraper.py https://example.com --select "a" --attr href --out links.json --pages 3 --delay 1.5
Robots.txt maani jati hai; zyada requests se bachne ke liye delay hai. Sirf wo sites jin ki ijazat ho.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = "PoliteScraper/1.0 (+personal use)"


class ScrapeError(RuntimeError):
    pass


def allowed(url: str, session: requests.Session | None = None) -> bool:
    p = urlparse(url)
    rp = urllib.robotparser.RobotFileParser()
    try:
        r = (session or requests).get(f"{p.scheme}://{p.netloc}/robots.txt", headers={"User-Agent": UA}, timeout=10)
        if r.status_code >= 400:
            return True
        rp.parse(r.text.splitlines())
    except requests.RequestException:
        return True
    return rp.can_fetch(UA, url)


def parse(html: str, selector: str, attr: str = "text", base: str = "") -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for el in soup.select(selector):
        v = el.get_text(" ", strip=True) if attr == "text" else el.get(attr)
        if v:
            out.append(urljoin(base, v) if attr in ("href", "src") and base else str(v))
    return out


def scrape(url: str, selector: str, attr: str = "text", pages: int = 1, delay: float = 1.0, next_selector: str | None = None) -> list[dict]:
    if urlparse(url).scheme not in ("http", "https"):
        raise ScrapeError("use a full http(s) link")
    rows: list[dict] = []
    s = requests.Session()
    s.headers["User-Agent"] = UA
    cur: str | None = url
    for i in range(max(1, min(pages, 50))):
        if not cur:
            break
        if not allowed(cur, s):
            raise ScrapeError(f"robots.txt does not allow scraping {cur}")
        try:
            r = s.get(cur, timeout=15)
            r.raise_for_status()
        except requests.RequestException as e:
            raise ScrapeError(f"could not fetch {cur}: {e}") from e
        rows += [{"page": i + 1, "url": cur, "value": v} for v in parse(r.text, selector, attr, cur)]
        cur = None
        if next_selector:
            nxt = parse(r.text, next_selector, "href", r.url)
            cur = nxt[0] if nxt else None
        if cur:
            time.sleep(max(0.0, delay))
    return rows


def save(rows: list[dict], path: str) -> str:
    if path.lower().endswith(".json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
    else:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["page", "url", "value"])
            w.writeheader()
            w.writerows(rows)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Polite web scraper")
    ap.add_argument("url")
    ap.add_argument("--select", required=True, help='CSS selector, e.g. "h2" or "a.title"')
    ap.add_argument("--attr", default="text", help="text, href, src ...")
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--next", dest="nxt", help='CSS selector of the "next page" link')
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--out", default="result.csv")
    n = ap.parse_args(argv)
    try:
        rows = scrape(n.url, n.select, n.attr, n.pages, n.delay, n.nxt)
    except ScrapeError as e:
        print("Error:", e, file=sys.stderr)
        return 1
    print(f"{len(rows)} items saved to {save(rows, n.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
FILES["app.py"] = r'''"""Dashboard: http://127.0.0.1:5000"""
from flask import Flask, render_template_string, request

import scraper

app = Flask(__name__)
PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Scraper</title>
<style>body{font:16px system-ui;max-width:860px;margin:2rem auto;padding:0 1rem;background:#0f172a;color:#e2e8f0}
form{background:#1e293b;padding:1rem;border-radius:12px}input{width:100%;padding:.55rem;margin:.3rem 0 .8rem;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0}
button{padding:.6rem 1.2rem;border:0;border-radius:8px;background:#6366f1;color:#fff;font-weight:600}table{width:100%;border-collapse:collapse;margin-top:1rem}
td,th{border-bottom:1px solid #334155;padding:.4rem;text-align:left;word-break:break-all}.err{background:#7f1d1d;padding:.8rem;border-radius:8px;margin:1rem 0}</style>
<h1>Web Scraper</h1>{% if err %}<div class=err>{{err}}</div>{% endif %}
<form method=post><label>Page link</label><input name=url placeholder="https://example.com" required>
<label>CSS selector</label><input name=sel value="h2" required><label>Attribute (text, href, src)</label><input name=attr value="text">
<label>Pages</label><input name=pages value="1"><button>Scrape</button></form>
{% if rows %}<p>{{rows|length}} items</p><table><tr><th>#</th><th>Value</th></tr>{% for r in rows %}<tr><td>{{loop.index}}</td><td>{{r.value}}</td></tr>{% endfor %}</table>
<p><a href="#" onclick="return false">Tip: use the command line to save CSV/JSON.</a></p>{% endif %}
<p>Only scrape sites you are allowed to. Robots.txt is respected.</p>"""


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        try:
            rows = scraper.scrape(request.form["url"], request.form.get("sel", "h2"), request.form.get("attr", "text") or "text",
                                  int(request.form.get("pages", 1) or 1))
            return render_template_string(PAGE, rows=rows[:500])
        except (scraper.ScrapeError, ValueError) as e:
            return render_template_string(PAGE, err=str(e))
    return render_template_string(PAGE)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
'''
FILES["run.bat"] = r'''@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul || (echo Python is not installed. Get it from https://www.python.org/downloads/ and tick "Add to PATH". & pause & exit /b 1)
if not exist venv ( python -m venv venv || (echo Could not create venv & pause & exit /b 1) )
call venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt || (echo Install failed & pause & exit /b 1)
echo Starting dashboard at http://127.0.0.1:5000
start "" http://127.0.0.1:5000
python app.py
pause
'''
FILES["tests/test_scraper.py"] = r'''import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scraper  # noqa: E402

HTML = '<h2>One</h2><h2>Two</h2><a href="/x">x</a><a class="n" href="p2.html">next</a>'


class T(unittest.TestCase):
    def test_parse_text_and_links(self):
        self.assertEqual(scraper.parse(HTML, "h2"), ["One", "Two"])
        self.assertEqual(scraper.parse(HTML, "a", "href", "https://e.com/a/"), ["https://e.com/x", "https://e.com/a/p2.html"])

    def test_bad_scheme(self):
        with self.assertRaises(scraper.ScrapeError):
            scraper.scrape("file:///etc/passwd", "h2")


if __name__ == "__main__":
    unittest.main()
'''
FILES["PLAN.md"] = """# Plan: Web Scraper

## Goal
Collect text or links from web pages by CSS selector, save to CSV/JSON, with a simple dashboard.

## Architecture
`scraper.py` (requests + BeautifulSoup, robots.txt check, delay, pagination) · `app.py` (local dashboard) · `run.bat` (setup + launch) · `tests/`.

## Roadmap
1. Phase 1 (done): selector scraping, pagination, CSV/JSON, robots.txt.
2. Phase 2: JavaScript pages via Playwright, login sessions.
3. Phase 3: scheduled runs and change alerts.

## Notes
Scrape only sites you are allowed to; keep the delay polite.
"""
FILES["README.md"] = """# Web Scraper

1. Install Python 3.10+ (tick **Add Python to PATH**). 2. Double-click **run.bat**. 3. Use the dashboard at http://127.0.0.1:5000

Command line: `python scraper.py https://example.com --select "h2" --out result.csv`
Tests: `python -m unittest discover tests`. Roadmap: **PLAN.md**.
"""
