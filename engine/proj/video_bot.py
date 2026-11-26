"""Video Edit Bot: ffmpeg (pip se, alag install nahi) + Flask dashboard + run.bat."""

FILES = {}

FILES["requirements.txt"] = "imageio-ffmpeg>=0.5\nflask>=3.0\n"

FILES["editor.py"] = r'''"""Video editing core: ffmpeg ko subprocess se chalata hai (imageio-ffmpeg apna ffmpeg saath laata hai).
CLI:  python editor.py trim in.mp4 out.mp4 --start 00:00:05 --end 00:00:20
      python editor.py concat out.mp4 a.mp4 b.mp4 c.mp4
      python editor.py audio in.mp4 music.mp3 out.mp4 [--mix]
      python editor.py resize in.mp4 out.mp4 --height 720
      python editor.py speed in.mp4 out.mp4 --factor 1.5
      python editor.py text in.mp4 out.mp4 --text "Hello" [--position bottom]
      python editor.py extract-audio in.mp4 out.mp3
      python editor.py info in.mp4
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
TIME_RE = re.compile(r"^\d+(\.\d+)?$|^(\d+:)?\d{1,2}:\d{1,2}(\.\d+)?$")


class EditError(RuntimeError):
    pass


def run(args: list[str]) -> str:
    p = subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise EditError(p.stderr.strip().splitlines()[-1] if p.stderr.strip() else "ffmpeg failed")
    return p.stdout


def _check_in(path: str) -> str:
    if not Path(path).is_file():
        raise EditError(f"file not found: {path}")
    return str(path)


def _check_time(t: str) -> str:
    if not TIME_RE.match(t):
        raise EditError(f"bad time '{t}' (use seconds like 12.5 or HH:MM:SS)")
    return t


def trim(src: str, dst: str, start: str = "0", end: str | None = None) -> str:
    args = ["-ss", _check_time(start), "-i", _check_in(src)]
    if end:
        args += ["-to", _check_time(end)]
    run(args + ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", dst])
    return dst


def concat(dst: str, *srcs: str) -> str:
    if len(srcs) < 2:
        raise EditError("need at least two videos")
    with tempfile.TemporaryDirectory() as td:
        args: list[str] = []
        for s in srcs:
            args += ["-i", _check_in(s)]
        n = len(srcs)
        # sab ko ek hi size/fps par la kar jodna (alag size ki clips bhi chalen)
        vf = "".join(f"[{i}:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}];" for i in range(n))
        af = "".join(f"[{i}:a]aresample=44100[a{i}];" for i in range(n))
        cat = "".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
        try:
            run(args + ["-filter_complex", vf + af + cat, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", dst])
        except EditError:
            # kisi clip mein audio na ho: sirf video jodo
            cat = "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
            run(args + ["-filter_complex", vf + cat, "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", dst])
    return dst


def add_audio(src: str, audio: str, dst: str, mix: bool = False) -> str:
    _check_in(src), _check_in(audio)
    if mix:
        run(["-i", src, "-i", audio, "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first[a]", "-map", "0:v", "-map", "[a]",
             "-c:v", "copy", "-c:a", "aac", "-shortest", dst])
    else:
        run(["-i", src, "-i", audio, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest", dst])
    return dst


def resize(src: str, dst: str, height: int = 720) -> str:
    if not 144 <= int(height) <= 2160:
        raise EditError("height must be between 144 and 2160")
    run(["-i", _check_in(src), "-vf", f"scale=-2:{int(height)}", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy", dst])
    return dst


def speed(src: str, dst: str, factor: float = 1.5) -> str:
    f = float(factor)
    if not 0.25 <= f <= 4:
        raise EditError("factor must be between 0.25 and 4")
    run(["-i", _check_in(src), "-filter_complex", f"[0:v]setpts=PTS/{f}[v];[0:a]atempo={f}[a]", "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "veryfast", dst])
    return dst


def add_text(src: str, dst: str, text: str, position: str = "bottom") -> str:
    """Text overlay (drawtext). Font ke liye ffmpeg ka default; text safe-escape."""
    safe = re.sub(r"[^\w\s.,!?-]", "", text)[:80]
    y = {"top": "h*0.08", "center": "(h-text_h)/2"}.get(position, "h*0.85")
    run(["-i", _check_in(src), "-vf", f"drawtext=text='{safe}':fontcolor=white:fontsize=h/18:box=1:boxcolor=black@0.5:x=(w-text_w)/2:y={y}",
         "-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy", dst])
    return dst


def extract_audio(src: str, dst: str) -> str:
    run(["-i", _check_in(src), "-vn", "-c:a", "libmp3lame", "-q:a", "2", dst])
    return dst


def info(src: str) -> dict:
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", _check_in(src)], capture_output=True, text=True)
    out = p.stderr
    dur = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", out)
    size = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", out)
    return {"duration_s": round(int(dur[1]) * 3600 + int(dur[2]) * 60 + float(dur[3]), 2) if dur else None,
            "width": int(size[1]) if size else None, "height": int(size[2]) if size else None, "has_audio": "Audio:" in out}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Video edit bot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("trim"); t.add_argument("src"); t.add_argument("dst"); t.add_argument("--start", default="0"); t.add_argument("--end")
    c = sub.add_parser("concat"); c.add_argument("dst"); c.add_argument("srcs", nargs="+")
    a = sub.add_parser("audio"); a.add_argument("src"); a.add_argument("audio"); a.add_argument("dst"); a.add_argument("--mix", action="store_true")
    r = sub.add_parser("resize"); r.add_argument("src"); r.add_argument("dst"); r.add_argument("--height", type=int, default=720)
    s = sub.add_parser("speed"); s.add_argument("src"); s.add_argument("dst"); s.add_argument("--factor", type=float, default=1.5)
    x = sub.add_parser("text"); x.add_argument("src"); x.add_argument("dst"); x.add_argument("--text", required=True); x.add_argument("--position", default="bottom")
    e = sub.add_parser("extract-audio"); e.add_argument("src"); e.add_argument("dst")
    i = sub.add_parser("info"); i.add_argument("src")
    n = ap.parse_args(argv)
    try:
        if n.cmd == "trim":
            print(trim(n.src, n.dst, n.start, n.end))
        elif n.cmd == "concat":
            print(concat(n.dst, *n.srcs))
        elif n.cmd == "audio":
            print(add_audio(n.src, n.audio, n.dst, n.mix))
        elif n.cmd == "resize":
            print(resize(n.src, n.dst, n.height))
        elif n.cmd == "speed":
            print(speed(n.src, n.dst, n.factor))
        elif n.cmd == "text":
            print(add_text(n.src, n.dst, n.text, n.position))
        elif n.cmd == "extract-audio":
            print(extract_audio(n.src, n.dst))
        else:
            print(info(n.src))
    except EditError as ex:
        print("Error:", ex, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

FILES["app.py"] = r'''"""Dashboard: http://127.0.0.1:5000  (sirf aap ke computer par; upload -> edit -> download)."""
import uuid
from pathlib import Path

