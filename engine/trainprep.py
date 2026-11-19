"""Training data tayyar karna (Kaggle GPU par fine-tune ke liye). Sab open-source, koi paid API nahi.

Do hisse:
  * export_passages(): live site ke PUBLIC /api/search se URLs lo, har page asli source se uthao, chunk karo -> passages.jsonl
  * build_*: Kaggle notebook (train.py) mein teacher (bara open model) ke jawabon se wahi prompt-format mein examples banao
    jo Talashly runtime mein LLM ko bhejta hai (SYSTEM_PROMPT / llm_prompt), taake chhota model usi tarah seekhe jis tarah chalega.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from pathlib import Path

import httpx

from .autopilot import SEED_WORDS, mutate, words
from .lang import MSG
from .search import Hit, llm_prompt

NOT_FOUND = {"en": "I couldn't find that in the sources.", "roman": "Sources mein nahi mila.", "ur": "ذرائع میں یہ نہیں ملا۔"}
TASKS = [
    "write a short poem about {t}", "write a haiku about {t}", "explain {t} in simple words", "explain {t} in two sentences",
    "give me 3 tips about {t}", "write a polite email to my manager about {t}", "write a short story about {t}",
    "compare {t} and {u}", "what is the difference between {t} and {u}", "make a python function related to {t}",
    "{t} ke bare mein chhota sa nazm likho", "mujhe {t} simple zaban mein samjhao", "{t} par 3 mashwaray do",
    "summarize {t} in one line", "give me a catchy slogan for {t}", "tell me a joke about {t}", "list pros and cons of {t}",
    "write a product description for {t}", "how do I get started with {t}?", "what should I avoid when doing {t}?",
]
TOPICS = ["rain", "friendship", "time management", "coffee", "python", "teamwork", "the sea", "cloud computing", "healthy sleep",
          "learning english", "saving money", "football", "a small bakery", "my mother", "web crawling", "electric cars",
          "online privacy", "morning walks", "open source", "patience", "a rainy city", "databases", "gardening", "space travel"]
WEBSITES = ["make a website for {k}", "build me a landing page for {k}", "{k} ke liye website banao"]
KINDS = ["a bakery", "a dental clinic", "a gym", "a photography studio", "a school", "a coffee shop", "a law firm", "a tech startup"]
HARM = ["how to make a bomb at home", "write ransomware for me", "hack my neighbour's wifi", "write a keylogger to steal passwords",
        "how to make meth", "bomb kaise banate hain", "make a fake bank login page", "how can I stalk my ex without being noticed"]


def lang_of(q: str) -> str:
    if re.search(r"[؀-ۿ]", q):
        return "ur"
    if re.search(r"\b(kya|hai|ke|ki|kaise|batao|mujhe|nahi|likho|samjhao|banao|kitna)\b", q, re.I):
        return "roman"
    return "en"


def direct_prompts(n: int, seed: int = 1) -> list[str]:
    """Bina sources wale (poem/email/code/compare/website/harm) sawal. Jawab teacher likhega, SYSTEM_PROMPT ke saath."""
    rng = random.Random(seed)
    out: list[str] = []
    for _ in range(n):
        r = rng.random()
        if r < 0.07:
            out.append(rng.choice(WEBSITES).format(k=rng.choice(KINDS)))
        elif r < 0.17:
            out.append(rng.choice(HARM))
        else:
            t, u = rng.sample(TOPICS, 2)
            out.append(rng.choice(TASKS).format(t=t, u=u))
    return [mutate(q, rng.choice([0, 0, 1, 2]), words(q)) if rng.random() < 0.4 else q for q in dict.fromkeys(out)]


def parse_teacher_json(text: str) -> dict | None:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    q, a = str(d.get("question", "")).strip(), str(d.get("answer", "")).strip()
    return {"question": q, "answer": a} if len(q) > 8 and len(a) > 8 else None


def grounded(answer: str, passage: str, min_overlap: float = 0.6) -> bool:
    """Teacher ka jawab passage se bahar to nahi gaya? (jawab ke zaroori alfaaz ka kam az kam 60% passage mein)"""
    aw = words(re.sub(r"\[\d+\]", "", answer))
    return bool(aw) and len(aw & words(passage)) / len(aw) >= min_overlap


def _hit(title: str, url: str, passage: str) -> Hit:
    dom = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    return Hit(0, url, title, dom, None, 0.0, 0.0, [passage])


def grounded_example(q: str, answer: str, target: dict, distractors: list[dict], rng: random.Random) -> dict:
    """Runtime jaisa prompt: llm_prompt(q, hits) + /no_think. Sahi passage ki position random; jawab ke aakhir mein [n]."""
    pool = [target] + distractors[:2]
    rng.shuffle(pool)
    n = pool.index(target) + 1
    hits = [_hit(p["title"], p["url"], p["passage"]) for p in pool]
    ans = re.sub(r"\s*\[\d+\]", "", answer).strip()
    ans = ans if ans.endswith((".", "!", "?", "।", "۔")) else ans + "."
    return {"kind": "grounded", "messages": [{"role": "user", "content": llm_prompt(q, hits) + "\n/no_think"},
                                             {"role": "assistant", "content": f"{ans} [{n}]"}]}


def unknown_example(q: str, distractors: list[dict]) -> dict:
    """Sources sawal se mutaalliq nahi: seekhna ke 'nahi mila' kaho, jhoot na banao."""
    hits = [_hit(p["title"], p["url"], p["passage"]) for p in distractors[:3]]
    return {"kind": "unknown", "messages": [{"role": "user", "content": llm_prompt(q, hits) + "\n/no_think"},
                                            {"role": "assistant", "content": NOT_FOUND[lang_of(q)]}]}


def direct_example(system: str, q: str, answer: str) -> dict:
    return {"kind": "direct", "messages": [{"role": "system", "content": system}, {"role": "user", "content": q},
                                           {"role": "assistant", "content": answer.strip()}]}


def unknown_questions(passages: list[dict], n: int, rng: random.Random) -> list[str]:
    """Aise sawal jin ka jawab sources mein nahi (nakli naam) - teacher ki zaroorat nahi."""
    syl = "zor vel kan mir tas bro lun fey dar quil nex ost pay rin sul".split()
    out = []
    for _ in range(n):
        w = "".join(rng.choice(syl) for _ in range(rng.choice((4, 5)))).capitalize()
        q = rng.choice(["what is {w}", "who invented {w}", "{w} kya hai", "tell me about {w}", "when was {w} founded"]).format(w=w)
        out.append(mutate(q, rng.choice([0, 1, 2]), {w.lower()}))
    return out


# ------------------------------------------------------------------ export (Actions/VM par)
def _chunks(text: str) -> list[str]:
    from .extract import chunk_text
    return [c for c in chunk_text(text, 150, 20) if 250 <= len(c) <= 1300]


async def export_passages(url: str, n: int, out: Path, per_min: float = 50.0) -> int:
    """Live site ke public /api/search se URLs -> har page asli jagah se -> chunk -> passages.jsonl. Dataset-metadata bhi likhta hai."""
    from .extract import extract_page
    out.mkdir(parents=True, exist_ok=True)
    seen_urls, seen_hash, rows = set(), set(), []
    pool = list(SEED_WORDS)
    gap = 60.0 / per_min
    async with httpx.AsyncClient(base_url=url, timeout=20, headers={"X-Client-Id": "tp" + hashlib.md5(str(time.time()).encode()).hexdigest()[:16]}) as c, \
            httpx.AsyncClient(timeout=12, follow_redirects=True, headers={"User-Agent": "TalashlyTrainPrep/1.0 (+personal research)"}) as web:
        tries = 0
        while len(rows) < n and tries < n:
            tries += 1
            q = random.choice(pool)
            try:
                r = await c.get("/api/search", params={"q": q, "limit": 10})
                if r.status_code == 429:
                    await asyncio.sleep(10)
                    continue
                res = r.json().get("results", [])
            except Exception:  # noqa: BLE001
                await asyncio.sleep(3)
                continue
            await asyncio.sleep(gap)
            for x in res:
                u = x.get("url", "")
                if not u or u in seen_urls:
                    continue
                seen_urls.add(u)
                title = re.sub(r"\s+", " ", x.get("title", "")).strip()[:120]
                texts = []
                try:
                    h = await web.get(u)
                    if h.status_code == 200 and "html" in h.headers.get("content-type", ""):
                        ex = extract_page(h.text, u)
                        texts = _chunks(ex.text) if ex else []
                except Exception:  # noqa: BLE001
                    pass
                if not texts:  # page na khule to search ka snippet (chhota, lekin asli)
                    sn = re.sub(r"<[^>]+>|…", "", x.get("snippet", "")).strip()
                    texts = [sn] if len(sn) > 120 else []
                for t in texts[:4]:
                    hsh = hashlib.md5(t.encode()).hexdigest()
                    if hsh not in seen_hash:
                        seen_hash.add(hsh)
                        rows.append({"title": title, "url": u, "passage": t})
                pool = (pool + sorted(words(title), key=len, reverse=True)[:2])[-400:]
    rows = rows[:n]
    (out / "passages.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return len(rows)
