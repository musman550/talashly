"""Zaban pehchan aur user-facing paigham: jawab usi zaban mein jis mein sawal hai (English / Roman Urdu / Urdu)."""
from __future__ import annotations

import re

URDU_LETTERS = re.compile(r"[ٹڈڑںےگچھۓیکہپ]")  # Urdu/Farsi ke hurouf (Arabic mein nahi hote)
ROMAN_MARKERS = {"assalam", "assalamualaikum", "alaikum", "walaikum", "aoa", "kya", "hai", "hain", "aap", "tum",
                 "kaise", "kese", "kaisa", "salam", "salaam", "shukriya", "shukria", "mujhe", "mujy", "nahi", "nahin",
                 "haan", "kaun", "kon", "btao", "batao", "bataiye", "khuda", "allah", "hafiz", "yar", "yaar", "bhai",
                 "janab", "karo", "karna", "sakte", "sakta", "naam", "mera", "meri", "apna", "kia", "hy", "kaam",
                 "kaisay", "kyun", "kyon", "iska", "uska", "mein", "main", "wala", "wali"}


def reply_lang(text: str) -> str:
    """'ur' (Urdu script), 'roman' (Roman Urdu/Hinglish), warna 'en'."""
    if URDU_LETTERS.search(text):
        return "ur"
    if set(re.findall(r"[a-z']+", text.lower())) & ROMAN_MARKERS:
        return "roman"
    return "en"


MSG: dict[str, dict[str, str]] = {
    "none": {
        "en": "I don't have an answer to that in my index yet. I've noted the question and will try again when the crawler brings in new content.",
        "roman": "Mere index mein is sawal ka jawab abhi nahi hai. Maine ise note kar liya hai; crawler naya content layega to dobara koshish karunga.",
        "ur": "میرے انڈیکس میں اس سوال کا جواب ابھی نہیں ہے۔ میں نے اسے نوٹ کر لیا ہے؛ نیا مواد آنے پر دوبارہ کوشش کروں گا۔"},
    "nosent": {
        "en": "I found sources (see below) but none of them contains a sentence that answers this directly.",
        "roman": "Sources mile hain (neeche dekhein) lekin un mein seedha jawab wala jumla nahi mila.",
        "ur": "ذرائع ملے ہیں (نیچے دیکھیں) لیکن ان میں براہِ راست جواب والا جملہ نہیں ملا۔"},
    "weak": {
        "en": "My index answer is weak, so I'm checking live sources...",
        "roman": "Index mein jawab kamzor hai, live sources se la raha hoon...",
        "ur": "انڈیکس میں جواب کمزور ہے، لائیو ذرائع سے لا رہا ہوں..."},
    "live_ok": {
        "en": "{n} new pages found and indexed.", "roman": "{n} naye pages mile aur index ho gaye.",
        "ur": "{n} نئے صفحات ملے اور انڈیکس ہو گئے۔"},
    "code_found": {
        "en": "Found {n} places in the code index. Best match: {path} (lines {a}-{b}, {lang}).",
        "roman": "Code index mein {n} jagah mila. Sab se behtar: {path} (lines {a}-{b}, {lang}).",
        "ur": "کوڈ انڈیکس میں {n} جگہیں ملیں۔ بہترین: {path} (لائنیں {a}-{b}، {lang})۔"},
    "code_miss": {
        "en": "Not in the local code index; checking web docs.", "roman": "Local code index mein nahi mila; web docs mein dekh raha hoon.",
        "ur": "مقامی کوڈ انڈیکس میں نہیں ملا؛ ویب دستاویزات دیکھ رہا ہوں۔"},
    "error": {
        "en": "Something went wrong on my side; I've recorded it. Please try again.",
        "roman": "Andar koi masla aaya; maine record kar liya hai. Dobara koshish karein.",
        "ur": "اندر کوئی مسئلہ آیا؛ میں نے ریکارڈ کر لیا ہے۔ دوبارہ کوشش کریں۔"},
    "busy": {
        "en": "The server is very busy right now. Please try again in a few seconds.",
        "roman": "Server abhi bohat masroof hai. Kuch second baad dobara koshish karein.",
        "ur": "سرور ابھی بہت مصروف ہے۔ چند سیکنڈ بعد دوبارہ کوشش کریں۔"},
    "empty": {"en": "Empty message. Please type something.", "roman": "Khali message. Kuch likhein.", "ur": "خالی پیغام۔ کچھ لکھیں۔"},
    "too_long": {
        "en": "Message is too long (max {n} characters).", "roman": "Message bohat lamba hai (max {n} characters).",
        "ur": "پیغام بہت لمبا ہے (زیادہ سے زیادہ {n} حروف)۔"},
    "not_found": {"en": "That conversation was not found.", "roman": "Ye conversation nahi mili.", "ur": "یہ گفتگو نہیں ملی۔"},
    "conv_full": {
        "en": "This conversation has become too long; please start a new chat.",
        "roman": "Ye conversation bahut lambi ho gayi; nayi chat shuru karein.",
        "ur": "یہ گفتگو بہت لمبی ہو گئی ہے؛ نئی چیٹ شروع کریں۔"},
}


def tr(key: str, text: str, **kw: object) -> str:
    return MSG[key][reply_lang(text)].format(**kw)
