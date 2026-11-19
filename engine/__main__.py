"""CLI:  python -m engine <command>

serve        server + crawler + brain (24/7)
crawl        sirf crawler (--once, --max-pages N)
doctor       poora system check (dependencies, config, database, port, network)
adduser      naya user + API key
index-code   apne code folders index karo
ask          terminal se sawal poochho
status       index/queue ka haal
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level), format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def preflight_db(cfg: dict) -> str:
    """Server shuru karne se pehle database ki jaanch. Kharab ho to khud sahi backup se wapas (server abhi band hai, surakshit)."""
    from .db import check_file, quarantine_corrupt, restore_latest_backup

    path = cfg["general"]["db_path"]
    if not os.path.exists(path) or os.path.getsize(path) == 0 or check_file(path):
        return "ok"
    print("[!] Database kharab mili. Sahi backup se wapas la raha hoon...")
    src = restore_latest_backup(path, os.path.join(cfg["_data_dir"], "backups"))
    if src:
        print(f"[OK] Backup se wapas aa gaya: {src}")
        return "restored"
    moved = quarantine_corrupt(path)
    print(f"[!] Koi sahi backup nahi mila. Kharab file yahan rakh di: {moved}\n    Naya khali index ban raha hai; crawler dobara data jama karega.")
    return "fresh"


def cmd_autopilot(a) -> int:
    from pathlib import Path

    from .autopilot import run
    from .config import load_config

    cfg = load_config()
    data = cfg["_data_dir"]
    db_path = cfg["general"]["db_path"]
    key = a.key
    keyf = Path(data) / "autopilot" / "key.txt"
    if not key and not a.report and not a.local and keyf.exists():
        key = keyf.read_text().strip()
    if not key and not a.report and not a.local and a.url and os.path.exists(db_path):
        from .db import DB
        from .users import create_user
        key = create_user(DB(db_path), "autopilot-bot", "admin", 600)  # roz ki quota nahi (server isay chhoot deta hai)
        keyf.parent.mkdir(parents=True, exist_ok=True)
        keyf.write_text(key)
    url = a.url or f"http://127.0.0.1:{cfg['server']['port']}"
    return asyncio.run(run(url, data, db_path, key or None, a.max_cases, a.per_min, local=a.local, once_report=a.report))


def cmd_export_train(a) -> int:
    import json as _json
    from pathlib import Path

    from .trainprep import export_passages

    out = Path(a.out)
    n = asyncio.run(export_passages(a.url, a.n, out))
    (out / "dataset-metadata.json").write_text(_json.dumps(
        {"title": "talashly-data", "id": f"{a.user}/talashly-data", "licenses": [{"name": "other"}]}), encoding="utf-8")
    print(f"{n} passages -> {out}")
    return 0 if n >= 50 else 1


def cmd_restore(a) -> int:
    from .config import load_config
    from .db import restore_latest_backup

    cfg = load_config()
    if not _port_free(cfg["server"]["host"], int(cfg["server"]["port"])):
        print("[X] Server chal raha hai. Pehle usay band karein (restore sirf band server par surakshit hai).")
        return 2
    src = restore_latest_backup(cfg["general"]["db_path"], os.path.join(cfg["_data_dir"], "backups"))
    print(f"[OK] Wapas rakha: {src}" if src else "[X] Koi sahi backup nahi mila (data\\backups khali ya sab kharab).")
    return 0 if src else 1


def cmd_serve(a) -> int:
    import uvicorn

    from .config import load_config

    cfg = load_config()
    preflight_db(cfg)
    host = a.host or cfg["server"]["host"]
    env_port = os.environ.get("PORT", "")  # Render/Railway/HF jaise hosts PORT dete hain
    port = a.port or (int(env_port) if env_port.isdigit() and 0 < int(env_port) < 65536 else int(cfg["server"]["port"]))
    if not _port_free(host, port):
        print(f"[X] Port {port} pehle se istemal mein hai. config.toml mein [server] port badlein ya dusra program band karein.")
        return 2
    if a.no_crawl:
        os.environ["SS_CRAWL"] = "0"
    if a.open:
        import threading
        import time as _t
        import webbrowser

        def _open_when_ready() -> None:
            for _ in range(120):  # 30 second tak server ka intezar
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.3)
                    if s.connect_ex(("127.0.0.1", port)) == 0:
                        try:
                            webbrowser.open(f"http://localhost:{port}")
                        except Exception:  # noqa: BLE001
                            pass
                        return
                _t.sleep(0.25)

        threading.Thread(target=_open_when_ready, daemon=True).start()
    print(f"Talashly chal raha hai: http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}")
    proxy = os.environ.get("SS_TRUST_PROXY", "0") == "1"  # sirf tab on karein jab hosting proxy ke peeche hon
    uvicorn.run("engine.server:create_app", factory=True, host=host, port=port, log_level="warning",
                proxy_headers=proxy, forwarded_allow_ips="*" if proxy else None)
    return 0


def cmd_crawl(a) -> int:
    from .crawler import Crawler

    _setup_logging()
    c = Crawler()
    try:
        asyncio.run(c.run(once=a.once, max_pages=a.max_pages))
    except KeyboardInterrupt:
        pass
    return 0


def cmd_adduser(a) -> int:
    from .config import load_config
    from .db import DB
    from .users import create_user

    db = DB(load_config()["general"]["db_path"])
    try:
        key = create_user(db, a.name, a.role, a.rate)
    except Exception as e:  # noqa: BLE001
        print(f"[X] user nahi bana: {e}")
        return 1
    print(f"User '{a.name}' ({a.role}) bana. API key (sirf abhi dikhai ja rahi hai):\n\n  {key}\n")
    return 0


def cmd_index_code(a) -> int:
    from .codeindex import index_paths
    from .config import load_config
    from .db import DB

    cfg = load_config()
    db = DB(cfg["general"]["db_path"])
    paths = a.paths or cfg["code"]["paths"]
    if not paths:
        print("[X] Koi path nahi diya. Misal: python -m engine index-code C:\\projects\\myapp")
        return 1
    print(index_paths(db, paths, cfg["code"]["max_file_kb"]))
    return 0


def cmd_ask(a) -> int:
    from .brain import Brain
    from .config import load_config, load_sources
    from .db import DB
    from .search import Searcher

    cfg = load_config()
    db = DB(cfg["general"]["db_path"])
    brain = Brain(cfg, db, Searcher(db, cfg), load_sources())

    async def go() -> None:
        ans = ""
        async for ev, data in brain.run(" ".join(a.question), "user", deep=a.deep, stream_delay=0):
            if ev == "plan":
                print("PLAN:", data)
            elif ev == "step":
                print(" ..", data["msg"])
            elif ev == "sources":
                for s in data:
                    print(f"  [{s['n']}] {s['title']}  ({s['domain']})\n      {s['url']}")
            elif ev == "code":
                for c in data:
                    print(f"  {c['path']}:{c['start']}-{c['end']} ({c['lang']})")
            elif ev == "token":
                ans += data
            elif ev == "replace":
                ans = data
            elif ev == "done":
                print("\nJAWAB:", ans.strip(), "\n", data)

    asyncio.run(go())
    return 0


def cmd_status(a) -> int:
    from .config import load_config
    from .db import DB

    db = DB(load_config()["general"]["db_path"])
    for k, v in db.counts().items():
        print(f"{k:12} {v}")
    print(f"{'db_mb':12} {db.size_mb():.1f}")
    return 0


def cmd_export_feedback(a) -> int:
    """Users ke 👍 se bane achhe (sawal, jawab) JSONL mein: baad mein fine-tuning ke liye (GPU chahiye, yahan nahi hoti)."""
    import json

    from .config import load_config
    from .db import DB

    db = DB(load_config()["general"]["db_path"])
    rows = db.q("""SELECT l.q, l.a, SUM(v.vote=1) ups, SUM(v.vote=-1) downs FROM learned l
                   JOIN learned_votes v ON v.qkey=l.qkey GROUP BY l.qkey HAVING ups>=? ORDER BY ups DESC""", (a.min_up,))
    out = open(a.out, "w", encoding="utf-8") if a.out else sys.stdout
    for r in rows:
        out.write(json.dumps({"messages": [{"role": "user", "content": r["q"]}, {"role": "assistant", "content": r["a"]}],
                              "ups": int(r["ups"]), "downs": int(r["downs"] or 0)}, ensure_ascii=False) + "\n")
    if a.out:
        out.close()
        print(f"{len(rows)} examples -> {a.out}")
    return 0


def cmd_doctor(a) -> int:
    """Har cheez check karo aur saaf bataao kya theek hai, kya nahi, aur kaise theek hoga."""
    ok = True

    def line(good: bool, msg: str, fix: str = "") -> None:
        nonlocal ok
        print(("[OK] " if good else "[X]  ") + msg + ("" if good or not fix else f"\n      -> {fix}"))
        ok = ok and good

    print("== Talashly doctor ==")
    v = sys.version_info
    line(v >= (3, 11), f"Python {v.major}.{v.minor}.{v.micro}", "Python 3.11 ya naya install karein (python.org, 'Add to PATH' tick karein)")
    for mod in ["httpx", "trafilatura", "lxml", "feedparser", "fastapi", "uvicorn", "pydantic"]:
        try:
            __import__(mod)
            line(True, f"package {mod}")
        except Exception:  # noqa: BLE001
            line(False, f"package {mod} nahi mila", "INSTALL.bat chalayein")
    import sqlite3

    line(True, f"SQLite {sqlite3.sqlite_version}")
    try:
        c = sqlite3.connect(":memory:")
        c.execute("create virtual table t using fts5(a)")
        line(True, "SQLite FTS5 (full-text search)")
    except Exception:  # noqa: BLE001
        line(False, "SQLite mein FTS5 nahi", "Python ka naya version install karein")
    try:
        c.execute("create virtual table t2 using fts5(a, tokenize='trigram')")
        line(True, "SQLite trigram (Chinese/Japanese/Korean search)")
    except Exception:  # noqa: BLE001
        print("[~]  trigram nahi: CJK search chalegi lekin dheemi (LIKE). Python/SQLite update karein to behtar.")
    from .config import load_config

    cfg = load_config()
    for p in cfg["_problems"]:
        print(f"[~]  config theek kiya gaya: {p}")
    if not cfg["_problems"]:
        line(True, "config.toml theek hai")
    from .db import DB

    try:
        db = DB(cfg["general"]["db_path"])
        integ = db.integrity()
        line(integ["ok"], f"database {cfg['general']['db_path']}  ({db.size_mb():.1f} MB, {integ['chunks']} chunks)",
             "FTS ka masla server khud theek karta hai; SQLite file kharab ho to server band kar ke RESTORE_BACKUP.bat chalayein")
    except Exception as e:  # noqa: BLE001
        line(False, f"database nahi khul raha: {e}", "data\\index.db ka backup data\\backups se wapas copy karein")
    host, port = cfg["server"]["host"], int(cfg["server"]["port"])
    free = _port_free(host, port)
    print(("[OK] " if free else "[~]  ") + f"port {port} " + ("khali hai" if free else
          "pehle se istemal mein (shayad Talashly pehle se chal raha hai)"))
    try:
        import httpx

        r = httpx.get("https://en.wikipedia.org/robots.txt", timeout=8, follow_redirects=True)
        line(r.status_code == 200, f"internet (wikipedia robots.txt -> {r.status_code})", "internet/proxy/firewall check karein")
    except Exception as e:  # noqa: BLE001
        line(False, f"internet nahi chal raha: {type(e).__name__}", "internet/proxy/firewall check karein; crawler ko internet chahiye")
    print("\n" + ("Sab theek hai." if ok else "Kuch cheezein theek karni hain (upar [X] dekhein)."))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows console par Urdu/Chinese print crash na kare
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(prog="python -m engine", description="Talashly")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="server + crawler + brain")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--no-crawl", action="store_true")
    s.add_argument("--open", action="store_true", help="server tayyar hote hi browser kholo")
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("crawl", help="sirf crawler")
    s.add_argument("--once", action="store_true")
    s.add_argument("--max-pages", type=int)
    s.set_defaults(fn=cmd_crawl)
    s = sub.add_parser("adduser", help="naya user + API key")
    s.add_argument("name")
    s.add_argument("--role", default="user", choices=["user", "admin"])
    s.add_argument("--rate", type=int, default=60)
    s.set_defaults(fn=cmd_adduser)
    s = sub.add_parser("index-code", help="code folders index karo")
    s.add_argument("paths", nargs="*")
    s.set_defaults(fn=cmd_index_code)
    s = sub.add_parser("ask", help="terminal se sawal")
    s.add_argument("question", nargs="+")
    s.add_argument("--deep", action="store_true")
    s.set_defaults(fn=cmd_ask)
    s = sub.add_parser("restore", help="sab se naye sahi backup se database wapas (server band ho)")
    s.set_defaults(fn=cmd_restore)
    s = sub.add_parser("status")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("export-feedback", help="achhe (sawal, jawab) JSONL mein export (fine-tuning ke liye)")
    s.add_argument("--out", default="")
    s.add_argument("--min-up", type=int, default=3)
    s.set_defaults(fn=cmd_export_feedback)
    s = sub.add_parser("autopilot", help="khud sawal banao, test karo, yaad rakho, report likho (hamesha)")
    s.add_argument("--url", default="")
    s.add_argument("--key", default=os.environ.get("AP_KEY", ""))
    s.add_argument("--max-cases", type=int, default=0, help="0 = hamesha")
    s.add_argument("--per-min", type=float, default=float(os.environ.get("AP_PER_MIN", "12")))
    s.add_argument("--local", action="store_true", help="is machine ke app ko seedha (bina network) test karo")
    s.add_argument("--report", action="store_true")
    s.set_defaults(fn=cmd_autopilot)
    s = sub.add_parser("export-train", help="Kaggle training ke liye passages export (live site ke public search se)")
    s.add_argument("--url", required=True)
    s.add_argument("--n", type=int, default=2500)
    s.add_argument("--out", default="kaggle/dataset")
    s.add_argument("--user", default="musfiraai", help="Kaggle username (dataset id ke liye)")
    s.set_defaults(fn=cmd_export_train)
    s = sub.add_parser("doctor", help="system check")
    s.set_defaults(fn=cmd_doctor)
    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):
        ap.print_help()
        return 0
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
