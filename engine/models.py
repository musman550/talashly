"""Talashly ke jawab dene wale models (profiles). Sab free hain, koi limit-per-model nahi.

HONEST NOTE: ye "answer engine" profiles hain jo ek hi apne index par alag tareeqe se kaam karte hain
(tez / gehra / code / multilingual). Ye alag-alag trained neural networks nahi hain. Agar aap apna local LLM
(llama.cpp) chalate hain to wo har profile ke jawab ko likhne mein madad karta hai, lekin har jumla phir bhi
sources se verify hota hai.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    tagline: str
    description: str
    deep: bool = False
    live: bool = True            # index kamzor ho to live sources se la sakta hai
    sentences: int = 5           # jawab mein zyada se zyada jumle
    sources: int = 6             # kitne sources dikhane hain
    weak_cov: float | None = None  # is se kam coverage par live research chalta hai (None = default)
    code_first: bool = False     # pehle apna code index dekho
    context: str = "balanced"

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "tagline": self.tagline, "description": self.description,
                "free": True, "deep": self.deep, "live_research": self.live, "max_sentences": self.sentences,
                "max_sources": self.sources, "code_first": self.code_first}


MODELS: dict[str, Model] = {m.id: m for m in (
    Model("mus-7.01", "MUS 7.01", "Balanced - everyday questions",
          "Default model. Searches its own index first, then live sources if the answer is weak. Up to 5 sentences, 6 citations."),
    Model("mus-7.01-flash", "MUS 7.01 Flash", "Fastest",
          "Index only (no live fetching), up to 3 sentences, 4 citations. Answers in milliseconds.",
          live=False, sentences=3, sources=4),
    Model("mus-7.01-deep", "MUS 7.01 Deep", "Deep research",
          "Splits the question into parts, reads more sources, up to 8 sentences and 10 citations.",
          deep=True, sentences=8, sources=10, context="deep"),
    Model("mus-7.01-code", "MUS 7.01 Code", "Coding help",
          "Searches code you indexed first (functions, classes, identifiers), then documentation.",
          code_first=True, sentences=6, sources=6, context="code"),
    Model("mus-7.01-polyglot", "MUS 7.01 Polyglot", "Any language",
          "Urdu, Arabic, Hindi, Chinese, Russian and more. Fetches live pages in the question's language sooner when the index is thin.",
          weak_cov=0.9, sentences=5, sources=8, context="multilingual"),
)}
DEFAULT_MODEL = "mus-7.01"


def get_model(mid: str | None, strict: bool = False) -> Model | None:
    """strict=False: ghalat/khali naam par default model; strict=True: ghalat naam par None (API ke liye)."""
    if not mid:
        return MODELS[DEFAULT_MODEL]
    m = MODELS.get(mid.strip().lower())
    if m is None and not strict:
        return MODELS[DEFAULT_MODEL]
    return m
