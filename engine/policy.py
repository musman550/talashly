"""Jawab dene ki policy: kab seedha baat-cheet, kab wazahat poochna, kab inkar, aur kab sources dikhana."""
from __future__ import annotations

import re

from .models import Model

CITE = re.compile(r"[ \t]?\[\d{1,2}\]")

# Sources sirf tab dikhte hain jab user khud mange ya Deep model chune
SRC_ASK = re.compile(
    r"(deep research|\bsources\b|\breferences\b|\bcitations?\b|\bcite\b|with (a )?sources?\b|"
    r"(show|give|list|add|share) (me )?(the |your |some )?sources?\b|sources? (do|dikhao|batao|bhi)\b|"
    r"where (did|do|does) (you|it) (get|find|take)|kahan se|kahaan se|kidhar se|"
    r"ماخذ|حوالہ|حوالے|ذرائع|स्रोत|来源|источник)", re.I)

HARM = re.compile(
    r"((how|ways?|steps?|guide|instructions?)( to| for)? (make|build|create|synthesi[sz]e|cook|produce|manufacture) "
    r"(a |an |some )?(pipe |nail |dirty |homemade |home made )?"
    r"(bomb|explosive|nerve agent|sarin|ricin|anthrax|napalm|thermite|methamphetamine|meth|fentanyl)(?!\w)(?!\s+calorimeter)|"
    r"(write|create|build|code|make|develop) (me )?(a |an |some )?(working |simple )?"
    r"(ransomware|malware|keylogger|botnet|ddos (tool|script|attack)|computer virus|trojan|rootkit)|"
    r"child\W{0,3}(porn|sex video|nude)|\bcsam\b|"
    r"bomb (kaise|kese|kaisay) (banate|banaye|banaen|banao|banta|banti)|"
    r"بم کیسے بنا)", re.I)

SELF_HARM = re.compile(
    r"((how|ways?) to (kill|hurt|harm) (myself|yourself)|suicide (methods?|ways?|kaise)|kill myself|i want to die|"
    r"khud ?kushi|mar jana chahta|marna chahta|مرنا چاہتا|خودکشی)", re.I)


def strip_cites(text: str) -> str:
    return CITE.sub("", text)


def wants_sources(text: str, model: Model) -> bool:
    return model.deep or bool(SRC_ASK.search(text))


def policy_check(text: str) -> str | None:
    """'support' (khud ko nuqsan ka zikr), 'refuse' (nuqsan-deh madad), warna None."""
    if SELF_HARM.search(text):
        return "support"
    if HARM.search(text):
        return "refuse"
    return None


def is_unclear(text: str) -> bool:
    s = re.sub(r"\s+", "", text)
    if not re.search(r"\w", s):
        return True
    if s.isascii() and len(s) < 3:
        return True
    return bool(re.fullmatch(r"(.)\1{3,}", s))
