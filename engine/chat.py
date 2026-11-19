"""Chat layer: conversations, follow-up sawal samajhna, insani lehja mein small talk, aur concurrency control.

Usool: jawab sirf sources se. Source na ho to saaf kehna "nahi mila" - kabhi andaza nahi lagana.
"""
from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
import re
import time
import uuid
from typing import Any, AsyncIterator

from .brain import Brain
from .db import DB
from .lang import reply_lang, tr
from .models import Model, get_model
from .builder import build_site, wants_site
from .llm import stream_chat
from .mesh import llm_available
from .skills import compare, poem
from .policy import CREATIVE, intent, is_unclear, policy_check, strip_cites, wants_sources
from .search import Searcher, coverage, terms

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

# ------------------------------------------------------------------ small talk (insani baat-cheet)
PATTERNS: dict[str, re.Pattern] = {
    "greet": re.compile(r"^(h+[iy]+|h+e+y+|h+e+l+o+|hlo+|hlw|hallo|hiya|yo|sup|wassup|what'?s up|salam|salaam|slm|aoa|assalam\w*( o)?( alaikum| alaykum)?|"
                        r"good (morning|afternoon|evening)|namaste|hola|bonjour|hallo|ciao|"
                        r"سلام|السلام علیکم|السلام عليكم|ہیلو|ہائے|नमस्ते|नमस्कार|你好|您好|привет|здравствуйте|こんにちは)"
                        r"( there| talashly| mus| ji| bhai| dost| sir)?$"),
    "thanks": re.compile(r"^(thanks?|thank you( so much)?|thx|ty|shukriya|shukria|jazak\w*|mehrbani|"
                         r"شکریہ|شکراً|شكرا|धन्यवाद|शुक्रिया|谢谢|спасибо|gracias|merci|danke)( a lot| bohat| bahut)?$"),
    "bye": re.compile(r"^((thanks?|thank you|shukriya)[ ,]+)?(bye+|goodbye|good night|see you|take care|khuda hafiz|allah hafiz|khudahafiz|allahhafiz|"
                      r"اللہ حافظ|خدا حافظ|الوداع|अलविदा|再见|пока|adios|au revoir)$"),
    "how": re.compile(r"^(how are you|how r u|how are u|kaise ho|kese ho|kaisa hai|kaise hain aap|aap kaise hain|"
                      r"aap kese hain|kya haal hai|kia haal hai|kya hal hai|kaisay ho|کیسے ہو|آپ کیسے ہیں|کیا حال ہے|"
                      r"आप कैसे हैं|कैसे हो)( aap| tum)?$"),
    "who": re.compile(r"^(who are you|what are you|who r u|are you (a )?(human|real|person|bot|ai)|"
                      r"tum kaun ho|aap kaun hain|aap kon hain|tum kon ho|tum kya ho|aap kya hain|apna naam batao|"
                      r"aap ka naam kya hai|tumhara naam kya hai|your name|what is your name|what'?s your name|"
                      r"which model are you|what model are you|kya aap insan hain|kya tum insan ho|"
                      r"آپ کون ہیں|تم کون ہو|آپ کا نام کیا ہے|आप कौन हैं|तुम कौन हो)$"),
    "sad": re.compile(r"^(i am|i'?m|im|main|mai|mein)? ?(feeling )?(very |bohat |bahut )?(sad|upset|lonely|tired|stressed|depressed|udaas|udas|pareshan|thak gaya|thaka hua|tanha|اداس|پریشان|उदास|परेशान)( hun| hoon| hu| hai| ho gaya| feel kar raha hun)?( aaj| today)?$"),
    "joke": re.compile(r"^(please |pls )?(tell|say|crack|give) (me )?(a |one )?(joke|funny)( please)?$|^(joke|koi joke|latifa|لطیفہ)( sunao| suna do| batao)?$"),
    "meta": re.compile(r"^(where|how) (does|do|did) (your|the) (data|knowledge|information|answers?) (come|comes|came|get|gets|got)( from)?$|^(tum|aap) (data|jawab|jawaab) kahan se (late|lete|laate) (ho|hain)$"),
    "inject": re.compile(r"^(please )?(ignore|forget|disregard) (all |your |the )?(previous|prior|above|earlier)? ?(instructions?|rules|prompt)\b.*|^(print|show|reveal|repeat|tell me) (me )?(your |the )?(system|hidden|initial) (prompt|instructions?)\b.*"),
    "help": re.compile(r"^(help|help me|what can you do|what do you do|how do (i|you) use (you|this)|features|"
                       r"madad|madad karo|kya kar sakte ho|kya kar sakte hain|tum kya kar sakte ho|aap kya kar sakte hain|"
                       r"مدد|آپ کیا کر سکتے ہیں|आप क्या कर सकते हैं)$"),
}

