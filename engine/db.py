from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS frontier (
    url          TEXT PRIMARY KEY,
    domain       TEXT NOT NULL,
    depth        INTEGER NOT NULL DEFAULT 0,
    kind         TEXT NOT NULL DEFAULT 'page',   -- page | feed | sitemap
    topic        TEXT,
    status       TEXT NOT NULL DEFAULT 'queued', -- queued | done | error | blocked
    next_fetch   REAL NOT NULL DEFAULT 0,
    interval_s   REAL NOT NULL DEFAULT 86400,
    last_fetch   REAL,
    etag         TEXT,
    last_modified TEXT,
    content_hash TEXT,
    fail_count   INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT
);
CREATE INDEX IF NOT EXISTS idx_frontier_due ON frontier(status, next_fetch);
CREATE INDEX IF NOT EXISTS idx_frontier_domain ON frontier(domain);

CREATE TABLE IF NOT EXISTS docs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    url          TEXT UNIQUE NOT NULL,
    domain       TEXT NOT NULL,
    title        TEXT,
    author       TEXT,
    lang         TEXT,
    topic        TEXT,
    published_at REAL,
    fetched_at   REAL NOT NULL,
    content_hash TEXT NOT NULL,
    word_count   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_docs_domain ON docs(domain);

CREATE TABLE IF NOT EXISTS chunks (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id  INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    ord     INTEGER NOT NULL,
    text    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, title, tokenize='porter unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS domain_state (
    domain       TEXT PRIMARY KEY,
    next_allowed REAL NOT NULL DEFAULT 0,
    robots_txt   TEXT,
    robots_at    REAL,
    pages        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stats (
    k TEXT PRIMARY KEY,
    v REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS query_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    q TEXT, at REAL, results INTEGER, ms REAL
);
"""

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS allowed_domains(domain TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS candidates(
    domain TEXT PRIMARY KEY, votes INTEGER NOT NULL DEFAULT 0, sample TEXT, promoted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS gaps(
    q TEXT PRIMARY KEY, n INTEGER NOT NULL DEFAULT 1, last_at REAL,
    resolved INTEGER NOT NULL DEFAULT 0, tries INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS learned(
    qkey TEXT PRIMARY KEY, q TEXT NOT NULL, a TEXT NOT NULL, ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS learned_votes(
    qkey TEXT NOT NULL, voter TEXT NOT NULL, vote INTEGER NOT NULL, PRIMARY KEY(qkey, voter)
);
CREATE TABLE IF NOT EXISTS brain_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, kind TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, key_hash TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL DEFAULT 'user', rate_per_min INTEGER NOT NULL DEFAULT 60, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, kind TEXT NOT NULL, payload TEXT,
    status TEXT NOT NULL DEFAULT 'queued', result TEXT, created REAL, finished REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, id);
CREATE TABLE IF NOT EXISTS code_files(path TEXT PRIMARY KEY, hash TEXT, lang TEXT, mtime REAL);
CREATE TABLE IF NOT EXISTS code_chunks(
    id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, lang TEXT, start_line INTEGER, end_line INTEGER,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_code_path ON code_chunks(path);
CREATE TABLE IF NOT EXISTS conversations(
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', model TEXT NOT NULL,
    created REAL NOT NULL, updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_owner ON conversations(owner, updated);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL, content TEXT NOT NULL, meta TEXT, created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id, id);
CREATE TABLE IF NOT EXISTS usage(
    owner TEXT NOT NULL, day TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner, day)
);
CREATE VIRTUAL TABLE IF NOT EXISTS code_fts USING fts5(text, symbols, tokenize="unicode61 tokenchars '_'");
"""

_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")


def cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    return len(_CJK.findall(text)) / max(1, len(text))


_TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|ref_|igshid|yclid|_ga)", re.I)


def normalize_url(url: str) -> str:
    """Duplicate URLs ko ek bana do: fragment hatao, tracking params hatao, host lowercase."""
    try:
        s = urlsplit(url.strip())
    except ValueError:
        return url
    if s.scheme not in ("http", "https"):
        return url
    host = (s.hostname or "").lower()
    if s.port and not ((s.scheme == "http" and s.port == 80) or (s.scheme == "https" and s.port == 443)):
        host = f"{host}:{s.port}"
    q = sorted((k, v) for k, v in parse_qsl(s.query, keep_blank_values=True) if not _TRACKING.match(k))
    path = s.path or "/"
    return urlunsplit((s.scheme, host, path, urlencode(q), ""))


