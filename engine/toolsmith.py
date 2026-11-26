"""Toolsmith: website ke "Get my plans" tool aur sidebar AI ka dimaag. User ki service-zaroorat sunta hai, plan banata hai aur
Python (ya JS/HTML/Bash) mein chalne wala tool code likh kar ZIP deta hai.
Priority 1: Cloudflare Workers AI (free, open-source model, tez). Wo na ho / roz ki hadd khatam ho to apna Kaggle (Qwen) mesh.
Asal Talashly chat is se alag hai aur apni training/Qwen par rehti hai. Free quota: roz N users x M requests."""
from __future__ import annotations

import ast
import re
import time
from typing import Any, AsyncIterator

from . import projects
from .llm import _stream_chat_raw, _strip_think, stream_chat
from .mesh import cf_available, cf_cfg, MESH

SYSTEM = (
    "You are Talashly Code, a senior engineer. Output the DELIVERABLE directly: never restate, rewrite or summarise the user's request or prompt. "
    "Follow every instruction and numbered requirement of the user's prompt exactly (stack, libraries, structure, file type, features); if the prompt asks for a "
    "single-file HTML, give one complete HTML file. Files must be COMPLETE and runnable: no placeholders, no '...', no 'rest of code', never stop mid-file. "
    "Start each fenced block with its file name as a first-line comment ('# file: app.py', '<!-- file: index.html -->', '// file: app.js'). "
    "Use real libraries/CDNs only. After the code add at most 3 short lines on how to run. "
    "Reply in the user's language (Roman Urdu if they write Roman Urdu; code and technical terms in English). "
    "Ask ONE short question only if the request is impossible to build otherwise. Refuse only clearly harmful tools (malware, credential theft, spam, stalking). /no_think")
PLAN_NOTE = (" MODE=PLAN: do not write full code. Produce a clear service plan: what the tool does, features, steps, tech stack, "
             "time estimate, and what the client must provide. End with 'Say \"build it\" and I will write the code.'")
CHAT_SYSTEM = ("You are Talashly Pro, a fast, accurate and professional assistant. Reply in the user's language (Roman Urdu if they write Roman Urdu; "
               "keep technical terms and code in English). Be concise and complete; for code give one complete fenced block. If unsure, say so. /no_think")
EXT = {"python": "py", "py": "py", "javascript": "js", "js": "js", "html": "html", "bash": "sh", "sh": "sh", "json": "json",
       "css": "css", "sql": "sql", "typescript": "ts", "txt": "txt"}
FENCE = re.compile(r"```([\w+-]*)\n(.*?)```", re.S)


class Quota:
    """Roz (UTC) ki free hadd: N alag users, har ek M requests, aur ek IP se zyada se zyada K client-ids (bypass rokne ke liye)."""

    def __init__(self, cfg: dict):
        self.cfg, self.day, self.users, self.ips = cfg, "", {}, {}

    def _roll(self) -> None:
        d = time.strftime("%Y-%m-%d", time.gmtime())
        if d != self.day:
            self.day, self.users, self.ips = d, {}, {}

    def admit(self, client: str, ip: str) -> tuple[bool, str, int]:
        """-> (ok, reason, requests_left_after). limits_enabled=False: koi hadd nahi (-1)."""
        if not self.cfg.get("limits_enabled", False):
            return True, "", -1
        self._roll()
        max_u, per_u, per_ip = (int(self.cfg["free_users_per_day"]), int(self.cfg["requests_per_user"]), int(self.cfg["clients_per_ip"]))
        if client not in self.users:
            if len(self.users) >= max_u:
                return False, "users_full", 0
            if len(self.ips.setdefault(ip, set())) >= per_ip and client not in self.ips[ip]:
                return False, "ip_limit", 0
            self.users[client] = 0
            self.ips.setdefault(ip, set()).add(client)
        if self.users[client] >= per_u:
            return False, "user_limit", 0
        self.users[client] += 1
        return True, "", per_u - self.users[client]

    def status(self) -> dict:
        self._roll()
        return {"users_today": len(self.users), "users_max": int(self.cfg["free_users_per_day"]),
                "requests_per_user": int(self.cfg["requests_per_user"])}


def extract_files(text: str) -> dict[str, str]:
    files: dict[str, str] = {}
    counts: dict[str, int] = {}
    for lang, body in FENCE.findall(text):
        body = body.rstrip() + "\n"
        if len(body.strip()) < 20:
            continue
        ext = EXT.get(lang.lower(), "txt")
        m = re.match(r"\s*(?:#|//|<!--)\s*file:\s*([\w./-]{1,60})", body)
        name = m.group(1) if m else ""
        if not name or ".." in name or name.startswith("/"):
            counts[ext] = counts.get(ext, 0) + 1
            name = f"tool.{ext}" if counts[ext] == 1 else f"tool{counts[ext]}.{ext}"
        files.setdefault(name, body)
    return files


