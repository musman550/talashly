from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field

from .db import DB, cjk_ratio

STOP = set(
    "a an the and or of to in on for with is are was were be been being it its this that these those as at by from "
    "what who whom which when where why how do does did can could should would will about into over than then so "
    "i you he she we they me my your our their latest news today tell give show explain please "
    "kya hai hain ka ki ke ko se mein par aur kaise kyun kon "
    "کیا ہے ہیں کا کی کے کو سے میں پر اور کیسے کیوں "
    "और का की के को से में पर है हैं क्या "
    "и в во не на что это как для по из "
    "de la le les des du un une et en est pour que qui "
    "der die das und ist nicht ein eine mit für von zu "
    "el los las una y es por con para que".split()
)
WORD = re.compile(r"[\w'\-]+", re.UNICODE)
CJK_RUN = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]+")
# Latin/Urdu/Hindi punctuation ke baad space zaroori (warna robots.txt, 3.14, file.py toot jate hain);
# CJK full-width punctuation ke baad space hoti hi nahi.
SENT_SPLIT = re.compile(r"(?<=[.!?۔।])\s+|(?<=[。！？])\s*|\n+")

SCRIPTS = {
    "urdu/arabic": re.compile(r"[؀-ۿݐ-ݿ]"),
    "devanagari": re.compile(r"[ऀ-ॿ]"),
    "cyrillic": re.compile(r"[Ѐ-ӿ]"),
    "cjk": re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]"),
    "greek": re.compile(r"[Ͱ-Ͽ]"),
    "hebrew": re.compile(r"[֐-׿]"),
    "thai": re.compile(r"[฀-๿]"),
    "latin": re.compile(r"[A-Za-zÀ-ɏ]"),
}


def detect_script(text: str) -> str:
    best, n = "latin", 0
    for name, rx in SCRIPTS.items():
        c = len(rx.findall(text))
        if c > n:
            best, n = name, c
    return best


def terms(q: str) -> list[str]:
    q = CJK_RUN.sub(lambda m: f" {m.group(0)} ", q)  # "使用Python开发" -> "使用", "Python", "开发"
    ws = [w.lower().strip("'-") for w in WORD.findall(q)]
    keep = [w for w in ws if w and w not in STOP and (len(w) > 1 or CJK_RUN.fullmatch(w))]
    return keep or [w for w in ws if w]


def _fts_quote(t: str) -> str:
    return '"' + t.replace('"', "") + '"'


@dataclass
class Hit:
    doc_id: int
    url: str
    title: str
    domain: str
    published_at: float | None
    fetched_at: float
    score: float
    passages: list[str] = field(default_factory=list)


@dataclass
class CodeHit:
    path: str
    lang: str
    start: int
    end: int
    text: str
    score: float


_SELECT = """
    SELECT c.id AS cid, c.doc_id, c.text, d.url, d.title, d.domain, d.published_at, d.fetched_at,
           {rank} AS rank
    FROM {tbl} JOIN chunks c ON c.id = {tbl}.rowid JOIN docs d ON d.id = c.doc_id
"""