from flask import Flask, abort, render_template_string, request, send_from_directory

import editor

BASE = Path(__file__).parent
WORK = BASE / "workspace"
WORK.mkdir(exist_ok=True)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024   # 2 GB

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Video Edit Bot</title>
<style>body{font:16px system-ui;max-width:760px;margin:2rem auto;padding:0 1rem;background:#0f172a;color:#e2e8f0}
h1{font-size:1.6rem}form{background:#1e293b;padding:1rem;border-radius:12px;margin:1rem 0}
label{display:block;margin:.5rem 0 .2rem;font-size:.9rem;color:#94a3b8}input,select{width:100%;padding:.5rem;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0}
button{margin-top:.8rem;padding:.6rem 1.2rem;border:0;border-radius:8px;background:#6366f1;color:#fff;font-weight:600;cursor:pointer}
.ok{background:#14532d;padding:.8rem;border-radius:8px}.err{background:#7f1d1d;padding:.8rem;border-radius:8px}a{color:#a5b4fc}</style>
<h1>Video Edit Bot</h1>{% if msg %}<div class="{{cls}}">{{msg|safe}}</div>{% endif %}
<form method=post enctype=multipart/form-data><label>Action</label>
<select name=action><option value=trim>Trim (start/end)</option><option value=resize>Resize (height)</option><option value=speed>Speed</option>
<option value=text>Add text</option><option value=extract>Extract audio (mp3)</option></select>
<label>Video file</label><input type=file name=video required accept="video/*">
<label>Start (e.g. 5 or 00:00:05)</label><input name=start value="0"><label>End (optional)</label><input name=end>
<label>Height (resize)</label><input name=height value="720"><label>Speed factor</label><input name=factor value="1.5">
<label>Text</label><input name=text><button>Run</button></form>
<p>Files stay on your computer in the <code>workspace</code> folder.</p>"""


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_template_string(PAGE)
    f = request.files.get("video")
    if not f or not f.filename:
        return render_template_string(PAGE, msg="Choose a video first.", cls="err")
    uid = uuid.uuid4().hex[:8]
    src = WORK / f"{uid}_in{Path(f.filename).suffix.lower()[:6] or '.mp4'}"
    f.save(src)
    act = request.form.get("action", "trim")
    try:
        if act == "extract":
            out = WORK / f"{uid}_out.mp3"
            editor.extract_audio(str(src), str(out))
        else:
            out = WORK / f"{uid}_out.mp4"
            if act == "trim":
                editor.trim(str(src), str(out), request.form.get("start") or "0", request.form.get("end") or None)
            elif act == "resize":
                editor.resize(str(src), str(out), int(request.form.get("height") or 720))
            elif act == "speed":
                editor.speed(str(src), str(out), float(request.form.get("factor") or 1.5))
            elif act == "text":
                editor.add_text(str(src), str(out), request.form.get("text") or "Hello")
            else:
                abort(400)
    except (editor.EditError, ValueError) as e:
        return render_template_string(PAGE, msg=f"Error: {e}", cls="err")
    return render_template_string(PAGE, msg=f'Done. <a href="/dl/{out.name}">Download {out.name}</a>', cls="ok")


@app.route("/dl/<name>")
def dl(name):
    if "/" in name or "\\" in name or ".." in name:
        abort(404)
    return send_from_directory(WORK, name, as_attachment=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
'''

FILES["run.bat"] = r'''@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul || (echo Python is not installed. Get it from https://www.python.org/downloads/ and tick "Add to PATH". & pause & exit /b 1)
if not exist venv (
  echo Creating virtual environment...
  python -m venv venv || (echo Could not create venv & pause & exit /b 1)
)
call venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt || (echo Install failed & pause & exit /b 1)
echo Starting dashboard at http://127.0.0.1:5000
start "" http://127.0.0.1:5000
python app.py
pause
'''

FILES["run.sh"] = '''#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -d venv ] || python3 -m venv venv
. venv/bin/activate
pip install -q -r requirements.txt
python app.py
'''

FILES["tests/test_editor.py"] = r'''"""Chhota smoke test: ffmpeg se 2 second ki test video bana kar har function chalata hai.  python -m unittest discover tests"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import editor  # noqa: E402


class T(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.v = str(Path(self.td.name) / "a.mp4")
        subprocess.run([editor.FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=15",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest", "-c:v", "libx264", "-c:a", "aac", self.v], check=True)

    def out(self, n):
        return str(Path(self.td.name) / n)

    def test_trim_info(self):
        editor.trim(self.v, self.out("t.mp4"), "0", "1")
        self.assertLess(editor.info(self.out("t.mp4"))["duration_s"], 1.6)

    def test_concat_resize_speed_text_audio(self):
        editor.concat(self.out("c.mp4"), self.v, self.v)
        self.assertGreater(editor.info(self.out("c.mp4"))["duration_s"], 5)
        editor.resize(self.v, self.out("r.mp4"), 144)
        self.assertEqual(editor.info(self.out("r.mp4"))["height"], 144)
        editor.speed(self.v, self.out("s.mp4"), 2)
        self.assertLess(editor.info(self.out("s.mp4"))["duration_s"], 2)
        editor.add_text(self.v, self.out("x.mp4"), "Hello")
        editor.extract_audio(self.v, self.out("a.mp3"))
        editor.add_audio(self.v, self.out("a.mp3"), self.out("m.mp4"), mix=True)
        self.assertTrue(Path(self.out("m.mp4")).stat().st_size > 0)

    def test_bad_input(self):
        with self.assertRaises(editor.EditError):
            editor.trim("missing.mp4", self.out("z.mp4"))
        with self.assertRaises(editor.EditError):
            editor.trim(self.v, self.out("z.mp4"), "1; rm -rf")


if __name__ == "__main__":
    unittest.main()
'''

PLAN = """# Plan: Video Edit Bot

## Goal
Edit videos from a simple dashboard or the command line: trim, join, resize, speed up, add text, add music, extract audio.

## Architecture
- `editor.py`  core functions (ffmpeg via `imageio-ffmpeg`; no separate ffmpeg install needed).
- `app.py`     local Flask dashboard (upload, run, download). Works only on your computer (127.0.0.1).
- `run.bat`    Windows launcher: creates a virtual environment, installs packages, opens the dashboard.
- `tests/`     smoke tests that build a small test video and run every function.

## Roadmap
1. Phase 1 (done): trim, concat, resize, speed, text overlay, audio mix, audio extract.
2. Phase 2: subtitles (.srt burn-in), watermark image, transitions.
3. Phase 3: batch mode (a folder of videos), presets (YouTube Shorts 9:16, Instagram 1:1).
4. Phase 4: queue + progress bar in the dashboard; optional AI auto-captions (Whisper).

## Notes
Only edit videos you own or have permission to use.
"""

README = """# Video Edit Bot

## Quick start (Windows)
1. Install Python 3.10+ from https://www.python.org/downloads/ (tick **Add Python to PATH**).
2. Double-click **run.bat**. First run installs everything (about 1-2 minutes).
3. The dashboard opens at http://127.0.0.1:5000

## Quick start (Mac / Linux)
`bash run.sh`

## Command line
```
python editor.py trim in.mp4 out.mp4 --start 00:00:05 --end 00:00:20
python editor.py concat out.mp4 a.mp4 b.mp4
python editor.py audio in.mp4 music.mp3 out.mp4 --mix
python editor.py resize in.mp4 out.mp4 --height 720
python editor.py speed in.mp4 out.mp4 --factor 1.5
python editor.py text in.mp4 out.mp4 --text "Hello"
python editor.py extract-audio in.mp4 out.mp3
python editor.py info in.mp4
```

## Run the tests
`python -m unittest discover tests`

See **PLAN.md** for the roadmap.
"""
FILES["PLAN.md"] = PLAN
FILES["README.md"] = README