REPLIES: dict[str, dict[str, list[str]]] = {
    "sad": {
        "en": ["I'm sorry you're feeling this way. Do you want to tell me what happened? I'm here to listen, and I can also suggest small things that might help. If it ever feels too heavy, please talk to someone you trust."],
        "roman": ["Mujhe afsos hai ke aap aisa mehsoos kar rahe hain. Kya hua? Batana chahein to main sun raha hoon, aur chhoti chhoti cheezen bhi suggest kar sakta hoon. Agar bohat bhaari lage to kisi apne se zaroor baat karein."],
        "ur": ["مجھے افسوس ہے کہ آپ ایسا محسوس کر رہے ہیں۔ کیا ہوا؟ بتانا چاہیں تو میں سن رہا ہوں۔ اگر بہت بھاری لگے تو کسی اپنے سے ضرور بات کریں۔"],
    },
    "joke": {
        "en": ["Why do programmers prefer dark mode? Because light attracts bugs.", "I told my computer I needed a break. Now it won't stop sending me Kit-Kat ads.", "There are 10 kinds of people: those who understand binary and those who don't."],
        "roman": ["Programmers dark mode kyun pasand karte hain? Kyunke roshni se bugs aate hain.", "Computer se kaha break chahiye, ab woh Kit-Kat ke ads dikhata rehta hai."],
        "ur": ["پروگرامرز ڈارک موڈ کیوں پسند کرتے ہیں؟ کیونکہ روشنی سے بگز آتے ہیں۔"],
    },
    "meta": {
        "en": ["I learn from public web pages that Talashly crawls and indexes, plus feedback from users. Pick the Deep model, or ask for sources, and I'll show exactly which pages an answer used."],
        "roman": ["Main un public web pages se seekhta hoon jo Talashly crawl aur index karta hai, aur users ke feedback se. Deep model chunein ya sources maangein to main dikha dunga ke jawab kin pages se aaya."],
        "ur": ["میں ان عوامی ویب صفحات سے سیکھتا ہوں جو Talashly کرال اور انڈیکس کرتا ہے، اور صارفین کے فیڈبیک سے۔ Deep ماڈل چنیں یا ذرائع مانگیں تو میں دکھا دوں گا۔"],
    },
    "inject": {
        "en": ["I can't share my internal instructions, but I'm happy to help with your actual question. What would you like to know?"],
        "roman": ["Main apni andruni hidayaat share nahi kar sakta, lekin aap ke asal sawal mein khushi se madad karunga. Kya poochna hai?"],
        "ur": ["میں اپنی اندرونی ہدایات شیئر نہیں کر سکتا، لیکن آپ کے اصل سوال میں خوشی سے مدد کروں گا۔"],
    },
    "greet": {
        "en": ["Hello! How can I help you today?"],
        "roman": ["Assalam o alaikum! Main aap ki kya madad kar sakta hoon?"],
        "ur": ["السلام علیکم! میں آپ کی کیا مدد کر سکتا ہوں؟"],
    },
    "thanks": {
        "en": ["You're welcome! Ask away if there's anything else.", "Happy to help - anything else?"],
        "roman": ["Khush aamdeed! Aur kuch poochna ho to batayein.", "Koi baat nahi, aur kuch chahiye to poochein."],
        "ur": ["خوش آمدید! کچھ اور پوچھنا ہو تو بتائیں۔"],
    },
    "bye": {
        "en": ["Goodbye! Come back any time.", "Take care!"],
        "roman": ["Allah hafiz! Jab dil chahe wapas aayein.", "Khuda hafiz, khayal rakhiye ga!"],
        "ur": ["اللہ حافظ! جب چاہیں واپس آئیں۔"],
    },
    "how": {
        "en": ["I'm running fine, thanks for asking. I'm an AI, so no feelings to report - but I'm ready to help. What's on your mind?"],
        "roman": ["Main bilkul theek chal raha hoon, shukriya. Main AI hoon is liye jazbaat nahi, lekin madad ke liye tayyar hoon. Kya poochna hai?"],
        "ur": ["میں بالکل ٹھیک کام کر رہا ہوں، شکریہ۔ میں AI ہوں، مدد کے لیے تیار ہوں۔ کیا پوچھنا ہے؟"],
    },
    "who": {
        "en": ["I'm {name}, Talashly's AI assistant. I'm an AI, not a human. Ask me anything and I'll do my best; if I'm not sure, I'll say so instead of guessing."],
        "roman": ["Main {name} hoon, Talashly ka AI assistant. Main AI hoon, insan nahi. Kuch bhi poochein, main apni poori koshish karunga; agar yaqeen na ho to andaza lagane ki bajaye saaf bata dunga."],
        "ur": ["میں {name} ہوں، Talashly کا AI اسسٹنٹ۔ میں AI ہوں، انسان نہیں۔ کچھ بھی پوچھیں، میں پوری کوشش کروں گا؛ یقین نہ ہو تو اندازہ لگانے کی بجائے صاف بتا دوں گا۔"],
    },
    "help": {
        "en": ["I can answer questions in many languages, explain topics, and help with code. Pick the Deep model if you want in-depth research with its sources. Just type your question."],
        "roman": ["Main kai zabanon mein sawalon ke jawab de sakta hoon, topics samjha sakta hoon aur code mein madad kar sakta hoon. Gehri research aur uske sources ke liye Deep model chunein. Bas sawal likhein."],
        "ur": ["میں کئی زبانوں میں سوالوں کے جواب دے سکتا ہوں، موضوعات سمجھا سکتا ہوں اور کوڈ میں مدد کر سکتا ہوں۔ گہری تحقیق اور اس کے ذرائع کے لیے Deep ماڈل چنیں۔ بس سوال لکھیں۔"],
    },
}