class Searcher:
    def __init__(self, db: DB, cfg: dict):
        self.db = db
        self.half_life = cfg["search"]["recency_half_life_days"]

    # ---------- low level ----------
    def _run(self, match: str, limit: int):
        sql = _SELECT.format(rank="bm25(chunks_fts, 1.0, 4.0)", tbl="chunks_fts") + \
            " WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?"
        return self.db.q(sql, (match, limit))

    def _run_cjk(self, query: str, limit: int):
        rows, seen = [], set()
        for run in CJK_RUN.findall(query):
            if len(run) >= 3 and self.db.has_trigram:
                sql = _SELECT.format(rank="bm25(chunks_tri)", tbl="chunks_tri") + \
                    " WHERE chunks_tri MATCH ? ORDER BY rank LIMIT ?"
                got = self.db.q(sql, (_fts_quote(run), limit))
            else:  # 1-2 character words (Chinese mein aam): trigram nahi, seedha substring
                sql = ("SELECT c.id AS cid, c.doc_id, c.text, d.url, d.title, d.domain, d.published_at, d.fetched_at, "
                       "-1.0 AS rank FROM chunks c JOIN docs d ON d.id=c.doc_id WHERE c.text LIKE ? LIMIT ?")
                got = self.db.q(sql, (f"%{run}%", limit))
            for r in got:
                if r["cid"] not in seen:
                    seen.add(r["cid"])
                    rows.append(r)
        return rows

    # ---------- web index ----------
    def search(self, query: str, limit: int = 8, domain: str | None = None, log: bool = True) -> list[Hit]:
        t0 = time.time()
        ts = terms(query)
        if not ts:
            return []
        rows: list = []
        if cjk_ratio(query) > 0:
            rows = list(self._run_cjk(query, 120))
        latin = [t for t in ts if not CJK_RUN.fullmatch(t)]
        if latin:
            seen = {r["cid"] for r in rows}
            got = self._run(" AND ".join(_fts_quote(t) for t in latin), 120)
            if len(got) < limit * 2 and len(latin) > 1:
                have = {r["cid"] for r in got}
                got = list(got) + [r for r in self._run(" OR ".join(_fts_quote(t) for t in latin), 120)
                                   if r["cid"] not in have]
            rows += [r for r in got if r["cid"] not in seen]

        now = time.time()
        by_doc: dict[int, Hit] = {}
        for r in rows:
            if domain and r["domain"] != domain:
                continue
            base = -r["rank"]
            ts_ref = r["published_at"] or r["fetched_at"]
            age_days = max(0.0, (now - ts_ref) / 86400)
            recency = math.exp(-age_days * math.log(2) / self.half_life)
            score = base * (0.75 + 0.25 * recency)
            h = by_doc.get(r["doc_id"])
            if h is None:
                by_doc[r["doc_id"]] = Hit(r["doc_id"], r["url"], r["title"] or r["url"], r["domain"],
                                          r["published_at"], r["fetched_at"], score, [r["text"]])
            else:
                h.score = max(h.score, score) + 0.15 * score
                if len(h.passages) < 3:
                    h.passages.append(r["text"])
        hits = sorted(by_doc.values(), key=lambda h: h.score, reverse=True)

        out, per = [], {}
        for h in hits:
            if per.get(h.domain, 0) >= 3:
                continue
            per[h.domain] = per.get(h.domain, 0) + 1
            out.append(h)
            if len(out) >= limit:
                break
        if log:
            self.db.x("INSERT INTO query_log(q,at,results,ms) VALUES(?,?,?,?)",
                      (query, now, len(out), (time.time() - t0) * 1000))
        return out

    def search_many(self, queries: list[str], limit: int = 8) -> list[Hit]:
        """Kai sub-queries ek saath: jo doc kai sub-queries mein aaye wo upar."""
        merged: dict[int, Hit] = {}
        for qi, q in enumerate(queries):
            for h in self.search(q, limit * 2, log=(qi == 0)):
                m = merged.get(h.doc_id)
                if m is None:
                    merged[h.doc_id] = h
                else:
                    m.score += h.score
                    for p in h.passages:
                        if p not in m.passages and len(m.passages) < 4:
                            m.passages.append(p)
        return sorted(merged.values(), key=lambda h: h.score, reverse=True)[:limit]

    # ---------- code index ----------
    def search_code(self, query: str, lang: str | None = None, limit: int = 10) -> list[CodeHit]:
        ts = [t.replace('"', "") for t in re.findall(r"[^\W\d]\w*|\d+", query) if t.lower() not in STOP and len(t) > 1]
        if not ts:
            return []
        def alt(t: str) -> str:
            sp = split_identifiers(t)
            return f'("{t}" OR "{sp}")' if sp and sp != t.lower() else f'"{t}"'

        def run(joiner: str):
            match = joiner.join(alt(t) for t in ts)
            sql = ("SELECT c.path, c.lang, c.start_line, c.end_line, c.text, bm25(code_fts, 1.0, 0.5) AS rank "
                   "FROM code_fts JOIN code_chunks c ON c.id=code_fts.rowid WHERE code_fts MATCH ? "
                   + ("AND c.lang=? " if lang else "") + "ORDER BY rank LIMIT ?")
            args = (match, lang, limit) if lang else (match, limit)
            return self.db.q(sql, args)
        rows = run(" AND ")
        if len(rows) < 3 and len(ts) > 1:
            rows = run(" OR ")
        return [CodeHit(r["path"], r["lang"] or "", r["start_line"], r["end_line"], r["text"], -r["rank"]) for r in rows]


def split_identifiers(text: str) -> str:
    """fetchUserProfile / fetch_user_profile -> 'fetch user profile' (code search ke liye)."""
    words = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", text.replace("_", " "))
    return " ".join(w.lower() for w in words)


