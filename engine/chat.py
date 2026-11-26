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
from . import hints, projects
from .llm import stream_chat
from .mesh import fast_llm_available, llm_available
from .skills import compare, poem
from .policy import CREATIVE, EDIT, EXPLAIN, intent, is_unclear, is_vague, short_fact, policy_check, strip_cites, wants_sources
from .search import Searcher, coverage, detect_script, terms

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
    "capab": re.compile(r"^(what can you (do|help me with|make|build|create)( for me)?|how can you help( me)?|what are your (skills|features|abilities)|"
                        r"(ap|aap|tum) (meri|mujhe|mujy|mjy|mere liye) ((kasy|kaise|kese|kaisay) )?(madad|help)( (kasy|kaise|kese|kaisay))?( (kr|kar) (skty|sakte|sakty|sakta))?( (han|hain|ho|hai))?|"
                        r"(ap|aap|tum) (mujy|mujhe|mjy) kiya (bna|bana) (k |ke )?(dy|de|do) (skty|sakte|sakty)( (han|hain|ho))?|"
                        r"(ap|aap|tum) kiya kiya (kr|kar) (skty|sakte|sakty) (han|hain|ho)|"
                        r"(kya |kiya )?(ap|aap|tum|app) (kya|kiya) (kr|kar) (skty|sakte|sakty|sakta) (han|hain|ho))$"),
    "langreq": re.compile(r"^(mujhse|mujh se|mujhse|mujy|mujhe|mjy) (roman urdu|urdu|hindi|english) (m|mein|me|main) (baat|bat) (karo|kro|kar)$|^(roman urdu|urdu|hindi|punjabi|pashto|pasto|pukhto|arabic|russian|russion|english|french)( zuban| language| lang)?( (m|mein|me|main|mai|mn|mei) (baat|bat|jawab|answer|reply|likho|bolo)( (kro|karo|kar|do|dein|den))?)?$|"
                        r"^(talk|speak|reply|answer|baat kro|baat karo) (in |to me in )?(roman urdu|urdu|hindi|punjabi|arabic|russian|english|french)$|"
                        r"^(roman urdu|urdu|hindi|english|russian|russion|arabic|french|pashto|punjabi) (zuban )?(m|mein|me|main) (baat|bat) (kro|karo|kar)$"),
    "opensrc": re.compile(r"^(kya |kiya )?(ye|yeh|ap|aap|tum|app) (an |ik |ek )?open ?source (ho|hai|hain|han|hy|he)( kya)?$|^(are you|ap|aap|tum) (an |ik |ek )?(open ?source|opensource)( model| ai)?( ho| hain| han)?\??$|^(mean|matlab|mtlb)? ?(that )?(you'?r?e?|you are) (an )?open ?source( model| ai)?$|"
                          r"^(which|what|konsa|kaun sa) (model|llm|ai) (are you|ho|hain|use (karte|krte) (ho|hain))$"),
    "why": re.compile(r"^(why|should i|why should i|why do i need to|kyun|kyu|kiun) ?(should i |do i need to |i )?(use|choose|pick|try|prefer) (you|talashly|this|mus|tum|aap)( over \w+)?$|^(what makes you (special|different|better)|how are you different( from \w+)?|tum kyun|tumhari khasiyat|aap ki khasiyat)$"),
    "limits": re.compile(r"^(what are your|what'?s your|tell me your|your) (limits?|limitations?|weaknesses|restrictions|rules|policy|policies)$|^(what (can'?t|cannot|can not) you do|what are you not able to do|what do you not do|tum kya nahi kar sakte|aap kya nahi kar sakte)$"),
    "privacy": re.compile(r"^(do you|does talashly|will you|are you) (store|save|keep|log|record|read|storing|saving) (my |our |the )?(chats?|messages?|conversations?|data)( \w+)?$|^(is my (chat|data|conversation) (safe|private|stored|saved))$|^(kya (aap|tum) (meri|mere) (chat|baatein|data) (save|store) karte (ho|hain))$"),
    "free": re.compile(r"^(is (this|talashly|it) free|are you free|is talashly free to use|kya (ye|yeh|talashly) free hai|how much (does it|do you) cost|what is the price of talashly)$"),
    "sad": re.compile(r"^(i am|i'?m|im|main|mai|mein)? ?(feeling )?(very |bohat |bahut )?(sad|upset|lonely|tired|stressed|depressed|udaas|udas|pareshan|thak gaya|thaka hua|tanha|اداس|پریشان|उदास|परेशान)( hun| hoon| hu| hai| ho gaya| feel kar raha hun)?( aaj| today)?$"),
    "joke": re.compile(r"^(please |pls )?(tell|say|crack|give) (me )?(a |one )?(joke|funny)( please)?$|^(joke|koi joke|latifa|لطیفہ)( sunao| suna do| batao)?$"),
    "meta": re.compile(r"^(where|how) (does|do|did) (your|the) (data|knowledge|information|answers?) (come|comes|came|get|gets|got)( from)?$|^(tum|aap) (data|jawab|jawaab) kahan se (late|lete|laate) (ho|hain)$"),
    "inject": re.compile(r"^(please )?(ignore|forget|disregard) (all |your |the )?(previous|prior|above|earlier)? ?(instructions?|rules|prompt)\b.*|^(print|show|reveal|repeat|tell me) (me )?(your |the )?(system|hidden|initial) (prompt|instructions?)\b.*"),
    "help": re.compile(r"^(help|help me|what can you do|what do you do|how do (i|you) use (you|this)|features|"
                       r"madad|madad karo|kya kar sakte ho|kya kar sakte hain|tum kya kar sakte ho|aap kya kar sakte hain|"
                       r"مدد|آپ کیا کر سکتے ہیں|आप क्या कर सकते हैं)$"),
}

