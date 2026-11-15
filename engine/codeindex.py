"""Apne code folders ko index karo (local repos, projects). Phir 'code' search se function/class/error dhoondo."""
from __future__ import annotations

import os
from pathlib import Path

from .db import DB, sha
from .search import split_identifiers

LANG = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".jsx": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".java": "java", ".kt": "kotlin", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
    ".cc": "cpp", ".cs": "csharp", ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".sql": "sql", ".sh": "shell", ".bat": "batch", ".ps1": "powershell", ".html": "html", ".css": "css",
    ".scss": "css", ".md": "markdown", ".yml": "yaml", ".yaml": "yaml", ".toml": "toml", ".lua": "lua",
    ".dart": "dart", ".r": "r", ".scala": "scala", ".vue": "vue", ".svelte": "svelte", ".dockerfile": "docker",
}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".idea", ".next",
             "target", ".pytest_cache", ".mypy_cache", "site-packages", ".gradle", "vendor"}
WINDOW, OVERLAP = 50, 10


def _chunks(lines: list[str]) -> list[tuple[int, int, str]]:
    out, i = [], 0
    while i < len(lines):
        j = min(len(lines), i + WINDOW)
        text = "\n".join(lines[i:j])
        if text.strip():
            out.append((i + 1, j, text))
        if j >= len(lines):
            break
        i = j - OVERLAP
    return out


def _delete_file(db: DB, path: str) -> None:
    for r in db.q("SELECT id FROM code_chunks WHERE path=?", (path,)):
        db.x("DELETE FROM code_fts WHERE rowid=?", (r["id"],))
    db.x("DELETE FROM code_chunks WHERE path=?", (path,))
    db.x("DELETE FROM code_files WHERE path=?", (path,))


def index_paths(db: DB, paths: list[str], max_file_kb: int = 500) -> dict[str, int]:
    """Incremental: sirf badli hui files dobara index hoti hain; delete hui files index se nikal jati hain."""
    stats = {"indexed": 0, "unchanged": 0, "removed": 0, "skipped": 0}
    seen: set[str] = set()
    roots = []
    for root in paths:
        rp = Path(root).expanduser()
        if not rp.exists():
            continue
        roots.append(str(rp.resolve()))
        for dirpath, dirs, files in os.walk(rp):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".git")]
            for fn in files:
                p = Path(dirpath) / fn
                ext = p.suffix.lower() or ("." + fn.lower() if fn.lower() == "dockerfile" else "")
                lang = LANG.get(ext)
                if not lang:
                    continue
                try:
                    if p.stat().st_size > max_file_kb * 1024:
                        stats["skipped"] += 1
                        continue
                    raw = p.read_bytes()
                except OSError:
                    stats["skipped"] += 1
                    continue
                if b"\x00" in raw[:2048]:  # binary
                    stats["skipped"] += 1
                    continue
                path = str(p.resolve())
                seen.add(path)
                h = sha(raw.decode("utf-8", "ignore"))
                row = db.q("SELECT hash FROM code_files WHERE path=?", (path,))
                if row and row[0]["hash"] == h:
                    stats["unchanged"] += 1
                    continue
                text = raw.decode("utf-8", "ignore")
                db.x("BEGIN")
                try:
                    _delete_file(db, path)
                    for start, end, chunk in _chunks(text.splitlines()):
                        c = db.x("INSERT INTO code_chunks(path,lang,start_line,end_line,text) VALUES(?,?,?,?,?)",
                                 (path, lang, start, end, chunk))
                        db.x("INSERT INTO code_fts(rowid,text,symbols) VALUES(?,?,?)",
                             (c.lastrowid, chunk, split_identifiers(chunk)))
                    db.x("INSERT INTO code_files(path,hash,lang,mtime) VALUES(?,?,?,?)",
                         (path, h, lang, p.stat().st_mtime))
                    db.x("COMMIT")
                except Exception:
                    db.x("ROLLBACK")
                    raise
                stats["indexed"] += 1
    # in roots ke andar jo files ab maujood nahi, unhe hata do
    for r in db.q("SELECT path FROM code_files"):
        if r["path"] not in seen and any(r["path"].startswith(root) for root in roots):
            _delete_file(db, r["path"])
            stats["removed"] += 1
    return stats
