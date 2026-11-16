"""Brain: khud faisla karne wala controller.

Har sawal par ye tay karta hai:  KYA (intent)  ->  KAISE (plan: sub-queries, code ya web, live research)
->  KAHAN (konsi sources)  ->  KIS TARAH (LLM ya extractive)  ->  THEEK HAI? (verify)  ->  SEEKHO (gaps, naye domains).
Sab faislay brain_log mein likhe jate hain taake dekha ja sake ke system ne kya aur kyun kiya.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, AsyncIterator, Callable
from urllib.parse import quote, quote_plus

from .codeindex import index_paths
from .lang import tr
from .db import DB, domain_of, normalize_url
from .extract import extract_links, weight
from .llm import llm_alive, stream_local_llm
from .search import (Hit, Searcher, build_answer, coverage, detect_script, ground_check, llm_prompt, snippet,
                     terms)

CODE_WORDS = re.compile(
    r"(```|\bdef\s+\w+|\bclass\s+\w+|\bimport\s+\w+|\bfunction\b|\btraceback\b|\bexception\b|\bstack\s?trace\b|"
    r"\bsyntax\b|\bcompile[dr]?\b|\bsnippet\b|\bimplement\b|\bsegfault\b|\bnull\s?pointer\b|\w+\(\)|\w+\.\w+\()", re.I)
CODE_TOKENS = re.compile(
    r"(\bdef\b|\bself\b|\braise\b|=>|\bconst\b|#include|\bpublic\s+(?:static|class)\b|\bSELECT\b.+\bFROM\b|"
    r"\bimport\s+\w+\s+from\b|\bnpm\s+install\b|\bpip\s+install\b)", re.I)
UNAMBIGUOUS_LANGS = re.compile(r"\b(python|javascript|typescript|golang|php|sql|bash|powershell|kotlin|c\+\+|c#)\b", re.I)
IDENT = re.compile(r"\b[a-z]+_[a-z_0-9]+\b|\b[a-z]+[A-Z][A-Za-z0-9]+\b")
FILEEXT = re.compile(r"\b\w+\.(py|js|ts|tsx|java|go|rs|cpp|cs|php|rb|sql|sh|bat|html|css)\b", re.I)
LANG_WORDS = {
    "python": "python", "javascript": "javascript", "js": "javascript", "typescript": "typescript",
    "java": "java", "golang": "go", "rust": "rust", "c++": "cpp", "csharp": "csharp", "c#": "csharp",
    "php": "php", "ruby": "ruby", "sql": "sql", "bash": "shell", "powershell": "powershell",
    "kotlin": "kotlin", "swift": "swift", "dart": "dart", "lua": "lua",
}
COMMAND = re.compile(r"^\s*(?:/(add|crawl|seed|status|gaps|heal|index|log)\b|(add|crawl)\s+(?:site\s+)?(?=https?://))\s*(.*)$",
                     re.I | re.S)
JUNK_DOMAINS = {
    "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com", "youtube.com", "youtu.be", "t.co",
    "google.com", "pinterest.com", "tiktok.com", "bit.ly", "goo.gl", "doubleclick.net", "amazon.com",
    "whatsapp.com", "telegram.org", "t.me", "play.google.com", "apps.apple.com", "wa.me",
}


def is_public_host(host: str) -> bool:
    """Auto-expand kabhi localhost/private IP/internal naam ko add na kare (SSRF se bachao)."""
    import ipaddress

    h = host.lower().strip("[]")
    if h in ("localhost", "") or h.endswith((".local", ".localhost", ".internal", ".lan", ".home", ".corp")):
        return False
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return "." in h  # normal domain naam
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _join_list(parts: list[str]) -> str:
    return ", ".join(parts)


class Brain:
    def __init__(self, cfg: dict, db: DB, searcher: Searcher, sources: list[dict] | None = None):
        self.cfg, self.db, self.searcher = cfg, db, searcher
        self.b = cfg["brain"]
        self.sources = sources or []
        # supervisor/server in hooks ko set karte hain
        self.get_crawler: Callable[[], Any] = lambda: None
        self.heal_now: Callable[[], Any] | None = None

    # ---------------------------------------------------------------- understanding
    def classify(self, q: str, is_admin: bool = False) -> str:
        if is_admin and COMMAND.match(q):
            return "command"
        if q.lower().lstrip().startswith("code:"):
            return "code"
        score = 0
        if "```" in q:
            score += 3
        score += min(2, len(CODE_WORDS.findall(q)))
        if CODE_TOKENS.search(q):
            score += 1
        if UNAMBIGUOUS_LANGS.search(q):
            score += 1
        if IDENT.search(q):
            score += 1
        if FILEEXT.search(q):
            score += 1
        if re.search(r"\b(code|snippet|function|method|bug|api example)\b", q, re.I):
            score += 1
        return "code" if score >= 3 else "question"

    def decompose(self, q: str, deep: bool = False) -> list[str]:
        """Mushkil sawal ko chhote sawalon mein todo. Original sawal hamesha pehle."""
        q = q.strip()
        parts: list[str] = []
        m = re.split(r"\s+(?:vs\.?|versus|compared to|aur|and|اور|और|и)\s+|[?;,]|\s+،\s+|،|[？；，]", q, flags=re.I)
        for p in m:
            p = p.strip(" .")
            if len(terms(p)) >= 1 and p.lower() != q.lower() and len(p) >= 3:
                parts.append(p)
        if len(terms(q)) <= 3 and weight(q) <= 6:  # chhota sawal: na todo (CJK mein tokens nahi, lamba-pan dekho)
            parts = []
        limit = 5 if deep else 3
        seen, out = set(), [q]
        seen.add(q.lower())
        for p in parts:
            if p.lower() not in seen:
                seen.add(p.lower())
                out.append(p)
            if len(out) > limit:
                break
        return out

    # ---------------------------------------------------------------- live research
    def _frontier_candidates(self, ts: list[str], limit: int) -> list[str]:
        """Sitemap/links se jo URLs queue mein pare hain par abhi fetch nahi hue: jinka URL sawal se milta hai pehle."""
        scores: dict[str, int] = {}
        for t in ts:
            if len(t) < 3:
                continue
            t_ = t.replace(" ", "-")
            for r in self.db.q(
                "SELECT url FROM frontier WHERE status='queued' AND kind='page' AND lower(url) LIKE ? "
                "AND domain NOT IN (SELECT domain FROM domain_state WHERE paused_until>?) LIMIT 60",
                (f"%{t_.lower()}%", time.time())):
                scores[r["url"]] = scores.get(r["url"], 0) + 1
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], len(kv[0])))
        return [u for u, s in ranked[:limit]]

    def _direct_urls(self, ts: list[str], script: str) -> list[str]:
        out: list[str] = []
        if not ts:
            return out
        def sentence_case(words: list[str]) -> str:  # Wikipedia style: Quantum_computing
            return "_".join([words[0][:1].upper() + words[0][1:], *words[1:]])

        titles: list[str] = []
        head = ts[:4]
        titles.append(sentence_case(head))                                   # poora jumla
        if len(head) > 1:
            titles.append("_".join(w[:1].upper() + w[1:] for w in head))     # Title_Case (New_York_City)
        for n in range(len(head) - 1, 1, -1):                                # prefixes: Quantum_computing
            titles.append(sentence_case(head[:n]))
        if len(head[0]) >= 3:
            titles.append(head[0][:1].upper() + head[0][1:])                 # sirf pehla lafz
        seen: set[str] = set()
        titles = [t for t in titles if not (t in seen or seen.add(t))]
        for src in self.sources:
            if src.get("kind") != "direct":
                continue
            if src.get("script") and src["script"] != script:
                continue
            for t in titles[:4]:
                out.append(src["url"].replace("{title}", quote(t, safe="_()'")).replace("{q}", quote_plus(t)))
        return out

    async def _search_source_links(self, crawler, src: dict, q: str) -> list[str]:
        url = src["url"].replace("{q}", quote_plus(q))
        await crawler._open()
        if not await crawler.robots.allowed(url):
            return []
        await crawler._wait_turn(url, fast=True)
        r = await crawler.client.get(url)
        if r.status_code >= 400:
            return []
        dom = domain_of(url)
        need = src.get("link_contains", "")
        links = [l for l in extract_links(r.text, url) if domain_of(l) == dom and need in l and l != normalize_url(url)]
        return links[: int(src.get("max_links", 4))]

    async def live_research(self, q: str, force: bool = False) -> dict[str, Any]:
        crawler = self.get_crawler()
        out = {"tried": 0, "indexed": 0, "urls": []}
        if crawler is None or not self.b["live_research"] or self.b["live_fetch_max"] <= 0:
            return out
        ts = terms(q)
        script = detect_script(q)
        cap = self.b["live_fetch_max"]
        urls = self._frontier_candidates(ts, cap)
        urls += [u for u in self._direct_urls(ts, script) if u not in urls]
        deadline = self.b["live_timeout_s"]

        async def gather_search_links():
            extra: list[str] = []
            for src in self.sources:
                if src.get("kind") == "search":
                    try:
                        extra += await self._search_source_links(crawler, src, q)
                    except Exception:  # noqa: BLE001
                        continue
            return extra

        try:
            urls += [u for u in await asyncio.wait_for(gather_search_links(), timeout=deadline / 2) if u not in urls]
        except asyncio.TimeoutError:
            pass
        urls = urls[:cap]
        out["urls"] = urls
        if not urls:
            return out

        async def one(u: str) -> bool:
            ok = await crawler.fetch_now(u, topic="live")
            if not ok:  # galat guess (404 waghera) ko frontier mein error bana kar na chhoro
                self.db.x("DELETE FROM frontier WHERE url=? AND status='error' AND depth<=1", (normalize_url(u),))
            return ok and bool(self.db.q("SELECT 1 FROM docs WHERE url=?", (normalize_url(u),)))

        tasks = [asyncio.create_task(one(u)) for u in urls]
        done, pending = await asyncio.wait(tasks, timeout=deadline)
        for p in pending:
            p.cancel()
        out["tried"] = len(urls)
        out["indexed"] = sum(1 for t in done if not t.cancelled() and t.exception() is None and t.result())
        self.db.log("live", json.dumps({"q": q[:80], **{k: out[k] for k in ("tried", "indexed")}}))
        return out

    # ---------------------------------------------------------------- learning
    def record_gap(self, q: str) -> None:
        key = re.sub(r"\s+", " ", q.strip().lower())[:200]
        self.db.x(
            "INSERT INTO gaps(q,n,last_at,resolved,tries) VALUES(?,1,?,0,0) "
            "ON CONFLICT(q) DO UPDATE SET n=n+1, last_at=excluded.last_at, resolved=0",
            (key, time.time()))
        self.db.log("gap", key)

    async def work_gap(self) -> str | None:
        """Background mein ek unresolved sawal par dobara koshish: naya content aa gaya ho to gap band."""
        rows = self.db.q("SELECT q,tries FROM gaps WHERE resolved=0 AND tries<? ORDER BY n DESC, last_at DESC LIMIT 1",
                         (self.b["gap_retries"],))
        if not rows:
            return None
        q = rows[0]["q"]
        self.db.x("UPDATE gaps SET tries=tries+1 WHERE q=?", (q,))
        await self.live_research(q)
        hits = self.searcher.search_many([q], 8)
        if hits and coverage(q, hits) >= self.b["weak_coverage"]:
            self.db.x("UPDATE gaps SET resolved=1 WHERE q=?", (q,))
            self.db.log("gap-resolved", q)
            return q
        return None

    def expand_domains(self, db: DB | None = None) -> list[str]:
        """Bahut si allowed sites jis domain ko link karti hain, usay khud add kar lo (unlimited growth, par control mein)."""
        db = db or self.db
        if not self.b["auto_expand"]:
            return []
        n_allowed = db.q("SELECT COUNT(*) FROM allowed_domains")[0][0]
        room = self.b["max_domains"] - n_allowed
        if room <= 0:
            return []
        added = []
        for r in db.q("SELECT domain, sample FROM candidates WHERE promoted=0 AND votes>=? ORDER BY votes DESC LIMIT ?",
                      (self.b["expand_votes"], min(5, room))):
            d = r["domain"]
            db.x("UPDATE candidates SET promoted=1 WHERE domain=?", (d,))
            if d in JUNK_DOMAINS or any(d.endswith("." + j) for j in JUNK_DOMAINS) or not is_public_host(d):
                continue
            db.x("INSERT OR IGNORE INTO allowed_domains(domain) VALUES(?)", (d,))
            db.add_url(f"https://{d}/", 0, "page", "auto")
            added.append(d)
            db.log("expand", d)
        return added

    # ---------------------------------------------------------------- admin commands
    async def command(self, q: str) -> str:
        m = COMMAND.match(q)
        verb = (m.group(1) or m.group(2)).lower()
        arg = (m.group(3) or "").strip()
        if verb in ("add", "crawl", "seed"):
            um = re.match(r"(https?://\S+)(?:\s+(feed|sitemap|page))?", arg, re.I)
            if not um:
                return "Format: /add https://site.com [feed|sitemap|page]"
            url = normalize_url(um.group(1))
            kind = (um.group(2) or ("feed" if re.search(r"(rss|atom|feed)", url, re.I) else
                                    "sitemap" if "sitemap" in url else "page")).lower()
            self.db.x("INSERT OR IGNORE INTO allowed_domains(domain) VALUES(?)", (domain_of(url),))
            added = self.db.add_url(url, 0, kind, "manual")
            self.db.log("cmd", f"add {kind} {url}")
            return f"{'Add kar diya' if added else 'Pehle se maujood'}: {url} ({kind}). Crawler isay agli baari mein uthayega."
        if verb == "status":
            c = self.db.counts()
            return (f"Pages: {c['docs']}, sites: {c['domains']}, queue: {c['queued']}, errors: {c['errors']}, "
                    f"code chunks: {c['code_chunks']}, open gaps: {c['open_gaps']}, users: {c['users']}.")
        if verb == "gaps":
            rows = self.db.q("SELECT q,n,tries FROM gaps WHERE resolved=0 ORDER BY n DESC LIMIT 10")
            return "Unresolved sawal: " + (_join_list([f"{r['q']} (x{r['n']})" for r in rows]) or "koi nahi")
        if verb == "log":
            rows = self.db.q("SELECT kind,detail FROM brain_log ORDER BY id DESC LIMIT 10")
            return "\n".join(f"{r['kind']}: {r['detail']}" for r in rows) or "log khali hai"
        if verb == "heal":
            if self.heal_now:
                return await self.heal_now()
            return "heal hook set nahi"
        if verb == "index":
            if not arg:
                return "Format: /index C:\\path\\to\\project"
            st = await asyncio.to_thread(self._index_code, [arg])
            return f"Code index: {st}"
        return "unknown command"

    def _index_code(self, paths: list[str]) -> dict:
        db = DB(self.db.path)  # thread ke liye apna connection
        return index_paths(db, paths, self.cfg["code"]["max_file_kb"])

    # ---------------------------------------------------------------- main entry
    def _hit_json(self, h: Hit, ts: list[str], n: int) -> dict:
        return {"n": n, "title": h.title, "url": h.url, "domain": h.domain,
                "snippet": snippet(h.passages[0], ts), "published_at": h.published_at,
                "fetched_at": h.fetched_at, "score": round(h.score, 3)}

    async def run(self, q: str, role: str = "user", deep: bool = False, stream_delay: float = 0.012,
                  live: bool | None = None, sentences: int | None = None, nsources: int | None = None,
                  weak_cov: float | None = None) -> AsyncIterator[tuple[str, Any]]:
        t0 = time.time()
        q = re.sub(r"\s+", " ", q).strip()[:500]
        intent = self.classify(q, role == "admin")
        script = detect_script(q)

        if intent == "command":
            yield "plan", {"intent": "command"}
            yield "token", await self.command(q)
            yield "done", {"mode": "command"}
            return

        if intent == "code":
            cq = re.sub(r"^\s*code:\s*", "", q, flags=re.I)
            lang = next((v for k, v in LANG_WORDS.items() if re.search(rf"(?<!\w){re.escape(k)}(?!\w)", cq, re.I)), None)
            yield "plan", {"intent": "code", "language": lang, "script": script}
            ch = self.searcher.search_code(cq, lang) or (self.searcher.search_code(cq) if lang else [])
            if ch:
                yield "code", [{"path": c.path, "lang": c.lang, "start": c.start, "end": c.end,
                                "text": "\n".join(c.text.splitlines()[:25]), "score": round(c.score, 3)} for c in ch[:8]]
                best = ch[0]
                yield "token", tr("code_found", q, n=len(ch), path=best.path, a=best.start, b=best.end, lang=best.lang)
                yield "done", {"mode": "code", "took_ms": round((time.time() - t0) * 1000)}
                return
            yield "step", {"msg": tr("code_miss", q)}
            q = cq

        subs = self.decompose(q, deep)
        yield "plan", {"intent": "question", "script": script, "subqueries": subs, "deep": deep}
        self.db.log("plan", json.dumps({"q": q[:80], "subs": subs, "script": script}, ensure_ascii=False))
        ts = terms(q)
        limit = 10 if deep else 8
        hits = self.searcher.search_many(subs, limit)
        cov = coverage(q, hits)
        weak = cov < (weak_cov if weak_cov is not None else (0.9 if deep else self.b["weak_coverage"]))
        live_ok = self.b["live_research"] if live is None else (live and self.b["live_research"])

        if weak and live_ok:
            yield "step", {"msg": tr("weak", q)}
            lr = await self.live_research(q)
            if lr["indexed"]:
                yield "step", {"msg": tr("live_ok", q, n=lr["indexed"])}
                hits = self.searcher.search_many(subs, limit)
                cov = coverage(q, hits)
        if not hits or cov < self.b["weak_coverage"]:
            self.record_gap(q)  # system isay yaad rakhta hai aur background mein dobara koshish karega

        yield "sources", [self._hit_json(h, ts, i) for i, h in enumerate(hits[: (nsources or (10 if deep else 6))], 1)]
        if not hits:
            yield "token", tr("none", q)
            yield "done", {"mode": "none", "coverage": 0, "took_ms": round((time.time() - t0) * 1000)}
            return

        mode = "extractive"
        ans = build_answer(q, hits, sentences or (8 if deep else 5))
        use_llm = self.cfg["llm"]["enabled"] and await llm_alive(self.cfg["llm"]["base_url"])
        if use_llm:
            buf: list[str] = []
            try:
                async for tok in stream_local_llm(self.cfg["llm"]["base_url"], llm_prompt(q, hits)):
                    buf.append(tok)
                    yield "token", tok
                text = "".join(buf)
                clean, dropped = ground_check(text, hits)
                if dropped or not clean:
                    # LLM ne source se bahar ya bina citation ke likha: wo hissa hata kar ya extractive se badal do
                    yield "replace", clean or ans["answer"]
                    self.db.log("verify", f"llm answer corrected, dropped={dropped}")
                mode = "llm" if clean else "extractive"
                yield "done", {"mode": mode, "coverage": round(cov, 2), "citations": ans["citations"],
                               "took_ms": round((time.time() - t0) * 1000)}
                return
            except Exception as e:  # noqa: BLE001  local model gir gaya: extractive pe wapas
                self.db.log("heal", f"llm failed ({type(e).__name__}), extractive fallback")
                if buf:
                    yield "replace", ""
        if not ans["answer"]:
            yield "token", tr("nosent", q)
        else:
            for word in ans["answer"].split(" "):
                yield "token", word + " "
                if stream_delay:
                    await asyncio.sleep(stream_delay)
        yield "done", {"mode": mode, "coverage": round(cov, 2), "citations": ans["citations"],
                       "took_ms": round((time.time() - t0) * 1000)}
