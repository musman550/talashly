"""Chat layer: conversations, follow-up sawal samajhna, insani lehja mein small talk, aur concurrency control.

Usool: jawab sirf sources se. Source na ho to saaf kehna "nahi mila" - kabhi andaza nahi lagana.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any, AsyncIterator

from .brain import Brain
from .db import DB
from .lang import reply_lang, tr
from .models import Model, get_model
from .search import Searcher, terms

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

# ------------------------------------------------------------------ small talk (insani baat-cheet)
PATTERNS: dict[str, re.Pattern] = {
    "greet": re.compile(r"^(hi+|hello+|hey+|hiya|yo|salam|salaam|slm|aoa|assalam\w*( o)?( alaikum| alaykum)?|"
                        r"good (morning|afternoon|evening)|namaste|hola|bonjour|hallo|ciao|"
                        r"سلام|السلام علیکم|السلام عليكم|ہیلو|ہائے|नमस्ते|नमस्कार|你好|您好|привет|здравствуйте|こんにちは)"
                        r"( there| talashly| mus)?$"),
    "thanks": re.compile(r"^(thanks?|thank you( so much)?|thx|ty|shukriya|shukria|jazak\w*|mehrbani|"
                         r"شکریہ|شکراً|شكرا|धन्यवाद|शुक्रिया|谢谢|спасибо|gracias|merci|danke)( a lot| bohat| bahut)?$"),
    "bye": re.compile(r"^(bye+|goodbye|good night|see you|khuda hafiz|allah hafiz|khudahafiz|allahhafiz|"
                      r"اللہ حافظ|خدا حافظ|الوداع|अलविदा|再见|пока|adios|au revoir)$"),
    "how": re.compile(r"^(how are you|how r u|how are u|kaise ho|kese ho|kaisa hai|kaise hain aap|aap kaise hain|"
                      r"aap kese hain|kya haal hai|kia haal hai|kya hal hai|kaisay ho|کیسے ہو|آپ کیسے ہیں|کیا حال ہے|"
                      r"आप कैसे हैं|कैसे हो)( aap| tum)?$"),
    "who": re.compile(r"^(who are you|what are you|who r u|are you (a )?(human|real|person|bot|ai)|"
                      r"tum kaun ho|aap kaun hain|aap kon hain|tum kon ho|tum kya ho|aap kya hain|apna naam batao|"
                      r"aap ka naam kya hai|tumhara naam kya hai|your name|what is your name|what'?s your name|"
                      r"which model are you|what model are you|kya aap insan hain|kya tum insan ho|"
                      r"آپ کون ہیں|تم کون ہو|آپ کا نام کیا ہے|आप कौन हैं|तुम कौन हो)$"),
    "help": re.compile(r"^(help|help me|what can you do|what do you do|how do (i|you) use (you|this)|features|"
                       r"madad|madad karo|kya kar sakte ho|kya kar sakte hain|tum kya kar sakte ho|aap kya kar sakte hain|"
                       r"مدد|آپ کیا کر سکتے ہیں|आप क्या कर सकते हैं)$"),
}

REPLIES: dict[str, dict[str, list[str]]] = {
    "greet": {
        "en": ["Hello! Ask me anything - I'll answer from sources I've actually crawled, with citations.",
               "Hi there! What would you like to find out today?"],
        "roman": ["Assalam o alaikum! Kuch bhi poochein - main apne crawl kiye hue sources se citations ke saath jawab dunga.",
                  "Salam! Aaj kya jaanna chahte hain?"],
        "ur": ["السلام علیکم! جو چاہیں پوچھیں، میں اپنے جمع کیے ہوئے ذرائع سے حوالوں کے ساتھ جواب دوں گا۔"],
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
        "en": ["I'm {name}, the answer engine of Talashly. I'm an AI, not a human. I search an index of pages Talashly has "
               "crawled itself and answer with citations; if the sources don't contain the answer, I say so instead of guessing."],
        "roman": ["Main {name} hoon, Talashly ka answer engine. Main AI hoon, insan nahi. Main Talashly ke apne crawl kiye "
                  "hue pages ke index se citations ke saath jawab deta hoon; sources mein jawab na ho to andaza lagane ki "
                  "bajaye saaf bata deta hoon."],
        "ur": ["میں {name} ہوں، Talashly کا انسر انجن۔ میں AI ہوں، انسان نہیں۔ میں Talashly کے اپنے جمع کیے ہوئے "
               "صفحات سے حوالوں کے ساتھ جواب دیتا ہوں؛ ذرائع میں جواب نہ ہو تو اندازہ لگانے کی بجائے صاف بتا دیتا ہوں۔"],
    },
    "help": {
        "en": ["I can: answer questions with citations, research a topic in depth (try the Deep model), search your indexed "
               "code (Code model), and work in many languages. Just type a question."],
        "roman": ["Main ye kar sakta hoon: citations ke saath sawalon ke jawab, kisi topic par gehri research (Deep model), "
                  "aap ka index kiya hua code search (Code model), aur kai zabanon mein kaam. Bas sawal likhein."],
        "ur": ["میں حوالوں کے ساتھ سوالوں کے جواب دے سکتا ہوں، کسی موضوع پر گہری تحقیق (Deep ماڈل)، آپ کا انڈیکس شدہ کوڈ "
               "تلاش (Code ماڈل)، اور کئی زبانوں میں کام۔ بس سوال لکھیں۔"],
    },
}


def smalltalk(text: str, model: Model) -> str | None:
    """Sirf choti, saaf baat-cheet (greeting/thanks...) par jawab; asli sawal ko kabhi nahi pakarta."""
    t = re.sub(r"[!?.,؟،۔:;~\"'`]+$", "", text.strip().lower())
    t = re.sub(r"\s+", " ", re.sub(r"[!?.,؟،۔]+", " ", t)).strip()
    if not t or len(t) > 60 or len(t.split()) > 8:
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


class Chat:
    def __init__(self, cfg: dict, db: DB, brain: Brain, searcher: Searcher):
        self.cfg, self.db, self.brain, self.searcher = cfg, db, brain, searcher
        self.c = cfg["chat"]
        self._sem: asyncio.Semaphore | None = None
        self.waiting = 0
        self.active = 0

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
                     history_user: str | None = None, persist: bool = True) -> AsyncIterator[tuple[str, Any]]:
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

            talk = smalltalk(text, model)
            if talk is not None:
                buf = talk
                yield "token", talk
                done = {"mode": "chat", "model": model.id}
            else:
                eq = contextualize(text, prev_user)
                if eq != text:
                    yield "rewrite", {"query": eq}
                bq = eq
                if model.code_first and not re.match(r"\s*code:", eq, re.I) and self.searcher.search_code(eq, None, 1):
                    bq = "code: " + eq
                try:
                    async for ev, data in self.brain.run(
                            bq, role, deep=model.deep, live=model.live, sentences=model.sentences,
                            nsources=model.sources, weak_cov=model.weak_cov, stream_delay=0.004):
                        if ev == "token":
                            buf += data
                        elif ev == "replace":
                            buf = data
                        elif ev == "sources":
                            sources = data
                        elif ev == "done":
                            done = dict(data)
                            done["model"] = model.id
                            continue  # done ham aakhir mein khud bhejte hain
                        yield ev, data
                except Exception as e:  # noqa: BLE001  koi bug ho to bhi user ko saaf jawab; log mein record
                    self.db.log("error", f"chat failed {type(e).__name__}: {e}")
                    msg = tr("error", text)
                    buf = (buf + "\n\n" if buf else "") + msg
                    yield "token", msg
                    done = {"mode": "error", "model": model.id}
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