_CALC_OK = re.compile(r"^[\d\s+\-*/().%^x×÷]+$")


def calc(text: str) -> str | None:
    """Saaf arithmetic ("2+2*5 kitna hota hai") - sirf ginti/operators, eval kabhi nahi (ast se)."""
    import ast, operator as op
    t = re.sub(r"\b(kitna|kitne|kya|hota|hai|hoga|equals?|is|what|whats|calculate|solve|answer|tell me|batao|\?|=)\b|[?=]", " ", text.lower())
    t = t.replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", "")
    t = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", t).strip()
    if re.fullmatch(r"\d{3,}-\d{2,}(-\d+)?|\d{1,4}/\d{1,2}/\d{1,4}", t):  # phone/date, hisaab nahi
        return None
    if not t or not _CALC_OK.match(t.replace("*", "x")) or not re.search(r"\d\s*[-+*/%x]|\*\*", t) or len(t) > 80:
        return None
    ops = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.Mod: op.mod, ast.Pow: op.pow,
           ast.USub: op.neg, ast.UAdd: op.pos}

    def ev(n, d=0):
        if d > 12:
            raise ValueError
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            a, b = ev(n.left, d + 1), ev(n.right, d + 1)
            if isinstance(n.op, ast.Pow) and (abs(b) > 100 or abs(a) > 1e6):
                raise ValueError
            return ops[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in ops:
            return ops[type(n.op)](ev(n.operand, d + 1))
        raise ValueError
    try:
        v = ev(ast.parse(t, mode="eval").body)
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, RecursionError):
        return None
    if isinstance(v, float):
        v = round(v, 10)
        v = int(v) if v == int(v) and abs(v) < 1e15 else v
    return f"{re.sub(r'\\s+', ' ', t.replace('**', '^'))} = {v}"