# ---------- snippets / answers ----------
def snippet(text: str, ts: list[str], width: int = 280) -> str:
    low = text.lower()
    pos = min((low.find(t) for t in ts if low.find(t) >= 0), default=0)
    start = max(0, pos - width // 3)
    seg = text[start:start + width].strip()
    if start > 0:
        seg = "…" + seg
    if start + width < len(text):
        seg += "…"
    for t in sorted(set(ts), key=len, reverse=True):
        seg = re.sub(f"(?i)({re.escape(t)})", r"<mark>\1</mark>", seg)
    return seg


def sentences(text: str) -> list[str]:
    out = []
    for s in SENT_SPLIT.split(text):
        s = (s or "").strip()
        if (40 <= len(s) <= 400) or (cjk_ratio(s) > 0.3 and 12 <= len(s) <= 300):
            out.append(s)
    return out


def coverage(query: str, hits: list[Hit], top: int = 3) -> float:
    """Query ke kitne alfaaz top results mein milte hain (0..1). Kam coverage = index mein kami."""
    ts = terms(query)
    if not ts or not hits:
        return 0.0
    blob = " ".join(p.lower() for h in hits[:top] for p in h.passages) + " " + " ".join(h.title.lower() for h in hits[:top])
    return sum(1 for t in ts if t in blob) / len(ts)


def build_answer(query: str, hits: list[Hit], max_sentences: int = 5) -> dict:
    """Bina LLM ke, sirf asli crawled text se jawab. Har jumla source mein maujood hota hai."""
    ts = terms(query)
    if not hits:
        return {"answer": "", "citations": [], "confident": False}
    cands: list[tuple[float, str, int]] = []
    for i, h in enumerate(hits[:6], 1):
        for p in h.passages:
            for s in sentences(p):
                low = s.lower()
                overlap = sum(1 for t in ts if t in low)
                if overlap == 0:
                    continue
                cands.append((overlap / max(1, len(ts)) + 0.05 * (7 - i), s, i))
    cands.sort(key=lambda x: x[0], reverse=True)
    chosen, seen_norm, used = [], [], set()
    for sc, s, i in cands:
        norm = re.sub(r"\W+", " ", s.lower())[:80]
        if any(norm[:50] == n[:50] for n in seen_norm):
            continue
        seen_norm.append(norm)
        chosen.append((s, i))
        used.add(i)
        if len(chosen) >= max_sentences:
            break
    if not chosen:
        return {"answer": "", "citations": [], "confident": False}
    order = {i: n for n, i in enumerate(sorted(used), 1)}
    text = " ".join(f"{s} [{order[i]}]" for s, i in chosen)
    cites = [{"n": order[i], "title": hits[i - 1].title, "url": hits[i - 1].url, "domain": hits[i - 1].domain}
             for i in sorted(used)]
    return {"answer": text, "citations": cites, "confident": len(chosen) >= 2}


def llm_prompt(query: str, hits: list[Hit]) -> str:
    ctx = "\n\n".join(f"[{i}] {h.title} ({h.domain})\n" + "\n".join(h.passages[:2]) for i, h in enumerate(hits[:6], 1))
    return (
        "Neeche diye gaye SOURCES se hi jawab do. Agar sources mein jawab nahi hai to saaf kaho 'sources mein nahi mila'. "
        "Har claim ke baad source number likho jaise [1][2]. Kuch apni taraf se mat banao. "
        "Jawab usi zaban mein do jis mein sawal hai.\n\n"
        f"SOURCES:\n{ctx}\n\nSAWAL: {query}\n\nJAWAB:"
    )


def ground_check(text: str, hits: list[Hit]) -> tuple[str, int]:
    """Jawab ki galtiyan pakadna: bina valid citation ya sources se unrelated jumle hata do.
    (clean_text, kitne jumle hataye) wapas karta hai."""
    src_words = [set(re.findall(r"\w+", p.lower())) for h in hits[:6] for p in [" ".join(h.passages)]]
    kept, dropped = [], 0
    for s in [x for x in SENT_SPLIT.split(text) if x and x.strip()]:
        cites = [int(n) for n in re.findall(r"\[(\d+)\]", s)]
        words = set(re.findall(r"\w+", re.sub(r"\[\d+\]", "", s).lower()))
        if not words:
            continue
        if not cites or any(c < 1 or c > len(src_words) for c in cites):
            dropped += 1
            continue
        support = max(len(words & src_words[c - 1]) / len(words) for c in cites)
        if support < 0.35:
            dropped += 1
            continue
        kept.append(s.strip())
    return " ".join(kept), dropped
