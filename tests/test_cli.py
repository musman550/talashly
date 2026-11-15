"""Asli process ki tarah: wahi commands jo .bat files chalati hain."""
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from engine.__main__ import main as cli_main
from engine.db import DB
from tests.helpers import seed_docs

ROOT = Path(__file__).resolve().parent.parent
TXT = ("A polite crawler obeys robots.txt and waits between requests to the same host. "
       "It identifies itself with a clear user agent and respects the crawl delay. ") * 4


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def setup_env(tmp: Path, port: int | None = None, extra_cfg: str = ""):
    db = tmp / "data" / "index.db"
    (tmp / "data").mkdir(exist_ok=True)
    cfg = tmp / "config.toml"
    cfg.write_text(f'[general]\ndb_path = "{db.as_posix()}"\n[server]\nport = {port or free_port()}\n{extra_cfg}')
    (tmp / "seeds.toml").write_text("")
    (tmp / "sources.toml").write_text("")
    env = dict(os.environ, SS_CONFIG=str(cfg), SS_SEEDS=str(tmp / "seeds.toml"), SS_SOURCES=str(tmp / "sources.toml"),
               PYTHONUTF8="1", BROWSER="true")
    return env, db


def cli(env, *args, timeout=90):
    return subprocess.run([sys.executable, "-m", "engine", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)


def populate(db_path: Path, n_extra=0):
    db = DB(str(db_path))
    seed_docs(db, [("http://t.test/crawler", "Understanding web crawlers", TXT)] +
              [(f"http://t.test/x{i}", f"Doc {i}", f"extra document {i} about restorable topic " * 30) for i in range(n_extra)])
    return db


class Server:
    def __init__(self, env, port, *extra):
        self.port = port
        self.p = subprocess.Popen([sys.executable, "-m", "engine", "serve", "--no-crawl", *extra], cwd=ROOT, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def wait_up(self, secs=40):
        for _ in range(int(secs * 10)):
            if self.p.poll() is not None:
                raise AssertionError(f"server band ho gaya: {self.p.stdout.read()}")
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/api/health", timeout=1).status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise AssertionError("server start nahi hua")

    def stop(self) -> int:
        if self.p.poll() is None:
            self.p.send_signal(signal.SIGINT)
            try:
                self.p.wait(15)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait()
        return self.p.returncode

    def get(self, path, **kw):
        return httpx.get(f"http://127.0.0.1:{self.port}{path}", timeout=10, **kw).json()


# ------------------------------------------------------------------ commands
def test_adduser_status_ask_indexcode(tmp_path):
    env, db_path = setup_env(tmp_path)
    populate(db_path)
    r = cli(env, "adduser", "alice")
    assert r.returncode == 0 and re.search(r"ss_[\w-]{20,}", r.stdout)
    assert cli(env, "adduser", "alice").returncode == 1                       # duplicate: saaf error
    assert "docs" in cli(env, "status").stdout
    a = cli(env, "ask", "what does a polite crawler do")
    assert a.returncode == 0 and "JAWAB" in a.stdout and "robots.txt" in a.stdout
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "m.py").write_text("def unique_function_name():\n    pass\n")
    i = cli(env, "index-code", str(proj))
    assert i.returncode == 0 and "'indexed': 1" in i.stdout
    assert cli(env, "index-code").returncode == 1                              # koi path nahi: saaf message


def test_unicode_output_survives_windows_console_encoding(tmp_path):
    env, db_path = setup_env(tmp_path)
    seed_docs(DB(str(db_path)), [("http://t.test/u", "اردو", "کریولر ویب پیجز کو پڑھتا ہے اور معلومات جمع کرتا ہے۔ " * 10)])
    env["PYTHONUTF8"] = "0"
    env["PYTHONIOENCODING"] = "cp1252"                                         # Windows console ki purani encoding
    r = cli(env, "ask", "کریولر معلومات")
    assert r.returncode == 0 and "Traceback" not in r.stderr and "UnicodeEncodeError" not in r.stderr
    assert "کریولر" in r.stdout


def test_doctor_reports_and_repairs_bad_config(tmp_path):
    env, db_path = setup_env(tmp_path, extra_cfg="[crawler]\nconcurrency = 99999\n")
    r = cli(env, "doctor")
    assert r.returncode in (0, 1)                                              # 1 sirf agar internet na ho
    assert "[OK] SQLite FTS5" in r.stdout and "config theek kiya gaya" in r.stdout and "crawler.concurrency" in r.stdout


def test_unknown_command_and_help(tmp_path):
    env, _ = setup_env(tmp_path)
    assert cli(env, "nonsense").returncode == 2
    h = cli(env)
    assert h.returncode == 0 and "serve" in h.stdout


# ------------------------------------------------------------------ real server process
def test_serve_start_api_stop_and_port_busy(tmp_path):
    port = free_port()
    env, db_path = setup_env(tmp_path, port)
    populate(db_path)
    s = Server(env, port, "--open").wait_up()
    try:
        assert s.get("/api/health")["index"]["docs"] == 1
        assert s.get("/api/search?q=polite+crawler")["results"][0]["url"].endswith("/crawler")
        assert (tmp_path / "data" / "admin_key.txt").read_text().startswith("ss_")
        busy = cli(env, "serve", "--no-crawl", timeout=30)
        assert busy.returncode == 2 and "pehle se istemal" in busy.stdout       # START.bat is code par restart-loop nahi chalata
    finally:
        rc = s.stop()
    assert rc in (0, -2, 130), rc


def test_serve_recovers_from_corrupt_database_using_backup(tmp_path):
    port = free_port()
    env, db_path = setup_env(tmp_path, port)
    db = populate(db_path, n_extra=25)
    db.backup(str(tmp_path / "data" / "backups"))
    db.x("PRAGMA wal_checkpoint(TRUNCATE)")
    db.conn.close()
    raw = bytearray(db_path.read_bytes())
    raw[:100] = b"X" * 100
    db_path.write_bytes(bytes(raw))
    s = Server(env, port).wait_up()
    try:
        assert s.get("/api/health")["index"]["docs"] == 26                     # backup se sab wapas
        assert s.get("/api/search?q=restorable+topic")["results"]
        assert list((tmp_path / "data").glob("index.db.corrupt-*"))
    finally:
        s.stop()


def test_serve_starts_fresh_when_corrupt_and_no_backup(tmp_path):
    port = free_port()
    env, db_path = setup_env(tmp_path, port)
    db_path.write_bytes(b"not a database" * 500)
    s = Server(env, port).wait_up()
    try:
        assert s.get("/api/health")["index"]["docs"] == 0 and s.get("/api/health")["status"] == "ok"
    finally:
        s.stop()


def test_restore_command_refuses_while_server_runs_and_works_when_stopped(tmp_path):
    port = free_port()
    env, db_path = setup_env(tmp_path, port)
    db = populate(db_path, n_extra=5)
    db.backup(str(tmp_path / "data" / "backups"))
    s = Server(env, port).wait_up()
    try:
        assert cli(env, "restore").returncode == 2
    finally:
        s.stop()
    r = cli(env, "restore")
    assert r.returncode == 0 and "Wapas rakha" in r.stdout


# ------------------------------------------------------------------ .bat static lint
BATS = sorted(ROOT.glob("*.bat"))


def test_bat_files_exist():
    assert {b.name for b in BATS} >= {"INSTALL.bat", "START.bat", "DOCTOR.bat", "ADD_USER.bat", "INDEX_CODE.bat", "ASK.bat",
                                      "TEST.bat", "AUTOSTART_ON.bat", "AUTOSTART_OFF.bat", "RESTORE_BACKUP.bat"}


@pytest.mark.parametrize("bat", BATS, ids=lambda p: p.name)
def test_bat_file_is_wellformed(bat):
    raw = bat.read_bytes()
    assert b"\r\n" in raw and not re.search(rb"(?<!\r)\n", raw), "sirf CRLF line endings hone chahiye"
    assert b"\t" not in raw and all(c < 128 for c in raw), "tab/non-ASCII cmd mein masla karte hain"
    lines = raw.decode().split("\r\n")
    assert lines[0].lower() == "@echo off"
    labels = {l[1:].strip().lower() for l in lines if l.startswith(":") and not l.startswith("::")}
    for l in lines:
        for tgt in re.findall(r"\bgoto\s+:?(\w+)", l, re.I):
            assert tgt.lower() in labels | {"eof"}, f"{bat.name}: goto {tgt} ka label nahi"
    # block ke andar kisi echo mein kachcha ')' block jaldi band kar deta hai
    depth = 0
    for n, l in enumerate(lines, 1):
        s = l.strip()
        if depth > 0 and s.lower().startswith("echo"):
            text = re.sub(r"\^\)", "", s)
            assert ")" not in text, f"{bat.name}:{n} block ke andar echo mein ')' (escape karein ^) )"
        if s.startswith(") else") and s.endswith("("):
            pass                                          # ") else (" : band + khula = net zero
        elif s.endswith("(") and re.match(r"(if|for|else)\b", s, re.I):
            depth += 1
        elif s == ")" or s.startswith(") else"):
            depth -= 1 if s == ")" else 0
    assert depth == 0, f"{bat.name}: brackets barabar nahi"
    # %errorlevel% jaise cheezein block ke andar nahi (delayed expansion ke baghair purani value)
    in_block = 0
    for l in lines:
        s = l.strip()
        if in_block and "%errorlevel%" in s.lower():
            raise AssertionError(f"{bat.name}: %errorlevel% block ke andar")
        if s.endswith("("):
            in_block += 1
        elif s == ")":
            in_block -= 1


def test_every_engine_command_used_by_bats_exists():
    cmds = set()
    for b in BATS:
        for m in re.findall(r"-m engine\s+([\w-]+)", b.read_text()):
            cmds.add(m)
    assert {"serve", "doctor", "adduser", "index-code", "ask", "restore"} <= cmds
    for c in cmds:
        with pytest.raises(SystemExit) as e:                                    # --help se pata chalta hai command registered hai
            cli_main([c, "--help"])
        assert e.value.code == 0, c


def test_bat_cross_references_exist():
    names = {b.name for b in BATS}
    for b in BATS:
        for ref in re.findall(r"\b([A-Z_]+\.bat)\b", b.read_text()):
            assert ref in names, f"{b.name} ek na-maujood file ka zikr karta hai: {ref}"


def test_start_bat_only_stops_on_clean_exit_or_port_busy():
    s = (ROOT / "START.bat").read_text()
    assert 'if "%RC%"=="0" goto end' in s and 'if "%RC%"=="2" goto end' in s and "GTR 50" in s
