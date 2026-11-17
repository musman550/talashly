"""Zaban pehchan aur user-facing paigham: jawab usi zaban mein jis mein sawal hai (English / Roman Urdu / Urdu)."""
from __future__ import annotations

import re

URDU_LETTERS = re.compile(r"[ٹڈڑںےگچھۓیکہپ]")  # Urdu/Farsi ke hurouf (Arabic mein nahi hote)
ROMAN_MARKERS = {"assalam", "assalamualaikum", "alaikum", "walaikum", "aoa", "kya", "hai", "hain", "aap", "tum",
                 "kaise", "kese", "kaisa", "salam", "salaam", "shukriya", "shukria", "mujhe", "mujy", "nahi", "nahin",
                 "haan", "kaun", "kon", "btao", "batao", "bataiye", "khuda", "allah", "hafiz", "yar", "yaar", "bhai",
                 "janab", "karo", "karna", "sakte", "sakta", "naam", "mera", "meri", "apna", "kia", "kaam",
                 "kaisay", "kyun", "kyon", "iska", "uska", "mein", "main", "wala", "wali"}  # "hy"/"he" jaan-boojh kar nahi (hy = hi bhi hota hai)


def reply_lang(text: str) -> str:
    """'ur' (Urdu script), 'roman' (Roman Urdu/Hinglish), warna 'en'."""
    if URDU_LETTERS.search(text):
        return "ur"
    if set(re.findall(r"[a-z']+", text.lower())) & ROMAN_MARKERS:
        return "roman"
    return "en"


MSG: dict[str, dict[str, str]] = {
    "none": {
        "en": "I don't have a good answer to that yet. I've noted it and will keep learning. You could also try asking it in different words.",
        "roman": "Is sawal ka achha jawab mere paas abhi nahi hai. Maine note kar liya hai aur seekhta rahunga. Aap sawal doosre alfaaz mein bhi pooch sakte hain.",
        "ur": "اس سوال کا اچھا جواب میرے پاس ابھی نہیں ہے۔ میں نے نوٹ کر لیا ہے اور سیکھتا رہوں گا۔ آپ سوال دوسرے الفاظ میں بھی پوچھ سکتے ہیں۔"},
    "nosent": {
        "en": "I found some related information, but nothing that answers this directly. Could you rephrase it or add more detail?",
        "roman": "Kuch mutaalliqa maloomat mili hai lekin seedha jawab nahi mila. Kya aap sawal thora aur wazeh kar sakte hain?",
        "ur": "کچھ متعلقہ معلومات ملی ہیں لیکن براہِ راست جواب نہیں ملا۔ کیا آپ سوال کو مزید واضح کر سکتے ہیں؟"},
    "needllm": {
        "en": "I can build complete websites right now (tell me the business name and type, e.g. \"website for Noor Bakery\"). For custom writing or code like this, the site owner needs to switch on my AI writer, and then I'll do it in seconds.",
        "roman": "Main abhi mukammal website bana sakta hoon (business ka naam aur qism batayein, jaise \"Noor Bakery ki website\"). Is tarah ki custom likhai ya code ke liye site owner ko mera AI writer on karna hoga, phir main seconds mein kar dunga.",
        "ur": "میں ابھی مکمل ویب سائٹ بنا سکتا ہوں (بزنس کا نام اور قسم بتائیں)۔ اس طرح کی کسٹم تحریر یا کوڈ کے لیے سائٹ اونر کو میرا AI رائٹر آن کرنا ہوگا، پھر میں سیکنڈوں میں کر دوں گا۔"},
    "fb_ok": {
        "en": "Thanks, noted. That helps me get better.",
        "roman": "Shukriya, note kar liya. Is se main behtar hota hoon.",
        "ur": "شکریہ، نوٹ کر لیا۔ اس سے میں بہتر ہوتا ہوں۔"},
    "fb_none": {
        "en": "Ask me something first, then use /good or /bad on my answer.",
        "roman": "Pehle koi sawal poochein, phir mere jawab par /good ya /bad likhein.",
        "ur": "پہلے کوئی سوال پوچھیں، پھر میرے جواب پر /good یا /bad لکھیں۔"},
    "clarify": {
        "en": "I didn't quite get that. What would you like to know?",
        "roman": "Mujhe samajh nahi aaya. Aap kya jaanna chahte hain?",
        "ur": "مجھے سمجھ نہیں آیا۔ آپ کیا جاننا چاہتے ہیں؟"},
    "refuse": {
        "en": "Sorry, I can't help with that. I'm happy to help with something else.",
        "roman": "Maazrat, main is mein madad nahi kar sakta. Kuch aur poochna ho to zaroor batayein.",
        "ur": "معذرت، میں اس میں مدد نہیں کر سکتا۔ کچھ اور پوچھنا ہو تو ضرور بتائیں۔"},
    "support": {
        "en": "I'm really sorry you're going through this, and I'm glad you said it out loud. You don't have to carry it alone: please contact your local emergency number or someone you trust right now. I'm here to talk as well.",
        "roman": "Mujhe bahut afsos hai ke aap is se guzar rahe hain, aur aap ne baat kahi ye achha kiya. Aap ko akele nahi sehna: abhi apne ilaqe ki emergency service ya kisi bharosemand shakhs se rabta karein. Main bhi baat karne ke liye yahan hoon.",
        "ur": "مجھے بہت افسوس ہے کہ آپ اس سے گزر رہے ہیں، اور آپ نے بات کہی یہ اچھا کیا۔ آپ کو اکیلے نہیں سہنا: ابھی اپنے علاقے کی ایمرجنسی سروس یا کسی بھروسے مند شخص سے رابطہ کریں۔ میں بھی بات کرنے کے لیے یہاں ہوں۔"},
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
