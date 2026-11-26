"""Video/audio downloader (yt-dlp) + dashboard + run.bat."""

FILES = {}
FILES["requirements.txt"] = "yt-dlp>=2024.8.6\nimageio-ffmpeg>=0.5\nflask>=3.0\n"

FILES["downloader.py"] = r'''"""yt-dlp wrapper. CLI:
  python downloader.py "https://..." [--audio] [--quality 720] [--out downloads]
Sirf wo content download karein jo aap ka apna ho ya jis ki ijazat ho; site ke terms ka khayal rakhein.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import imageio_ffmpeg
import yt_dlp

URL_RE = re.compile(r"^https?://[^\s]+$")


class DownloadError(RuntimeError):
    pass


def build_options(out_dir: str, audio: bool = False, quality: int = 720) -> dict:
    if not 144 <= int(quality) <= 2160:
        raise DownloadError("quality must be between 144 and 2160")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    opts = {"outtmpl": str(Path(out_dir) / "%(title).80s [%(id)s].%(ext)s"), "noplaylist": True, "quiet": True, "no_warnings": True,
            "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(), "restrictfilenames": False, "retries": 3}
    if audio:
        opts.update(format="bestaudio/best", postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}])
    else:
        opts.update(format=f"bestvideo[height<={int(quality)}]+bestaudio/best[height<={int(quality)}]/best", merge_output_format="mp4")
    return opts


def download(url: str, out_dir: str = "downloads", audio: bool = False, quality: int = 720) -> str:
    if not URL_RE.match(url.strip()):
        raise DownloadError("please give a full http(s) link")
    try:
        with yt_dlp.YoutubeDL(build_options(out_dir, audio, quality)) as ydl:
            info = ydl.extract_info(url.strip(), download=True)
            path = Path(ydl.prepare_filename(info))
            if audio:
                path = path.with_suffix(".mp3")
            elif path.suffix != ".mp4" and path.with_suffix(".mp4").exists():
                path = path.with_suffix(".mp4")
            return str(path)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(str(e).replace("ERROR: ", "")[:300]) from e


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Video downloader")
    ap.add_argument("url")
    ap.add_argument("--audio", action="store_true", help="mp3 only")
    ap.add_argument("--quality", type=int, default=720)
    ap.add_argument("--out", default="downloads")
    n = ap.parse_args(argv)
    try:
        print("Saved:", download(n.url, n.out, n.audio, n.quality))
    except DownloadError as e:
        print("Error:", e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

FILES["app.py"] = r'''"""Dashboard: http://127.0.0.1:5000 (sirf aap ke computer par)."""
from pathlib import Path

from flask import Flask, abort, render_template_string, request, send_from_directory

import downloader

OUT = Path(__file__).parent / "downloads"
OUT.mkdir(exist_ok=True)
app = Flask(__name__)
PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Downloader</title>
<style>body{font:16px system-ui;max-width:700px;margin:2rem auto;padding:0 1rem;background:#0f172a;color:#e2e8f0}
form{background:#1e293b;padding:1rem;border-radius:12px}input,select{width:100%;padding:.55rem;margin:.3rem 0 .8rem;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0}
button{padding:.6rem 1.2rem;border:0;border-radius:8px;background:#6366f1;color:#fff;font-weight:600}.ok{background:#14532d;padding:.8rem;border-radius:8px;margin:1rem 0}
.err{background:#7f1d1d;padding:.8rem;border-radius:8px;margin:1rem 0}a{color:#a5b4fc}</style>
<h1>Downloader</h1>{% if msg %}<div class="{{cls}}">{{msg|safe}}</div>{% endif %}
<form method=post><label>Link</label><input name=url placeholder="https://..." required>
<label>Type</label><select name=kind><option value=video>Video (mp4)</option><option value=audio>Audio only (mp3)</option></select>
<label>Max quality</label><select name=q><option>360</option><option selected>720</option><option>1080</option></select><button>Download</button></form>
<p>Only download content you own or have permission to use.</p>
<h3>Files</h3>{% for f in files %}<div><a href="/f/{{f}}">{{f}}</a></div>{% else %}<p>Nothing yet.</p>{% endfor %}"""


def files():
    return sorted((p.name for p in OUT.iterdir() if p.is_file()), reverse=True)[:30]


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        try:
            path = downloader.download(request.form.get("url", ""), str(OUT), request.form.get("kind") == "audio", int(request.form.get("q", 720)))
            return render_template_string(PAGE, msg=f"Saved <b>{Path(path).name}</b>", cls="ok", files=files())
        except (downloader.DownloadError, ValueError) as e:
            return render_template_string(PAGE, msg=f"Error: {e}", cls="err", files=files())
    return render_template_string(PAGE, files=files())


@app.route("/f/<name>")
def get(name):
    if "/" in name or "\\" in name or ".." in name:
        abort(404)
    return send_from_directory(OUT, name, as_attachment=True)


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
python -m pip install --quiet --upgrade -r requirements.txt || (echo Install failed & pause & exit /b 1)
echo Starting dashboard at http://127.0.0.1:5000
start "" http://127.0.0.1:5000
python app.py
pause
'''
FILES["tests/test_downloader.py"] = r'''import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import downloader  # noqa: E402


class T(unittest.TestCase):
    def test_options(self):
        with tempfile.TemporaryDirectory() as td:
            v = downloader.build_options(td, False, 720)
            self.assertIn("height<=720", v["format"])
            a = downloader.build_options(td, True)
            self.assertEqual(a["postprocessors"][0]["preferredcodec"], "mp3")

    def test_validation(self):
        with self.assertRaises(downloader.DownloadError):
            downloader.download("not a url")
        with self.assertRaises(downloader.DownloadError):
            downloader.build_options("x", False, 99999)


if __name__ == "__main__":
    unittest.main()
'''
FILES["PLAN.md"] = """# Plan: Downloader

## Goal
Download video (mp4) or audio (mp3) from a link, from a simple local dashboard or the command line.

## Architecture
- `downloader.py` uses yt-dlp (kept up to date by run.bat) and a bundled ffmpeg for merging and mp3 conversion.
- `app.py` local dashboard on 127.0.0.1.
- `run.bat` creates the virtual environment, installs/updates packages, opens the dashboard.

## Roadmap
1. Phase 1 (done): single link, quality choice, mp3.
2. Phase 2: playlist support, download queue and progress bar.
3. Phase 3: subtitle download, scheduled downloads.

## Notes
Only download content you own or have permission to use. Websites change often: if a download fails, run run.bat again to update yt-dlp.
"""
FILES["README.md"] = """# Downloader

1. Install Python 3.10+ (tick **Add Python to PATH**).
2. Double-click **run.bat** (first run installs packages; each run updates yt-dlp).
3. Paste a link in the dashboard at http://127.0.0.1:5000

Command line: `python downloader.py "https://..." --audio` or `--quality 1080`.
Tests: `python -m unittest discover tests`. Roadmap: **PLAN.md**.
"""