def incomplete(text: str) -> bool:
    """Jawab beech mein kata hua? (khula fence, html bina </html>, python adhoora)"""
    if text.count("```") % 2 == 1:
        return True
    for lang, body in FENCE.findall(text):
        if lang.lower() == "html" and "<html" in body.lower() and "</html>" not in body.lower():
            return True
        if lang.lower() in ("python", "py"):
            try:
                ast.parse(body)
            except SyntaxError as e:
                if re.search(r"EOF|never closed|unterminated|expected an indented block", str(e)):
                    return True
    return False


def syntax_report(files: dict[str, str]) -> list[str]:
    out = []
    for n, c in files.items():
        if n.endswith(".py"):
            try:
                ast.parse(c)
                out.append(f"{n}: syntax OK")
            except SyntaxError as e:
                out.append(f"{n}: syntax error line {e.lineno}")
    return out


MSG = {
    "users_full": "Aaj ke free users poore ho gaye. Kal dobara aayein, ya plan upgrade karwayein.",
    "user_limit": "Aaj ki aap ki free requests poori ho gayin. Kal dobara aayein.",
    "ip_limit": "Is network se aaj bohat zyada alag users aaye; kal dobara koshish karein.",
}


async def _source(messages: list[dict], max_tokens: int, llm_cfg: dict) -> AsyncIterator[tuple[str, str]]:
    """(source, token): pehle Cloudflare, na chale to Kaggle mesh."""
    if cf_available():
        started = False
        try:
            MESH.cf_left()
            MESH._cf_used += 1
            async for t in _strip_think(_stream_chat_raw(cf_cfg(), messages, max_tokens, 0.3)):
                started = True
                yield "cloudflare", t
            return
        except Exception as e:  # noqa: BLE001
            MESH._fail("cf")
            MESH.cf_last = f"{type(e).__name__}: {e}"[:200]
            if started:
                raise
    async for t in stream_chat(llm_cfg, messages, max_tokens=max_tokens, temperature=0.3):
        yield "mesh", t


async def run(cfg: dict, quota: Quota, llm_cfg: dict, client: str, ip: str, message: str, history: list[dict] | None,
              mode: str = "build") -> AsyncIterator[tuple[str, Any]]:
    tc = cfg["toolsmith"]
    if not tc.get("enabled", True):
        yield "error", {"code": "disabled", "message": "Toolsmith abhi band hai."}
        return
    message = (message or "").strip()
    if not message:
        yield "error", {"code": "empty", "message": "Khali message."}
        return
    if len(message) > int(tc["max_message_chars"]):
        yield "error", {"code": "too_long", "message": f"Message lamba hai (max {tc['max_message_chars']})."}
        return
    ok, why, left = quota.admit(client, ip)
    if not ok:
        yield "error", {"code": why, "message": MSG[why]}
        return
    msgs = [{"role": "system", "content": CHAT_SYSTEM if mode == "chat" else SYSTEM + (PLAN_NOTE if mode == "plan" else "")}]
    for h in (history or [])[-6:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": str(h["content"])[:3000]})
    msgs.append({"role": "user", "content": message})
    yield "meta", {"left_today": left, "mode": mode}
    text, src = "", ""
    cap = 700 if mode == "plan" else 1500 if mode == "chat" else 3500
    cont = 0
    while True:
        was_open = text.count("```") % 2 == 1
        head, decided = "", not was_open
        call = msgs if not text else msgs + [
            {"role": "assistant", "content": text[-14000:]},
            {"role": "user", "content": "Continue EXACTLY from the last character. Do not repeat anything, do not re-open the code fence if inside a block, do not add commentary. Finish every remaining file completely."}]
        try:
            async for src, tok in _source(call, cap, llm_cfg):
                if not decided:
                    head += tok
                    if len(head) < 40 and "\n" not in head:
                        continue
                    decided = True
                    if head.lstrip().startswith("```"):     # model ne fence dobara khola: hata do
                        head = head.split("\n", 1)[1] if "\n" in head else ""
                    tok = head
                    if not tok:
                        continue
                text += tok
                yield "token", tok
            if not decided and head:
                text += head
                yield "token", head
        except Exception:  # noqa: BLE001
            if not text:
                yield "error", {"code": "busy", "message": "AI abhi masroof hai, thori der baad dobara try karein."}
                return
            break
        if mode == "plan" or cont >= 3 or not incomplete(text):
            break
        cont += 1
        yield "step", {"msg": f"Jawab lamba hai, aage likh raha hoon ({cont}/3)..."}
    files = extract_files(text) if mode == "build" else {}
    if files:
        rep = syntax_report(files)
        if rep:
            yield "check", {"lines": rep}
        folder = "tool"
        data = projects.make_zip(folder, {**files, "README.txt": "Generated by Talashly Toolsmith. Run the main file as described in the chat.\n"})
        token = projects.put(f"{folder}.zip", data)
        if token:
            yield "download", {"url": f"/api/dl/{token}", "name": f"{folder}.zip", "size": len(data)}
    yield "done", {"mode": "toolsmith", "source": src or "none", "files": len(files)}
