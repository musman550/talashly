"""Talashly Agent (mus-7.01-agent): "Muse jaisa" kaam, lekin kam tokens mein.

Design: planning aur tools ZERO-LLM-token Python hain (rule planner, calc, ghadi, apna index, URL padhna, code sandbox);
LLM ko sirf EK chhoti call milti hai jis mein tools ke natije compact ho kar jate hain. Code ho to sandbox mein chala kar parakha
jata hai (GPU node ho to ghalti par ek baar khud theek karta hai). LLM na ho to tools ke natije se hi jawab (extractive/calc/ghadi).
Chat yaad nahi rakhta; history sirf follow-up par, aur chhoti.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx

from .extract import extract_page
from .lang import tr
from .llm import stream_chat
from .mesh import fast_llm_available, llm_available
from .sandbox import run_python

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
CODE_Q = re.compile(r"\b(python|script|program|function|algorithm|code|implement|simulate|simulation|compute|calculate|calculation|"
                    r"fibonacci|prime|sort|regex|parse|matrix|probability|statistics|factorial)\b", re.I)
DO_Q = re.compile(r"\b(write|create|make|build|implement|find|compute|calculate|simulate|generate|solve|check|verify|test|likho|banao|nikalo)\b", re.I)
TIME_Q = re.compile(r"\b(today|now|current(ly)?|aaj|abhi)\b.*\b(date|time|day|tarikh|waqt|din)\b|\b(date|time|day|tarikh|waqt|din)\b.*\b(today|now|aaj|abhi)\b|"
                    r"\bwhat('?s| is) the (date|time)\b", re.I)
PY_BLOCK = re.compile(r"```[ \t]*(?:python3?|py)?[ \t]*\r?\n(.*?)(?:```|\Z)", re.S | re.I)


def find_python(text: str) -> str | None:
    """Jawab mein pehla python code block: ```python / ```py / bina naam ke, aur band na hone par bhi; sirf agar valid python ho."""
    import ast
    for m in PY_BLOCK.finditer(text):
        code = m.group(1).strip()
        if not code:
            continue
        try:
            ast.parse(code)
        except SyntaxError:
            continue
        return code
    return None

SYSTEM = ("You are Talashly Agent. TOOL RESULTS are ground truth: use them, never contradict them, and cite sources like [1]. "
          "Answer in the user's language. Be complete but compact: no filler, no repeated points. "
          "For code: one ```python block that prints its results, then at most 3 lines of explanation.")


EXPR_RE = re.compile(r"\(?\d[\d\s.,()]*?\s*(?:\*\*|[-+*/%^x×÷])\s*\(?\d[\d\s.,()+\-*/%^x×÷]*")


def exprs(text: str) -> list[str]:
    """Message ke andar ke hisaab ("12*13 and date" mein 12*13): har tukre par calc (ast, eval nahi). Max 3."""
    from .chat import calc
    out: list[str] = []
    for m in EXPR_RE.finditer(text):
        r = calc(m.group(0).strip())
        if r and r not in out:
            out.append(r)
        if len(out) == 3:
            break
    return out


def make_plan(text: str) -> list[str]:
    """Rule-based planner (0 token). Tools ke naam ki list, chalne ke tarteeb mein (sab parallel chalte hain)."""
    plan: list[str] = []
    if URL_RE.search(text):
        plan.append("url")
    if exprs(text):
        plan.append("calc")
    if TIME_Q.search(text):
        plan.append("clock")
    code = bool(CODE_Q.search(text) and DO_Q.search(text))
    if code:
        plan.append("code")
    if not plan or (not code and "url" not in plan) or len(text.split()) > 12:
        plan.append("search")
    return plan


def _public_ip_only(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for i in infos:
        ip = ipaddress.ip_address(i[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return bool(infos)


async def fetch_url(url: str) -> dict[str, Any]:
    """Public URL (SSRF-safe: har redirect par dobara check), 500 KB cap, text nikal kar."""
    cur = url
    async with httpx.AsyncClient(timeout=8, follow_redirects=False, headers={"User-Agent": "TalashlyAgent/1.0"}) as c:
        for _ in range(4):
            u = urlparse(cur)
            if u.scheme not in ("http", "https") or not u.hostname or not await asyncio.to_thread(_public_ip_only, u.hostname):
                return {"ok": False, "err": "blocked or private address"}
            async with c.stream("GET", cur) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    cur = str(httpx.URL(cur).join(r.headers["location"]))
                    continue
                if r.status_code != 200:
                    return {"ok": False, "err": f"HTTP {r.status_code}"}
                body = b""
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > 500_000:
                        break
                html = body.decode(r.encoding or "utf-8", "replace")
        ex = await asyncio.to_thread(extract_page, html, cur)
        if not ex:
            return {"ok": False, "err": "no readable text"}
        return {"ok": True, "url": cur, "title": ex.title, "text": ex.text[:1500]}
    return {"ok": False, "err": "too many redirects"}


def _clock_text() -> str:
    import datetime as dt
    n = dt.datetime.now(dt.timezone.utc)
    p = n.astimezone(dt.timezone(dt.timedelta(hours=5)))
    return f"{n:%A, %d %B %Y}, {n:%H:%M} UTC ({p:%H:%M} Pakistan)"


def _est(s: str) -> int:
    return max(1, len(s) // 4)


async def run(chat: Any, text: str, history: list[dict] | None, prev_user: str | None, model: Any) -> AsyncIterator[tuple[str, Any]]:
    from .chat import _follow_up, strip_cites
    t0 = time.time()
    plan = make_plan(text)
    yield "step", {"msg": "Plan: " + " → ".join(plan) + (" → answer" if True else "")}
    res: dict[str, Any] = {}

    async def t_calc():
        res["calc"] = "; ".join(exprs(text))

    async def t_clock():
        res["clock"] = _clock_text()

    async def t_url():
        urls = URL_RE.findall(text)[:2]
        res["url"] = await asyncio.gather(*[fetch_url(u) for u in urls])

    async def t_search():
        parts = (await asyncio.to_thread(chat.brain.decompose, text, True))[:3] or [text]
        hits = await asyncio.to_thread(chat.searcher.search_many, parts, 6)
        if len(hits) < 2 and model.live:
            await chat.brain.live_research(text)
            hits = await asyncio.to_thread(chat.searcher.search_many, parts, 6)
        res["hits"] = hits

    tools = {"calc": t_calc, "clock": t_clock, "url": t_url, "search": t_search}
    tasks = {asyncio.create_task(tools[n]()): n for n in plan if n in tools}
    errors: list[str] = []
    for fut in asyncio.as_completed(list(tasks)):
        name = tasks[fut] if fut in tasks else "tool"
        try:
            await fut
            yield "step", {"msg": f"✓ {name} ({round((time.time() - t0) * 1000)} ms)"}
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}")
            yield "step", {"msg": f"✗ {name} failed, continuing"}

    # --- compact context (token bachat)
    ctx: list[str] = []
    if res.get("calc"):
        ctx.append(f"[calc] {res['calc']}")
    if res.get("clock"):
        ctx.append(f"[clock] {res['clock']}")
    for p in res.get("url", []):
        ctx.append(f"[page {p['url']}] {p['title']}: {p['text'][:900]}" if p.get("ok") else f"[page] could not read: {p.get('err')}")
    hits = (res.get("hits") or [])[:3]
    for i, h in enumerate(hits, 1):
        ctx.append(f"[{i}] {h.title} ({h.domain}): " + " ".join(h.passages[:2])[:420])
    sources = [{"title": h.title, "url": h.url, "domain": h.domain} for h in hits]

    ready = llm_available(chat.cfg["llm"])
    answered_by = "rule"
    buf = ""
    p_tok = c_tok = 0
    verified: bool | None = None
    if ready and (ctx or "code" in plan or not plan):
        from .hints import extra as _extra
        msgs: list[dict] = [{"role": "system", "content": SYSTEM + _extra(text)}]
        if history and _follow_up(text):
            for h in history[-2:]:
                msgs.append({"role": h.get("role", "user"), "content": str(h.get("content", ""))[:600]})
        body = ("TOOL RESULTS:\n" + "\n".join(ctx) + "\n\n" if ctx else "") + "QUESTION: " + text + "\n/no_think"
        msgs.append({"role": "user", "content": body})
        p_tok = sum(_est(m["content"]) for m in msgs)
        yield "step", {"msg": f"writing answer (~{p_tok} prompt tokens)"}
        try:
            async for tok in stream_chat(chat.cfg["llm"], msgs, max_tokens=700 if "code" in plan else 450, temperature=0.3):
                buf += tok
                yield "token", tok
            c_tok = _est(buf)
            answered_by = "model"
        except Exception as e:  # noqa: BLE001
            chat.db.log("error", f"agent llm failed {type(e).__name__}: {e}")
            errors.append("llm")
        # --- code ko sandbox mein chala kar parakho
        code = find_python(buf) if buf else None
        if code:
            yield "step", {"msg": "running code in sandbox"}
            try:
                r = await asyncio.to_thread(run_python, code)
            except Exception as e:  # noqa: BLE001  sandbox ki apni kharabi ho to bhi jawab poora aaye
                chat.db.log("error", f"sandbox failed {type(e).__name__}: {e}")
                r = {"ok": False, "out": "", "err": f"sandbox unavailable ({type(e).__name__})", "blocked": True}
            if not r["ok"] and fast_llm_available() and not r["blocked"]:
                yield "step", {"msg": "code failed, fixing once"}
                fix = msgs + [{"role": "assistant", "content": buf[:1500]},
                              {"role": "user", "content": f"The code failed: {r['err']}. Return only the corrected ```python block.\n/no_think"}]
                fixed = ""
                try:
                    async for tok in stream_chat(chat.cfg["llm"], fix, max_tokens=600, temperature=0.2):
                        fixed += tok
                except Exception:  # noqa: BLE001
                    fixed = ""
                fixed_code = find_python(fixed)
                if fixed_code:
                    r2 = await asyncio.to_thread(run_python, fixed_code)
                    if r2["ok"]:
                        note = "\n\n**Corrected code (the first version failed):**\n```python\n" + fixed_code + "\n```"
                        buf += note
                        yield "token", note
                        r = r2
            verified = bool(r["ok"])
            note = ("\n\n**Run check ✅** output:\n```\n" + r["out"].strip()[:800] + "\n```") if r["ok"] else \
                   f"\n\n**Run check ⚠️** the code did not run cleanly: `{r['err'] or 'no output'}`. Review it before using."
            buf += note
            yield "token", note
    if not buf:
        # LLM nahi/ghalat: tools ke natije se seedha jawab
        lines: list[str] = []
        if res.get("calc"):
            lines.append(str(res["calc"]))
        if res.get("clock"):
            lines.append("Now: " + res["clock"])
        for p in res.get("url", []):
            lines.append(f"{p['title']}: {p['text'][:500]}" if p.get("ok") else f"Could not read the page ({p.get('err')}).")
        if hits:
            lines.append(strip_cites(" ".join(hits[0].passages[:1])[:500]))
        if "code" in plan or "llm" in errors or not ready:
            lines.append(tr("agent_nollm", text))
        buf = "\n\n".join(lines) or tr("error", text)
        yield "token", buf
    yield "done", {"mode": "agent", "agent": {"plan": plan, "errors": errors, "verified": verified, "by": answered_by,
                   "tokens": {"prompt": p_tok, "completion": c_tok}, "sandbox_err": (locals().get("r") or {}).get("err", "")[:120], "tool_ms": round((time.time() - t0) * 1000)},
                   "sources": sources}
