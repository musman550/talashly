"""Chhote, sahi hints jo model ko galat library/zaban se bachate hain (model 1.7B/4B hai, isay yaad dilana parta hai)."""
from __future__ import annotations

import re

from .lang import reply_lang

LIBS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"you ?tube|\byt\b", re.I), "YouTube download: use the real library yt-dlp (pip install yt-dlp), never pytube or a library named 'py'."),
    (re.compile(r"video|reel|shorts?|clip|ffmpeg", re.I), "Video editing: call ffmpeg via subprocess (ffmpeg binary from imageio-ffmpeg) or use moviepy; never invent library names."),
    (re.compile(r"scrap|crawl", re.I), "Scraping: requests + beautifulsoup4 (bs4); respect robots.txt and add a delay."),
    (re.compile(r"telegram", re.I), "Telegram bot: python-telegram-bot (v20+, async) or aiogram."),
    (re.compile(r"discord", re.I), "Discord bot: discord.py."),
    (re.compile(r"\bexcel\b|xlsx", re.I), "Excel: openpyxl or pandas."),
    (re.compile(r"\bpdf\b", re.I), "PDF: pypdf for reading, reportlab for creating."),
    (re.compile(r"\bapi\b|server|backend", re.I), "Web API in Python: FastAPI or Flask."),
]


def lib_hints(text: str) -> str:
    out = [h for rx, h in LIBS if rx.search(text)][:3]
    return (" Library facts (follow them): " + " ".join(out)) if out else ""


_FR_ES_DE = re.compile(r"\b(le|la|les|des|une|est|pour|dans|el|los|las|una|para|con|der|die|das|und|nicht|ein|eine|mit|ist)\b", re.I)


def lang_note(text: str) -> str:
    lang = reply_lang(text)
    nonascii = sum(ord(c) > 127 for c in text)
    if lang == "roman":
        return (" The user writes Roman Urdu (Urdu in Latin letters, mixed with English words). Understand it as Urdu, "
                "reply in simple Roman Urdu, keep technical terms and code in English. If you truly cannot understand, ask one short question in Roman Urdu; never invent an answer.")
    if lang == "ur":
        return " The user writes Urdu: reply in simple Urdu script, keep code and technical terms in English."
    if nonascii > max(3, len(text) * .25) or len(_FR_ES_DE.findall(text)) >= 3:
        return " Reply in exactly the same language and script as the user's message, even for technical terms; never switch to English."
    return ""


def extra(text: str) -> str:
    """System prompt ke aakhir mein jodne ka tukra."""
    return lang_note(text) + lib_hints(text)