def domain_of(url: str) -> str:
    h = (urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()


BIG_DB_BYTES = 300 * 1024 * 1024  # is se bari file par start par poora scan nahi (minutes lagte hain); roz ka full check pakar lega


def check_file(path: str) -> bool:
    """True agar SQLite file khul jati hai aur quick_check 'ok' deti hai (file maujood na ho to False; nayi file nahi banati)."""
    import os

    if not os.path.isfile(path):
        return False
    try:
        c = sqlite3.connect(path, timeout=10)
        try:
            if os.path.getsize(path) > BIG_DB_BYTES:  # sasta check: file khulti hai, schema aur FTS jawab dete hain
                c.execute("SELECT COUNT(*) FROM docs").fetchone()
                c.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'a' LIMIT 1").fetchall()
                return c.execute("PRAGMA schema_version").fetchone() is not None
            return c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            c.close()
    except sqlite3.DatabaseError:
        return False


def restore_latest_backup(db_path: str, backup_dir: str) -> str | None:
    """Sab se naya SAHI backup db_path par wapas rakho. Kharab file mehfooz jagah (index.db.corrupt-<time>) chali jati hai.
    Sirf tab chalao jab server band ho (koi open connection na ho). Backup path ya None wapas karta hai."""
    import os
    import shutil

    if not os.path.isdir(backup_dir):
        return None
    cands = sorted((f for f in os.listdir(backup_dir) if f.startswith("index-") and f.endswith(".db")), reverse=True)
    for f in cands:
        src = os.path.join(backup_dir, f)
        if not check_file(src):
            continue
        quarantine_corrupt(db_path)
        shutil.copy2(src, db_path)
        return src
    return None


def quarantine_corrupt(db_path: str) -> str | None:
    import os

    dest = None
    if os.path.exists(db_path):
        dest = f"{db_path}.corrupt-{int(time.time())}"
        os.replace(db_path, dest)
    for ext in ("-wal", "-shm"):
        try:
            os.remove(db_path + ext)
        except OSError:
            pass
    return dest


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute("PRAGMA cache_size=-16000")  # ~16 MB (chhoti RAM wali VM ke liye)
        self.conn.execute("PRAGMA temp_store=FILE")
        self.conn.executescript(SCHEMA_V2)
        try:  # trigram: Chinese/Japanese/Korean ke liye (purane SQLite mein na ho to LIKE fallback)
            self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_tri USING fts5(text, tokenize='trigram')")
            self.has_trigram = True
        except sqlite3.OperationalError:
            self.has_trigram = False
        try:
            self.conn.execute("ALTER TABLE domain_state ADD COLUMN paused_until REAL NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column pehle se hai

    # ---------- generic ----------
    def q(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(args)).fetchall()

    def x(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, tuple(args))

    def bump(self, key: str, n: float = 1) -> None:
        self.x(
            "INSERT INTO stats(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=v+excluded.v",
            (key, n),
        )

    # ---------- frontier ----------
    def add_url(self, url: str, depth: int = 0, kind: str = "page", topic: str | None = None,
                interval_s: float = 86400, due_now: bool = True) -> bool:
        url = normalize_url(url)
        if not url.startswith("http"):
            return False
        cur = self.x(
            "INSERT OR IGNORE INTO frontier(url,domain,depth,kind,topic,next_fetch,interval_s) "
            "VALUES(?,?,?,?,?,?,?)",
            (url, domain_of(url), depth, kind, topic, 0 if due_now else time.time() + interval_s, interval_s),
        )
        return cur.rowcount > 0

    def due(self, limit: int) -> list[sqlite3.Row]:
        now = time.time()
        return self.q(
            "SELECT * FROM frontier WHERE status IN ('queued','done','error') AND next_fetch<=? "
            "AND fail_count<5 AND domain NOT IN (SELECT domain FROM domain_state WHERE paused_until>?) "
            "ORDER BY (kind='feed') DESC, depth ASC, next_fetch ASC LIMIT ?",
            (now, now, limit),
        )

    # ---------- documents ----------
    def upsert_doc(self, url: str, title: str, author: str | None, lang: str | None, topic: str | None,
                   published_at: float | None, content_hash: str, chunks: list[str]) -> tuple[int, bool]:
        """(doc_id, changed). Content nahi badla to kuch rewrite nahi hota."""
        url = normalize_url(url)
        row = self.q("SELECT id, content_hash FROM docs WHERE url=?", (url,))
        now = time.time()
        wc = int(sum(len(_CJK.sub(" ", c).split()) + len(_CJK.findall(c)) * 0.6 for c in chunks))
        if row and row[0]["content_hash"] == content_hash:
            self.x("UPDATE docs SET fetched_at=? WHERE id=?", (now, row[0]["id"]))
            return row[0]["id"], False
        self.x("SAVEPOINT upsert_doc")  # SAVEPOINT: bahar ki transaction ke andar bhi surakshit
        try:
            if row:
                doc_id = row[0]["id"]
                self._delete_chunks(doc_id)
                self.x(
                    "UPDATE docs SET title=?,author=?,lang=?,topic=?,published_at=?,fetched_at=?,"
                    "content_hash=?,word_count=? WHERE id=?",
                    (title, author, lang, topic, published_at, now, content_hash, wc, doc_id),
                )
            else:
                cur = self.x(
                    "INSERT INTO docs(url,domain,title,author,lang,topic,published_at,fetched_at,content_hash,word_count)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (url, domain_of(url), title, author, lang, topic, published_at, now, content_hash, wc),
                )
                doc_id = cur.lastrowid
            for i, text in enumerate(chunks):
                c = self.x("INSERT INTO chunks(doc_id,ord,text) VALUES(?,?,?)", (doc_id, i, text))
                self.x("INSERT INTO chunks_fts(rowid,text,title) VALUES(?,?,?)", (c.lastrowid, text, title or ""))
                if self.has_trigram and cjk_ratio(text) > 0.15:
                    self.x("INSERT INTO chunks_tri(rowid,text) VALUES(?,?)", (c.lastrowid, text))
            self.x("RELEASE upsert_doc")
        except Exception:
            self.x("ROLLBACK TO upsert_doc")
            self.x("RELEASE upsert_doc")
            raise
        return doc_id, True

    def _delete_chunks(self, doc_id: int) -> None:
        ids = [r["id"] for r in self.q("SELECT id FROM chunks WHERE doc_id=?", (doc_id,))]
        for cid in ids:
            self.x("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
            if self.has_trigram:
                self.x("DELETE FROM chunks_tri WHERE rowid=?", (cid,))
        self.x("DELETE FROM chunks WHERE doc_id=?", (doc_id,))

    # ---------- brain / self-healing helpers ----------
    def log(self, kind: str, detail: str) -> None:
        self.x("INSERT INTO brain_log(at,kind,detail) VALUES(?,?,?)", (time.time(), kind, detail[:500]))
        self.x("DELETE FROM brain_log WHERE id < (SELECT MAX(id) FROM brain_log) - 2000")

    def set_stat(self, key: str, value: float) -> None:
        self.x("INSERT INTO stats(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value))

    def get_stat(self, key: str, default: float = 0.0) -> float:
        r = self.q("SELECT v FROM stats WHERE k=?", (key,))
        return r[0]["v"] if r else default

    def vote_candidate(self, domain: str, sample: str) -> None:
        self.x(
            "INSERT INTO candidates(domain,votes,sample) VALUES(?,1,?) "
            "ON CONFLICT(domain) DO UPDATE SET votes=votes+1", (domain, sample[:300]))

    def pause_domain(self, domain: str, seconds: float) -> None:
        self.x(
            "INSERT INTO domain_state(domain,paused_until) VALUES(?,?) "
            "ON CONFLICT(domain) DO UPDATE SET paused_until=excluded.paused_until",
            (domain, time.time() + seconds))

    def integrity_light(self) -> bool:
        """Sasta check (milliseconds): DB khulti hai aur FTS jawab deta hai. Poora scan roz ek dafa (integrity())."""
        try:
            c = sqlite3.connect(self.path, timeout=10)
            try:
                c.execute("SELECT COUNT(*) FROM docs").fetchone()
                c.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'a' LIMIT 1").fetchall()
                return True
            finally:
                c.close()
        except sqlite3.DatabaseError:
            return False

    def integrity(self) -> dict[str, Any]:
        """Index ki sehat. Hamesha NAYE chhote connection par chalta hai (lambe-chalte connection ka purana FTS cache
        jhoota alarm de sakta hai) aur kharab file par exception nahi balkay saaf report deta hai."""
        out: dict[str, Any] = {"sqlite": "?", "chunks": -1, "fts_rows": -1, "fts_integrity": "?", "orphans": -1, "ok": False}
        try:
            c = sqlite3.connect(self.path, timeout=10)
        except sqlite3.DatabaseError as e:
            out["sqlite"] = f"error: {e}"
            return out
        try:
            out["sqlite"] = c.execute("PRAGMA quick_check").fetchone()[0]
            out["chunks"] = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            out["fts_rows"] = c.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
            out["orphans"] = c.execute("SELECT COUNT(*) FROM chunks WHERE doc_id NOT IN (SELECT id FROM docs)").fetchone()[0]
            try:
                c.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('integrity-check')")
                out["fts_integrity"] = "ok"
            except sqlite3.OperationalError as e:
                out["fts_integrity"] = "busy" if "locked" in str(e) else f"bad: {e}"
            except sqlite3.DatabaseError as e:
                out["fts_integrity"] = f"bad: {e}"
            out["ok"] = (out["sqlite"] == "ok" and out["chunks"] == out["fts_rows"] and out["orphans"] == 0
                         and out["fts_integrity"] in ("ok", "busy"))
        except sqlite3.DatabaseError as e:
            out["sqlite"] = out["sqlite"] if out["sqlite"] != "?" else f"error: {e}"
            out["ok"] = False
        finally:
            c.close()
        return out

    def rebuild_fts(self) -> int:
        """Search index kharab/adhoora ho to chunks table se dobara bana lo (data zaya nahi hota)."""
        self.x("BEGIN")
        try:
            self.x("DELETE FROM chunks WHERE doc_id NOT IN (SELECT id FROM docs)")
            self.x("DELETE FROM chunks_fts")
            if self.has_trigram:
                self.x("DELETE FROM chunks_tri")
            rows = self.q("SELECT c.id, c.text, d.title FROM chunks c JOIN docs d ON d.id=c.doc_id")
            for r in rows:
                self.x("INSERT INTO chunks_fts(rowid,text,title) VALUES(?,?,?)", (r["id"], r["text"], r["title"] or ""))
                if self.has_trigram and cjk_ratio(r["text"]) > 0.15:
                    self.x("INSERT INTO chunks_tri(rowid,text) VALUES(?,?)", (r["id"], r["text"]))
            self.x("COMMIT")
        except Exception:
            self.x("ROLLBACK")
            raise
        return len(rows)

    def backup(self, dest_dir: str, keep: int = 7) -> str:
        import os
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, time.strftime("index-%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}.db")
        target = sqlite3.connect(dest)
        with target:
            self.conn.backup(target)
        target.close()
        files = sorted(f for f in os.listdir(dest_dir) if f.startswith("index-") and f.endswith(".db"))
        for old in files[:-keep]:
            os.remove(os.path.join(dest_dir, old))
        return dest

    def size_mb(self) -> float:
        import os
        try:
            return (os.path.getsize(self.path) + os.path.getsize(self.path + "-wal")) / 1e6
        except OSError:
            return os.path.getsize(self.path) / 1e6 if os.path.exists(self.path) else 0.0

    def prune_oldest(self, target_mb: float) -> int:
        """Disk limit paar ho jaye to sab se purane docs hata do (naya data mehfooz). Sirf utna hatata hai jitna zaroori
        (anuman se), phir ek dafa compact; zarurat ho to chand rounds."""
        removed = 0
        for _ in range(6):
            size = self.size_mb()
            if size <= target_mb:
                break
            n = self.q("SELECT COUNT(*) FROM docs")[0][0]
            if n == 0:
                break
            frac = min(1.0, (size - target_mb) / size * 1.15 + 0.02)
            ids = [r["id"] for r in self.q("SELECT id FROM docs ORDER BY fetched_at ASC LIMIT ?", (max(1, int(n * frac)),))]
            self.x("BEGIN")
            try:
                for i in ids:
                    self._delete_chunks(i)
                    self.x("DELETE FROM docs WHERE id=?", (i,))
                self.x("COMMIT")
            except Exception:
                self.x("ROLLBACK")
                raise
            removed += len(ids)
            try:
                self.x("PRAGMA wal_checkpoint(TRUNCATE)")
                self.x("VACUUM")
            except sqlite3.OperationalError:
                pass  # kisi aur connection ne lock rakha hai: agli maintenance mein compact ho jayega
        return removed

    def counts(self) -> dict[str, Any]:
        one = lambda s: self.q(s)[0][0] or 0  # noqa: E731
        return {
            "docs": one("SELECT COUNT(*) FROM docs"),
            "chunks": one("SELECT COUNT(*) FROM chunks"),
            "domains": one("SELECT COUNT(DISTINCT domain) FROM docs"),
            "queued": one("SELECT COUNT(*) FROM frontier WHERE status='queued'"),
            "errors": one("SELECT COUNT(*) FROM frontier WHERE status='error'"),
            "blocked": one("SELECT COUNT(*) FROM frontier WHERE status='blocked'"),
            "words": one("SELECT SUM(word_count) FROM docs"),
            "code_chunks": one("SELECT COUNT(*) FROM code_chunks"),
            "code_files": one("SELECT COUNT(*) FROM code_files"),
            "open_gaps": one("SELECT COUNT(*) FROM gaps WHERE resolved=0"),
            "users": one("SELECT COUNT(*) FROM users"),
        }