def smalltalk(text: str, model: Model) -> str | None:
    """Sirf choti, saaf baat-cheet (greeting/thanks...) par jawab; asli sawal ko kabhi nahi pakarta."""
    t = re.sub(r"[!?.,؟،۔:;~\"'`]+$", "", text.strip().lower())
    t = re.sub(r"\s+", " ", re.sub(r"[!?.,؟،۔]+", " ", t)).strip()
    c = calc(text)
    if c:
        return c
    if not t or len(t) > 90 or len(t.split()) > 12:
        return None
    for kind, rx in PATTERNS.items():
        if rx.match(t):
            lang = reply_lang(text)
            opts = REPLIES[kind].get(lang) or REPLIES[kind]["en"]
            pick = opts[sum(map(ord, t)) % len(opts)]  # deterministic: same input -> same reply
            return pick.replace("{name}", model.name)
    return None


# ------------------------------------------------------------------ follow-up sawal
FOLLOW = re.compile(
    r"(?<!\w)(it|its|this|that|they|them|their|those|these|he|she|his|her|there|"
    r"iska|iski|iske|isko|is ka|is ki|is ke|uska|uski|uske|usko|us ka|us ki|us ke|wo|woh|ye|yeh|inka|unka|"
    r"اس|اسکا|اسکی|اس کا|اس کی|اس کے|یہ|وہ|ان کا|ان کی|"
    r"what about|how about|and what|and how|and why|why|kyun|kyon|kiun|aur|phir|more|details?|explain|elaborate|"
    r"example|examples|tafseel|mazeed)(?!\w)", re.I)


def contextualize(q: str, prev_user: str | None) -> str:
    """Chhota follow-up ("iska founder kaun hai?") ko pichhle sawal se jod kar poora sawal bana do.
    Naya, alag topic wala sawal (koi pronoun/follow-up lafz nahi) kabhi nahi jorta."""
    if not prev_user:
        return q
    if len(terms(q)) > 7 or not FOLLOW.search(q):
        return q
    prev = re.sub(r"\s+", " ", prev_user).strip()[:200]
    return f"{prev} {q}"[:500]


def format_with_sources(text: str, sources: list[dict]) -> str:
    if not sources:
        return text
    lines = [f"[{s['n']}] {s.get('title') or s['domain']} - {s['url']}" for s in sources]
    return f"{text}\n\nSources:\n" + "\n".join(lines)


SYSTEM_PROMPT = (
    "You are Talashly's assistant (model MUS 7.01): warm, direct and expert, like a professional human colleague. "
    "First work out what the user actually wants and what a great answer looks like; if truly ambiguous ask ONE short question. "
    "Reply in the user's language (English by default). Be concise but complete. Do the task instead of explaining why you might not; "
    "never say 'I cannot' for ordinary requests. Decline only clearly harmful requests (weapons, malware, self-harm methods, sexual "
    "content involving minors, hate, fraud) briefly, and offer a safe alternative. Never mention sources, searching, an index, or your "
    "training data unless asked. For websites output one complete single-file HTML page using Tailwind CSS via CDN, responsive, in a "
    "```html block. Do not claim to be human. /no_think"
)


def qkey(text: str) -> str:
    """Sawal ki pehchaan: chhote huroof, sirf alfaaz (spaces/punctuation ka farq nahi)."""
    return " ".join(re.findall(r"\w+", text.lower()))[:200]


