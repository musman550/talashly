from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import trafilatura
from lxml import html as lxml_html

from .db import domain_of, normalize_url

_SKIP_EXT = re.compile(
    r"\.(jpg|jpeg|png|gif|webp|svg|ico|pdf|zip|gz|tar|mp3|mp4|avi|mov|css|js|json|xml|woff2?|ttf|exe|dmg|apk)$", re.I
)


@dataclass
class Extracted:
    title: str = ""
    author: str | None = None
    lang: str | None = None
    published_at: float | None = None
    text: str = ""
    links: list[str] = field(default_factory=list)


def _parse_date(s: str | None) -> float | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00")[:25])
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except ValueError:
        return None


def extract_page(html: str, url: str) -> Extracted | None:
    """Asli article text nikalo: menus, ads, footers, cookie banners hata kar."""
    # NOTE: trafilatura ka deduplicate=True process-wide cache use karta hai, jis se same page ka
    # text har baar alag nikalta hai aur change-detection toot jata hai. Isliye apna deterministic dedupe.
    text = trafilatura.extract(
        html, url=url, include_comments=False, include_tables=True, favor_precision=True,
    )
    if not text:
        return None
    seen: set[str] = set()
    paras: list[str] = []
    for p in text.split("\n"):
        key = re.sub(r"\W+", " ", p.lower()).strip()
        if key and key in seen:
            continue
        seen.add(key)
        paras.append(p)
    text = "\n".join(paras)
    meta = trafilatura.extract_metadata(html, default_url=url)
    ex = Extracted(text=text.strip())
    if meta:
        ex.title = (meta.title or "").strip()
        ex.author = meta.author
        ex.lang = getattr(meta, "language", None)
        ex.published_at = _parse_date(meta.date)
    if not ex.title:
        ex.title = text.strip().split("\n", 1)[0][:120]
    ex.links = extract_links(html, url)
    return ex


def extract_links(html: str, base_url: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return out
    for a in tree.iterfind(".//a[@href]"):
        if (a.get("rel") or "").lower().find("nofollow") >= 0:
            continue
        href = a.get("href", "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        full = normalize_url(urljoin(base_url, href))
        if not full.startswith("http") or _SKIP_EXT.search(urlsplit(full).path):
            continue
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out


_NOSPACE = re.compile(r"[\u0e00-\u0e7f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_SENT = re.compile(r"(?<=[.!?\u06d4\u0964])\s+|(?<=[\u3002\uff01\uff1f])\s*")


def weight(s: str) -> float:
    """Lamba-pan 'alfaaz' mein: spaces wali zabanon mein words, Chinese/Japanese/Thai mein ~0.6 word per character."""
    n = len(_NOSPACE.findall(s))
    return len(_NOSPACE.sub(" ", s).split()) + n * 0.6


def _hard_split(u: str, target: float) -> list[str]:
    """Bina punctuation wala bohat lamba tukra: zabardasti tukron mein todo."""
    if len(_NOSPACE.findall(u)) > len(u) * 0.3:
        step = max(20, int(target / 0.6))
        return [u[i:i + step] for i in range(0, len(u), step)]
    w = u.split()
    step = max(20, int(target))
    return [" ".join(w[i:i + step]) for i in range(0, len(w), step)]


def chunk_text(text: str, target_words: int = 180, overlap_words: int = 30) -> list[str]:
    """Paragraphs -> sentences -> chunks (~180 alfaaz). Har zaban/script ke liye durust, sentence-level overlap ke saath."""
    units: list[str] = []
    for para in (p.strip() for p in re.split(r"\n+", text)):
        if not para:
            continue
        if weight(para) <= target_words * 1.6:
            units.append(para)
            continue
        for sent in _SENT.split(para):
            sent = sent.strip()
            if not sent:
                continue
            if weight(sent) > target_words * 1.6:
                units.extend(x for x in _hard_split(sent, target_words) if x.strip())
            else:
                units.append(sent)
    chunks: list[str] = []
    cur: list[str] = []
    w = 0.0
    for u in units:
        uw = weight(u)
        if w + uw > target_words and cur:
            chunks.append(" ".join(cur))
            tail: list[str] = []
            tw = 0.0
            for x in reversed(cur):  # aakhri jumlay agle chunk mein dohrao (context na toote)
                xw = weight(x)
                if tw + xw > overlap_words:
                    break
                tail.insert(0, x)
                tw += xw
            cur, w = tail, tw
        cur.append(u)
        w += uw
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def same_site(url: str, allowed_domains: set[str]) -> bool:
    return domain_of(url) in allowed_domains