REPLIES: dict[str, dict[str, list[str]]] = {
    "capab": {
        "en": ["I can: answer questions from my own web index with sources; explain topics in simple words; write and test Python (I run the code in a safe sandbox with the Agent model); "
               "read a web page you paste; build a ready-to-run project as a ZIP (for example a video-edit bot, a YouTube downloader, a web scraper, a shop website); "
               "write emails and posts; and chat in many languages. Tell me what you want to make, in one line, and I'll start."],
        "roman": ["Main yeh kar sakta hoon: apne web index se sources ke saath jawab; topics aasan alfaaz mein samjhana; Python likhna aur (Agent model mein) mehfooz sandbox mein chala kar test karna; "
                  "aap ka paste kiya URL padhna; tayyar project ZIP mein banana (video-edit bot, YouTube downloader, web scraper, shop website); email/post likhna; aur kai zabanon mein baat. "
                  "Bas ek line mein bata dein kya banana hai, main shuru kar deta hoon."],
    },
    "langreq": {
        "en": ["I understand Roman Urdu, Urdu, Hindi and many other languages. My small open-source model is strongest in English and weaker in Roman Urdu, so for code and technical questions I give the most accurate answer in English and add a short Roman Urdu summary. Just ask your question in any language."],
        "roman": ["Main Roman Urdu, Urdu, Hindi aur kai zabanein samajhta hoon. Mera chhota open-source model English mein sab se mazboot hai aur Roman Urdu mein kamzor, is liye code aur technical sawalon ka sab se sahi jawab English mein deta hoon aur saath mein chhota Roman Urdu khulasa. Aap kisi bhi zaban mein sawal poochein."],
    },
    "opensrc": {
        "en": ["Talashly's search engine, index and chat app are my own code. The language model inside is an open-source model (Qwen3, Apache-2.0 licence) that I fine-tune on my own data, and answers cite my own crawled index. It is smaller than the big commercial models, so I may be wrong on hard questions: please check important answers."],
        "roman": ["Talashly ka search engine, index aur chat app apna code hai. Andar ka language model open-source hai (Qwen3, Apache-2.0 licence) jise main apne data par train karta hoon, aur jawab apne crawl kiye index se aate hain. Ye bade commercial models se chhota hai, is liye mushkil sawalon mein ghalti ho sakti hai: zaroori jawab khud check kar lein."],
    },
    "why": {
        "en": ["Talashly is a self-owned answer engine: it searches its own index of public web pages, can cite the pages an answer came from, and runs without third-party AI APIs. It also works in many languages and keeps no chat history on the server. It is smaller than the biggest commercial models, so for very hard code or maths treat answers as a strong first draft."],
        "roman": ["Talashly ka apna search index hai, jawab ke sources dikha sakta hai, aur kisi third-party AI API par nahi chalta. Kai zabanon mein kaam karta hai aur server par chat save nahi karta. Sab se bade commercial models se chhota hai, is liye mushkil code ya maths mein jawab ko pehla draft samjhein."],
    },
    "limits": {
        "en": ["I can answer questions, explain topics, write emails, poems and code, and build simple websites. My limits: I can be wrong on very hard code, maths or niche facts, live data (news, prices) depends on what I have indexed, and I decline clearly harmful requests. If I'm unsure I'll say so."],
        "roman": ["Main sawalon ke jawab, topics ki wazahat, email/poem/code aur simple websites bana sakta hoon. Meri hadein: mushkil code, maths ya niche facts mein ghalti ho sakti hai, taaza data (news, prices) mere index par munhasir hai, aur nuqsandeh cheezon se inkar karta hoon. Yaqeen na ho to saaf bata deta hoon."],
    },
    "privacy": {
        "en": ["Chats are not stored on the server by default; your conversation history stays in your own browser. Only feedback you choose to give (like /good or /bad) is kept."],
        "roman": ["Chat server par save nahi hoti; aap ki history aap ke apne browser mein rehti hai. Sirf wohi feedback rakha jata hai jo aap khud den (jaise /good ya /bad)."],
    },
    "free": {
        "en": ["Yes, Talashly is free to use, and you can create a free API key for your own apps."],
        "roman": ["Ji haan, Talashly free hai, aur apni apps ke liye free API key bhi bana sakte hain."],
    },
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


CLOCK = re.compile(r"^(what('?s| is)? ?(the )?|tell me (the )?|)(today'?s |current |todays )?(date|time|day)( and (date|time))?( is it)?( today| now| right now)?$|"
                   r"^(what )?(date|day|time) is (it|today)( now)?$|^(aaj|abhi) (ki |ka )?(date|tarikh|tareekh|time|din|waqt)( kya hai| kia hai| batao)?$|"
                   r"^(date|time|tarikh) (kya hai|kia hai|batao)$")


def clock(t: str, raw: str) -> str | None:
    """Tareekh/waqt server ki ghadi se (UTC) - LLM ya index andaza na lagaye."""
    if len(t) > 60 or not CLOCK.match(t):
        return None
    import datetime as dt
    n = dt.datetime.now(dt.timezone.utc)
    ps = dt.timezone(dt.timedelta(hours=5))
    p = n.astimezone(ps)
    if reply_lang(raw) == "en":
        return (f"Today is {n:%A, %d %B %Y} and it is {n:%H:%M} UTC ({p:%H:%M} in Pakistan time). "
                "I use the server clock, so for your own time zone please check your device.")
    return f"Aaj {n:%A, %d %B %Y} hai. Abhi UTC {n:%H:%M} (Pakistan mein {p:%H:%M}) bajay hain."


def smalltalk(text: str, model: Model) -> str | None:
    """Sirf choti, saaf baat-cheet (greeting/thanks...) par jawab; asli sawal ko kabhi nahi pakarta."""
    t = re.sub(r"[!?.,؟،۔:;~\"'`]+$", "", text.strip().lower())
    t = re.sub(r"\s+", " ", re.sub(r"[!?.,؟،۔]+", " ", t)).strip()
    c = calc(text)
    if c:
        return c
    d = clock(t, text)
    if d:
        return d
    if not t or len(t) > 90 or len(t.split()) > 12:
        return None
    t = re.sub(r"^(mean kh|mean|matlab|mtlb|ok|okay|ji|acha|phir|to|yar|bhai)\s+", "", t)
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


def _has_page(history: list[dict] | None) -> bool:
    return any(h.get("role") == "assistant" and re.search(r"<html|```html|<!doctype", str(h.get("content", "")), re.I)
               for h in (history or [])[-6:])


def _page_edit(text: str, history: list[dict] | None) -> bool:
    """Pichhle page ko badalne ki farmaish (edit/upgrade...), na ke bilkul naya "website banao"."""
    return _has_page(history) and bool(EDIT.search(text)) and not re.search(r"\b(new|another|different|naya|nayi|alag)\b", text, re.I)


_STARTER = re.compile(r"^\s*(ok|okay|and|also|now|then|next|so|but|aur|phir|ab|acha|theek)\b", re.I)


def _follow_up(text: str) -> bool:
    """Kya ye pichhli baat ka silsila hai? Warna history LLM ko nahi jati (mukhtalif topics mix na hon)."""
    if _STARTER.match(text) or (len(text.split()) <= 9 and (FOLLOW.search(text) or EDIT.search(text))):
        return True
    return False


def _last_site_request(history: list[dict] | None) -> str | None:
    for h in reversed(history or []):
        if h.get("role") == "user" and wants_site(str(h.get("content", ""))):
            return str(h["content"])[:600]
    return None


def format_with_sources(text: str, sources: list[dict]) -> str:
    if not sources:
        return text
    lines = [f"[{s['n']}] {s.get('title') or s['domain']} - {s['url']}" for s in sources]
    return f"{text}\n\nSources:\n" + "\n".join(lines)


SYSTEM_PROMPT = (
    "You are Talashly's assistant (model MUS 7.01): warm, direct and expert, like a professional human colleague. "
    "First work out what the user actually wants and what a great answer looks like; if truly ambiguous ask ONE short question. "
    "Reply in the user's language (English by default). Be concise but complete: simple factual questions get 2-4 sentences, not an essay; lists and how-tos stay under about 350 words and always finish cleanly. Do the task instead of explaining why you might not; "
    "never say 'I cannot' for ordinary requests. Decline only clearly harmful requests (weapons, malware, self-harm methods, sexual "
    "content involving minors, hate, fraud) briefly, and offer a safe alternative. Never mention sources, searching, an index, or your "
    "training data unless asked. For websites output one complete single-file HTML page using Tailwind CSS via CDN, responsive, in a "
    "```html block. Do not claim to be human. /no_think"
)


# Har jawab kahan se aaya: model (apna open-source LLM), index (apna crawl), rule (fixed code/builder), learned (users ke 👍)
ORIGIN = {"llm": "model", "llm-direct": "model", "extractive": "index", "builder": "rule", "chat": "rule", "clarify": "rule",
          "refuse": "rule", "skill": "rule", "learned": "learned", "needllm": "rule", "agent": "model", "error": "rule", "none": "index"}


def qkey(text: str) -> str:
    """Sawal ki pehchaan: chhote huroof, sirf alfaaz (spaces/punctuation ka farq nahi)."""
    return " ".join(re.findall(r"\w+", text.lower()))[:200]


LEARN_TTL_DAYS = 60      # purane yaad kiye jawab 60 din baad khud hat jate hain (knowledge decay)
CACHE_TTL_S = 900        # tez jawab: wahi sawal 15 minute tak memory se
CACHE_MAX = 300
QUEUE_WAIT_S = 100.0
OWNER_MAX = 2   # ek user ek waqt mein itne bhaari sawal (baqi users ko jagah milti rahe)
LEARN_MIN_VOTERS = 3  # itne alag log 👍 den tab hi jawab "seekha hua" mana jata hai (ek akela bigaar na sake)


class Chat:
    def __init__(self, cfg: dict, db: DB, brain: Brain, searcher: Searcher):
        self.cfg, self.db, self.brain, self.searcher = cfg, db, brain, searcher
        self.c = cfg["chat"]
        self._sem: asyncio.Semaphore | None = None
        self.waiting = 0
        self.active = 0
        self._cache: OrderedDict[tuple, tuple[float, str, str]] = OrderedDict()
        self._per_owner: dict[str, int] = {}      # har user ke chalte/intezar mein bhaari sawal (fairness)
        self._svc_s = 25.0                         # bhaari jawab ka ausat waqt (EMA) -> queue ka andaza
        self._inflight: dict[tuple, asyncio.Event] = {}   # wahi sawal ek saath: ek banaye, baqi uska jawab len

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
            # itna intezar nahi: 25 s mein jagah na mile to saaf "busy" (user atka na rahe, retry kare)
            await asyncio.wait_for(self._sem.acquire(), QUEUE_WAIT_S)
        except asyncio.TimeoutError:
            return False
        finally:
            self.waiting -= 1
        self.active += 1
        return True

    def _load_cap(self, n: int) -> int:
        """Bheed mein jawab chhote (kam tokens) taake zyada log nipte: queue 10+ -> 450, 40+ -> 300 (website/HTML par nahi)."""
        if n >= 4000:
            return n
        return min(n, 300 if self.waiting >= 40 else 450 if self.waiting >= 10 else n)

    def queue_eta(self) -> tuple[int, int]:
        """(aap ka number, andaza second): jab sab slot bhare hon."""
        n = self.waiting + 1
        slots = max(1, int(self.c["max_concurrent"]))
        return n, int(max(1, n / slots) * self._svc_s)

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

        # halke kaam (greeting/date/calc/clarify/builder/refuse/commands) slot nahi lete: bhari load mein bhi foran jawab
        cheap = (text.startswith("/") or smalltalk(text, model) is not None or policy_check(text) is not None
                 or is_unclear(text) or projects.match(text) is not None or (is_vague(text) and not history and not prev_user) or (wants_site(text) and not fast_llm_available()))
        held = False
        ckey0 = (model.id, qkey(text))
        lead = False
        if not cheap and not history and not wants_sources(text, model):
            ev0 = self._inflight.get(ckey0)
            if ev0 is not None:
                try:
                    await asyncio.wait_for(ev0.wait(), 90)
                except asyncio.TimeoutError:
                    pass
                if self._cache_get(ckey0):
                    cheap = True   # pehle wale ne jawab bana kar yaad kar liya: slot ki zaroorat nahi
            else:
                self._inflight[ckey0] = asyncio.Event()
                lead = True
        if not cheap and self._per_owner.get(owner, 0) >= OWNER_MAX:
            if lead:
                self._inflight.pop(ckey0, asyncio.Event()).set()
            yield "error", {"code": "busy", "message": tr("owner_busy", text)}
            return
        if not cheap:
            if self._sem is not None and self._sem.locked() and self.waiting < int(self.c["queue_max"]):
                n_, s_ = self.queue_eta()
                yield "step", {"msg": tr("queued", text, n=n_, s=s_)}
            self._per_owner[owner] = self._per_owner.get(owner, 0) + 1
            t_hold = time.time()
            if not await self._acquire():
                self._per_owner[owner] = max(0, self._per_owner.get(owner, 1) - 1)
                if lead:
                    self._inflight.pop(ckey0, asyncio.Event()).set()
                yield "error", {"code": "busy", "message": tr("busy", text)}
                return
            held = True
        elif lead:
            self._inflight.pop(ckey0, asyncio.Event()).set()
            lead = False
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
            if talk is None and verdict is None and (is_unclear(text) or (is_vague(text) and not prev_user and not history)):
                verdict = "clarify"
            if talk is not None or verdict is not None:
                talk = talk if talk is not None else tr(verdict, text)  # type: ignore[arg-type]
                buf = talk
                yield "token", talk
                done = {"mode": "chat" if verdict is None else verdict, "model": model.id}
            else:
                smart_done = None
                try:
                    async for ev, data in self._smart(text, history, prev_user, model):
                        if ev == "done":
                            smart_done = data
                            continue
                        if ev == "token":
                            buf += data
                        yield ev, data
                except Exception as e:  # noqa: BLE001  koi bhi bug ho: stream adhoora na kate, saaf jawab + log
                    self.db.log("error", f"smart failed {type(e).__name__}: {e}")
                    msg = tr("error", text)
                    buf = (buf + "\n\n" if buf else "") + msg
                    yield "token", ("\n\n" if buf != msg else "") + msg
                    smart_done = {"mode": "error"}
                if smart_done is not None:
                    done = dict(smart_done)
                    done["model"] = model.id
                    if (done.get("mode") == "llm-direct" or (done.get("mode") == "agent" and "clock" not in done["agent"]["plan"]
                                                             and not done["agent"]["errors"])) \
                            and not show and not history and buf and len(buf) < 4000:
                        self._cache_put((model.id, qkey(text)), buf, done["mode"])
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
                        if model.code_first and not re.match(r"\s*code:", eq, re.I) and (await asyncio.to_thread(self.searcher.search_code, eq, None, 1)):
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
            done["origin"] = ORIGIN.get(done.get("mode", ""), "index")
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
            if lead:
                self._inflight.pop(ckey0, asyncio.Event()).set()
            if held:
                self._svc_s = 0.9 * self._svc_s + 0.1 * min(120.0, max(2.0, time.time() - t_hold))
                self._per_owner[owner] = max(0, self._per_owner.get(owner, 1) - 1)
                if not self._per_owner[owner]:
                    self._per_owner.pop(owner, None)
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
        if not history and not wants_sources(text, model):
            hit = self._cache_get((model.id, qkey(text)))
            if hit:   # pehle bana hua jawab: milliseconds mein
                yield "token", hit[0]
                yield "done", {"mode": hit[1], "cached": True}
                return
        proj = projects.build(text)
        if proj:   # tayyar template: LLM ke baghair, seconds mein asli ZIP
            yield "token", proj["text"]
            yield "download", {"url": proj["url"], "name": proj["name"], "size": proj["size"]}
            yield "done", {"mode": "project"}
            return
        if model.agent:
            from . import agent
            async for ev, data in agent.run(self, text, history, prev_user, model):
                yield ev, data
            return
        llm = self.cfg["llm"]
        ready = llm_available(llm)
        site = wants_site(text)
        if site and not ready:
            yield "token", build_site(text)
            yield "done", {"mode": "builder"}
            return
        edit = _page_edit(text, history)
        if site and ready and not fast_llm_available() and not edit:
            # sirf CPU node: poori website LLM se 60-90 s (10 tok/s); template builder < 1 s mein poora page deta hai.
            # LLM tab jab GPU node zinda ho, ya user pichle page ko edit/upgrade kare (history mein html)
            yield "token", build_site(text)
            yield "done", {"mode": "builder"}
            return
        if edit and not fast_llm_available():
            # CPU par poora page dobara likhna/prefill 3 min+ lagta hai: asli farmaish + naya hukm jod kar builder se turant naya page
            base = _last_site_request(history)
            yield "token", build_site(f"{base or 'website'} {text}")
            yield "done", {"mode": "builder"}
            return
        if model.deep and not (ready and (site or intent(text) == "task")):
            return  # Deep: sawal ho to research + sources; "email likho / poem / code" jaisa kaam ho aur LLM zinda ho to seedha LLM (research bekaar aur sust)
        if not ready:
            for fn in (lambda: poem(text), lambda: compare(text, self.searcher)):
                got = fn()
                if got:
                    yield "token", got
                    yield "done", {"mode": "skill"}
                    return
        eq = contextualize(text, prev_user)
        try:
            # non-Latin (Urdu/Arabic/Hindi...) sawal: LLM zinda ho to seedha LLM (index ke ghalat-zaban hits + live research 80 s tak lete the)
            weak = detect_script(eq) != "latin" or coverage(eq, await asyncio.to_thread(self.searcher.search_many, [eq], 8)) < 0.6
        except Exception:  # noqa: BLE001  search gira: normal rasta apna error-jawab dega
            return
        if not ready:
            if CREATIVE.search(text) and weak:
                yield "token", tr("needllm", text)
                yield "done", {"mode": "needllm"}
            return
        quick = short_fact(text) and not wants_sources(text, model) and not model.deep
        longq = len(text) >= 90   # lamba/mushkil sawal: index ke tukde jawab nahi dete, LLM likhe
        if not (site or intent(text) == "task" or weak or quick or longq):
            return
        msgs = [{"role": "system", "content": SYSTEM_PROMPT.replace(" /no_think", "") + hints.extra(text) + " /no_think"}]
        for h in (history or [])[-6:] if (edit or _follow_up(text)) else []:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                msgs.append({"role": h["role"], "content": str(h["content"])[:9000 if (h["role"] == "assistant" and edit) else 1500]})
        msgs.append({"role": "user", "content": text})
        got = False
        try:
            async for tok in stream_chat(llm, msgs, max_tokens=self._load_cap(4000 if (site or CREATIVE.search(text) or "html" in text.lower())
                                                                     else 1000 if EXPLAIN.search(text) else 700)):
                got = True
                yield "token", tok
            if got:
                yield "done", {"mode": "llm-direct"}
                return
        except Exception as e:  # noqa: BLE001
            self.db.log("heal", f"llm direct failed {type(e).__name__}: {e}")
            if got and site:   # adhoora HTML dikhane se behtar: builder ka poora, chalta hua page
                yield "replace", build_site(text)
                yield "done", {"mode": "builder"}
                return
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