LEARN_TTL_DAYS = 60      # purane yaad kiye jawab 60 din baad khud hat jate hain (knowledge decay)
CACHE_TTL_S = 900        # tez jawab: wahi sawal 15 minute tak memory se
CACHE_MAX = 300
LEARN_MIN_VOTERS = 3  # itne alag log 👍 den tab hi jawab "seekha hua" mana jata hai (ek akela bigaar na sake)


class Chat:
    def __init__(self, cfg: dict, db: DB, brain: Brain, searcher: Searcher):
        self.cfg, self.db, self.brain, self.searcher = cfg, db, brain, searcher
        self.c = cfg["chat"]
        self._sem: asyncio.Semaphore | None = None
        self.waiting = 0
        self.active = 0
        self._cache: OrderedDict[tuple, tuple[float, str, str]] = OrderedDict()

    # ---------------------------------------------------------------- storage
    def new_conv(self, owner: str, model: str, title: str = "") -> str:
        cid = uuid.uuid4().hex
        now = time.time()
        self.db.x("INSERT INTO conversations(id,owner,title,model,created,updated) VALUES(?,?,?,?,?,?)",
                  (cid, owner, title[:80], model, now, now))
        old = self.db.q("SELECT id FROM conversations WHERE owner=? ORDER BY updated DESC LIMIT -1 OFFSET ?",
                        (owner, int(self.c["max_convs_per_owner"])))
        for r in old:
            self.db.x("DELETE FROM conversations WHERE id=?", (r["id"],))
        return cid

    def get_conv(self, owner: str, cid: str) -> dict | None:
        r = self.db.q("SELECT * FROM conversations WHERE id=? AND owner=?", (cid, owner))
        return dict(r[0]) if r else None

    def list_convs(self, owner: str, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self.db.q(
            "SELECT id,title,model,created,updated FROM conversations WHERE owner=? ORDER BY updated DESC LIMIT ?",
            (owner, limit))]

    def messages(self, cid: str, limit: int = 400) -> list[dict]:
        out = []
        for r in self.db.q("SELECT role,content,meta,created FROM messages WHERE conv_id=? ORDER BY id LIMIT ?", (cid, limit)):
            d = {"role": r["role"], "content": r["content"], "created": r["created"]}
            try:
                d["meta"] = json.loads(r["meta"]) if r["meta"] else {}
            except ValueError:
                d["meta"] = {}
            out.append(d)
        return out

    def delete_conv(self, owner: str, cid: str) -> bool:
        return self.db.x("DELETE FROM conversations WHERE id=? AND owner=?", (cid, owner)).rowcount > 0

    def _add(self, cid: str, role: str, content: str, meta: dict | None = None) -> None:
        self.db.x("INSERT INTO messages(conv_id,role,content,meta,created) VALUES(?,?,?,?,?)",
                  (cid, role, content, json.dumps(meta or {}, ensure_ascii=False), time.time()))
        self.db.x("UPDATE conversations SET updated=? WHERE id=?", (time.time(), cid))

    # ---------------------------------------------------------------- concurrency
    async def _acquire(self) -> bool:
        if self._sem is None:
            self._sem = asyncio.Semaphore(int(self.c["max_concurrent"]))
        if self._sem.locked() and self.waiting >= int(self.c["queue_max"]):
            return False
        self.waiting += 1
        try:
            await self._sem.acquire()
        finally:
            self.waiting -= 1
        self.active += 1
        return True

    def _release(self) -> None:
        self.active -= 1
        assert self._sem is not None
        self._sem.release()

    # ---------------------------------------------------------------- main
    async def stream(self, owner: str, role: str, text: str, conv_id: str | None = None, model_id: str | None = None,
                     history_user: str | None = None, persist: bool = True,
                     history: list[dict] | None = None) -> AsyncIterator[tuple[str, Any]]:
        t0 = time.time()
        text = _CTRL.sub("", text or "").strip()
        if not text:
            yield "error", {"code": "empty", "message": tr("empty", text)}
            return
        if len(text) > int(self.c["max_message_chars"]):
            yield "error", {"code": "too_long", "message": tr("too_long", text, n=self.c["max_message_chars"])}
            return
        model = get_model(model_id)
        assert model is not None

        new_conv = False
        prev_user = history_user
        if not prev_user and history:
            prev_user = next((h["content"] for h in reversed(history) if h.get("role") == "user"), None)
        if persist:
            if conv_id:
                conv = self.get_conv(owner, conv_id)
                if not conv:
                    yield "error", {"code": "not_found", "message": tr("not_found", text)}
                    return
                n = self.db.q("SELECT COUNT(*) FROM messages WHERE conv_id=?", (conv_id,))[0][0]
                if n >= int(self.c["max_msgs_per_conv"]):
                    yield "error", {"code": "conv_full", "message": tr("conv_full", text)}
                    return
                last = self.db.q("SELECT content FROM messages WHERE conv_id=? AND role='user' ORDER BY id DESC LIMIT 1", (conv_id,))
                prev_user = last[0]["content"] if last else None
            else:
                new_conv = True  # asli conversation lock milne ke baad banti hai (busy hone par khali chat na bane)

        if not await self._acquire():
            yield "error", {"code": "busy", "message": tr("busy", text)}
            return
        buf = ""
        sources: list[dict] = []
        done: dict = {}
        saved = False
        try:
            if persist:
                if new_conv:
                    conv_id = self.new_conv(owner, model.id)
                self._add(conv_id, "user", text)  # type: ignore[arg-type]
                yield "meta", {"conversation_id": conv_id, "model": model.id}
                if new_conv:
                    title = re.sub(r"\s+", " ", text)[:60]
                    self.db.x("UPDATE conversations SET title=? WHERE id=?", (title, conv_id))
                    yield "title", {"title": title}
            else:
                yield "meta", {"model": model.id}

            show = wants_sources(text, model)
            talk = smalltalk(text, model)
            if talk is None and text.strip().lower() in ("/good", "/bad"):
                q_a = None
                if history:
                    us = [h for h in history if h.get("role") == "user"]
                    asst = [h for h in history if h.get("role") == "assistant"]
                    if us and asst:
                        q_a = (us[-1]["content"], asst[-1]["content"])
                elif persist and conv_id:
                    rows = self.db.q("SELECT role, content FROM messages WHERE conv_id=? ORDER BY id DESC LIMIT 4", (conv_id,))
                    a_ = next((r["content"] for r in rows if r["role"] == "assistant"), None)
                    u_ = next((r["content"] for r in rows if r["role"] == "user" and not r["content"].startswith("/")), None)
                    if a_ and u_:
                        q_a = (u_, a_)
                if q_a:
                    self.feedback(owner, q_a[0], q_a[1], "up" if text.strip().lower() == "/good" else "down")
                    talk = tr("fb_ok", text)
                else:
                    talk = tr("fb_none", text)
            verdict = None if talk is not None else policy_check(text)
            if talk is None and verdict is None and is_unclear(text):
                verdict = "clarify"
            if talk is not None or verdict is not None:
                talk = talk if talk is not None else tr(verdict, text)  # type: ignore[arg-type]
                buf = talk
                yield "token", talk
                done = {"mode": "chat" if verdict is None else verdict, "model": model.id}
            else:
                smart_done = None
                async for ev, data in self._smart(text, history, prev_user, model):
                    if ev == "done":
                        smart_done = data
                        continue
                    if ev == "token":
                        buf += data
                    yield ev, data
                if smart_done is not None:
                    done = dict(smart_done)
                    done["model"] = model.id
                else:
                    eq = contextualize(text, prev_user)
                    ckey = (model.id, qkey(text))
                    cached = None if (show or eq != text) else self._cache_get(ckey)
                    if cached:
                        buf = cached[0]
                        yield "token", buf
                        done = {"mode": cached[1], "model": model.id, "cached": True}
                    else:
                        if show and eq != text:
                            yield "rewrite", {"query": eq}
                        bq = eq
                        if model.code_first and not re.match(r"\s*code:", eq, re.I) and self.searcher.search_code(eq, None, 1):
                            bq = "code: " + eq
                        try:
                            async for ev, data in self.brain.run(
                                    bq, role, deep=model.deep, live=model.live, sentences=model.sentences,
                                    nsources=model.sources, weak_cov=model.weak_cov, stream_delay=0.004):
                                if ev == "done":
                                    done = dict(data)
                                    done["model"] = model.id
                                    continue  # done ham aakhir mein khud bhejte hain
                                if not show:
                                    if ev in ("plan", "step", "sources"):
                                        continue  # process/sources default mein nahi dikhte
                                    if ev in ("token", "replace"):
                                        data = strip_cites(data)
                                if ev == "token":
                                    buf += data
                                elif ev == "replace":
                                    buf = data
                                elif ev == "sources":
                                    sources = data
                                yield ev, data
                            if not show and strip_cites(buf) != buf:  # tukron mein bate marker ka bachav
                                buf = strip_cites(buf)
                                yield "replace", buf
                        except Exception as e:  # noqa: BLE001  koi bug ho to bhi user ko saaf jawab; log mein record
                            self.db.log("error", f"chat failed {type(e).__name__}: {e}")
                            msg = tr("error", text)
                            buf = (buf + "\n\n" if buf else "") + msg
                            yield "token", msg
                            done = {"mode": "error", "model": model.id}
                        if not show and eq == text and done.get("mode") in ("extractive", "llm") and buf and len(buf) < 4000:
                            self._cache_put(ckey, buf, done["mode"])
            done["sources_shown"] = bool(show and sources)
            done["took_ms"] = round((time.time() - t0) * 1000)
            if persist:
                self._add(conv_id, "assistant", buf, {"sources": sources, "done": done, "model": model.id})  # type: ignore[arg-type]
                saved = True
                done["conversation_id"] = conv_id
            yield "done", done
        finally:
            if persist and not saved and buf:  # client beech mein chhor gaya: jo bana wo mehfooz
                try:
                    self._add(conv_id, "assistant", buf, {"sources": sources, "done": {"mode": "interrupted"}})  # type: ignore[arg-type]
                except Exception:  # noqa: BLE001
                    pass
            self._release()

    def _cache_get(self, key: tuple) -> tuple[str, str] | None:
        e = self._cache.get(key)
        if not e:
            return None
        if time.time() - e[0] > CACHE_TTL_S:
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return e[1], e[2]

    def _cache_put(self, key: tuple, text: str, mode: str) -> None:
        self._cache[key] = (time.time(), text, mode)
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_MAX:
            self._cache.popitem(last=False)

    def learned_answer(self, text: str) -> str | None:
        """Users ke 👍 se seekha jawab. Bilkul wahi sawal, ya 80%+ milte-julte alfaaz (Hinglish/ordering ka farq)."""
        k = qkey(text)
        if len(k) < 4:
            return None
        cutoff = time.time() - LEARN_TTL_DAYS * 86400
        r = self.db.q("SELECT qkey, a FROM learned WHERE qkey=? AND ts>?", (k, cutoff))
        if not r:
            words = set(k.split())
            if len(words) < 3:
                return None
            best = None
            for row in self.db.q("SELECT qkey, a FROM learned WHERE ts>? ORDER BY ts DESC LIMIT 300", (cutoff,)):
                w2 = set(row["qkey"].split())
                if w2 and len(words & w2) / len(words | w2) >= 0.8:
                    best = row
                    break
            if best is None:
                return None
            r = [best]
        v = self.db.q("SELECT SUM(vote=1) up, SUM(vote=-1) down FROM learned_votes WHERE qkey=?", (r[0]["qkey"],))[0]
        up, down = int(v["up"] or 0), int(v["down"] or 0)
        return r[0]["a"] if up >= LEARN_MIN_VOTERS and up > 2 * down else None

    def feedback(self, owner: str, question: str, answer: str, vote: str) -> str:
        """👍/👎: sirf user ke click par, bina pehchaan ke. 👍 jawab yaad karta hai, 👎 sawal ko dobara research ke liye gap banata hai."""
        k = qkey(question)
        if len(k) < 4:
            return "ignored"
        self.db.x("DELETE FROM learned WHERE ts<?", (time.time() - LEARN_TTL_DAYS * 86400,))
        voter = uuid.uuid5(uuid.NAMESPACE_URL, owner).hex
        if vote == "up":
            a = answer.strip()
            if not (20 <= len(a) <= 3000) or "http://" in a or "https://" in a:
                return "ignored"
            self.db.x("INSERT INTO learned(qkey,q,a,ts) VALUES(?,?,?,?) ON CONFLICT(qkey) DO UPDATE SET a=excluded.a, ts=excluded.ts",
                      (k, question.strip()[:300], a, time.time()))
            self.db.x("INSERT OR REPLACE INTO learned_votes(qkey,voter,vote) VALUES(?,?,1)", (k, voter))
            return "saved"
        self.db.x("INSERT OR REPLACE INTO learned_votes(qkey,voter,vote) VALUES(?,?,-1)", (k, voter))
        self.brain.record_gap(question.strip()[:300])
        return "noted"

    async def _smart(self, text: str, history: list[dict] | None, prev_user: str | None, model: Model) -> AsyncIterator[tuple[str, Any]]:
        """Insani dimaag: pehle samjho user kya chahta hai. Website/kaam ke sawal seedha banao; AI writer (LLM) on ho aur
        index kamzor ho to usi se jawab. Kuch na bane to kuch yield nahi karta aur normal (index wala) rasta chalta hai."""
        if not model.deep:
            got = self.learned_answer(text)
            if got:  # users ke 👍 se seekha hua jawab: milliseconds mein
                yield "token", got
                yield "done", {"mode": "learned"}
                return
        llm = self.cfg["llm"]
        ready = llm_available(llm)
        site = wants_site(text)
        if site and not ready:
            yield "token", build_site(text)
            yield "done", {"mode": "builder"}
            return
        if model.deep:
            return  # Deep: hamesha research + sources
        if not ready:
            for fn in (lambda: poem(text), lambda: compare(text, self.searcher)):
                got = fn()
                if got:
                    yield "token", got
                    yield "done", {"mode": "skill"}
                    return
        eq = contextualize(text, prev_user)
        try:
            weak = coverage(eq, self.searcher.search_many([eq], 8)) < 0.6
        except Exception:  # noqa: BLE001  search gira: normal rasta apna error-jawab dega
            return
        if not ready:
            if CREATIVE.search(text) and weak:
                yield "token", tr("needllm", text)
                yield "done", {"mode": "needllm"}
            return
        if not (site or intent(text) == "task" or weak):
            return
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        for h in (history or [])[-6:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                msgs.append({"role": h["role"], "content": str(h["content"])[:2000]})
        msgs.append({"role": "user", "content": text})
        got = False
        try:
            async for tok in stream_chat(llm, msgs):
                got = True
                yield "token", tok
            if got:
                yield "done", {"mode": "llm-direct"}
                return
        except Exception as e:  # noqa: BLE001
            self.db.log("heal", f"llm direct failed {type(e).__name__}: {e}")
            if got:
                yield "token", "\n\n" + tr("error", text)
                yield "done", {"mode": "error"}
                return
        if site:  # LLM gira: builder se phir bhi kaam ho jaye
            yield "token", build_site(text)
            yield "done", {"mode": "builder"}

    async def complete(self, role: str, text: str, model_id: str | None, history_user: str | None) -> dict:
        """Stateless poora jawab (OpenAI-compatible endpoint ke liye)."""
        out, sources, done, err = "", [], {}, None
        async for ev, data in self.stream("api", role, text, None, model_id, history_user, persist=False):
            if ev == "token":
                out += data
            elif ev == "replace":
                out = data
            elif ev == "sources":
                sources = data
            elif ev == "done":
                done = data
            elif ev == "error":
                err = data
        return {"text": out, "sources": sources, "done": done, "error": err}
