"""LLM ke bina chalne wali chhoti skills: compare (index se, har baat asli text se) aur poem/haiku (templates)."""
from __future__ import annotations

import re

from .search import build_answer

CMP = re.compile(
    r"^\s*(?:please |pls |can you |could you )?(?:compare|difference between|differences between|contrast)\s+(.+?)\s+(?:and|vs\.?|versus|with|aur|و)\s+(.+?)\s*[?.!]*$"
    r"|^\s*(.+?)\s+(?:vs\.?|versus)\s+(.+?)\s*[?.!]*$"
    r"|^\s*(.+?)\s+(?:aur|and)\s+(.+?)\s+(?:mein|me|main)\s+(?:kya\s+)?(?:farq|fark|difference)(?:\s+hai)?\s*[?.!]*$", re.I)
POEM = re.compile(r"\b(poem|poetry|nazm|shayari|ghazal|haiku|kavita)\b", re.I)
ON = re.compile(r"\b(?:about|on|for|par|pe|ke bare mein|per)\s+(.+?)\s*[?.!]*$|^(?:.*?)\b(?:poem|nazm|shayari|haiku)\s+(?:of|on)?\s*(.+?)\s*[?.!]*$", re.I)


def compare_parts(text: str) -> tuple[str, str] | None:
    m = CMP.match(text)
    if not m:
        return None
    g = [x for x in m.groups() if x]
    a, b = (g[0].strip(" \"'"), g[1].strip(" \"'")) if len(g) >= 2 else (None, None)
    if not a or not b or len(a) > 60 or len(b) > 60 or a.lower() == b.lower():
        return None
    return a, b


def compare(text: str, searcher) -> str | None:
    """Do cheezon ka muqabla: har ek ke liye index se asli jumle. Kisi ka data na ho to saaf batata hai."""
    ab = compare_parts(text)
    if not ab:
        return None
    rows, missing = [], []
    for x in ab:
        try:
            hits = searcher.search_many([x], 8)
            ans = build_answer(x, hits, max_sentences=2)
        except Exception:  # noqa: BLE001
            ans = {"answer": ""}
        a = re.sub(r"\s*\[\d+\]", "", ans.get("answer", "")).strip()
        (rows if a else missing).append((x, a))
    if not rows:
        return None
    out = [f"{ab[0]} vs {ab[1]}:\n"]
    for x, a in rows:
        out.append(f"- **{x}**: {a}")
    if missing:
        out.append("\n" + ", ".join(f"I don't have enough information about {x} yet" for x, _ in missing) + ".")
    return "\n".join(out)


def _topic(text: str) -> str:
    m = ON.search(text)
    t = next((g for g in (m.groups() if m else ()) if g), "") or "life"
    t = re.sub(r"^(?:about|on|of|for|par|pe|per)\s+", "", t.strip(), flags=re.I)
    t = re.sub(r"\b(a|an|the|short|small|beautiful|nice|please|pls|likho|likh|do|dena)\b", " ", t, flags=re.I)
    t = re.sub(r"[^\w\s؀-ۿऀ-ॿ-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()[:40]
    return t or "life"


def poem(text: str) -> str | None:
    if not POEM.search(text) or not re.match(r"^\s*(?:please |pls |can you |could you |kindly |)?(?:write|compose|make|create|give me|likho|likh|ek|a)\b", text, re.I):
        return None
    t = _topic(text)
    if re.search(r"haiku", text, re.I):
        return f"{t.capitalize()} in quiet light,\nsoft words rise and drift away,\nthe heart finds its way."
    return (f"**{t.capitalize()}**\n\n"
            f"In every quiet corner, {t} appears,\nA gentle thought that softens all our fears.\n\n"
            f"Through morning light and evening's fading glow,\n{t.capitalize()} reminds us where the good things grow.\n\n"
            f"So hold it close and let your spirit sing,\nFor {t} is a small and lasting spring.")
