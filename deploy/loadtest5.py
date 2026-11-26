#!/usr/bin/env python3
"""300 alag users x 5 lambe/mushkil sawal, har sawal alag zaban mein (English, Roman Urdu, Urdu, Hindi, Arabic, French, Spanish, German, Russian).
Sirf python stdlib (koi LLM/token kharch nahi). Jawab ko 5 cheezon se parakhta hai: error nahi, lambai, junk nahi,
fallback nahi, aur SAHI ZABAN mein jawab (script / stopwords). Usage: loadtest5.py SITE N [ramp_s]"""
import json, random, re, sys, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

SITE = (sys.argv[1] if len(sys.argv) > 1 else "http://137.23.39.13:8000").rstrip("/")
N = int(sys.argv[2]) if len(sys.argv) > 2 else 300
RAMP = float(sys.argv[3]) if len(sys.argv) > 3 else 900
Q = {
 "en": ["Explain {t} in depth: the core idea, one worked example, two common mistakes and when not to use it.",
        "Write a detailed Python program that {c}, with error handling and comments, then explain its time complexity.",
        "Design a scalable backend for a ride-hailing app: components, database, caching, failure handling and trade-offs."],
 "roman": ["{t} ko tafseel se samjhao: buniyadi idea, ek misaal, do aam ghaltiyan aur kab istemal nahi karna chahiye.",
           "Mujhe Python mein ek program likh kar do jo {c}, error handling ke sath, aur uski complexity bhi samjhao.",
           "Ek freelancer ke liye 30 din ka plan banao taake wo pehla client hasil kar sake, har hafte ke kaam ke sath."],
 "ur": ["{t} کو تفصیل سے سمجھائیں: بنیادی خیال، ایک مثال، دو عام غلطیاں اور کب استعمال نہیں کرنا چاہیے۔",
        "پاکستان کی معیشت کے بڑے مسائل اور ان کے ممکنہ حل تفصیل سے بیان کریں۔"],
 "hi": ["{t} को विस्तार से समझाइए: मूल विचार, एक उदाहरण, दो आम गलतियाँ और कब इस्तेमाल नहीं करना चाहिए।",
        "एक छोटे व्यवसाय के लिए डिजिटल मार्केटिंग की पूरी योजना विस्तार से बताइए।"],
 "ar": ["اشرح {t} بالتفصيل: الفكرة الأساسية ومثال واحد وخطآن شائعان ومتى لا يجب استخدامه.",
        "اكتب خطة مفصلة لتعلم البرمجة من الصفر خلال ستة أشهر مع مصادر وأهداف أسبوعية."],
 "fr": ["Explique {t} en détail : l'idée centrale, un exemple concret, deux erreurs courantes et quand ne pas l'utiliser.",
        "Rédige un email professionnel détaillé à un client pour expliquer un retard de deux semaines et proposer un nouveau calendrier."],
 "es": ["Explica {t} en detalle: la idea central, un ejemplo concreto, dos errores comunes y cuándo no usarlo.",
        "Escribe un plan detallado para lanzar una tienda online desde cero, con pasos, costos y riesgos."],
 "de": ["Erkläre {t} ausführlich: die Grundidee, ein konkretes Beispiel, zwei häufige Fehler und wann man es nicht verwenden sollte.",
        "Schreibe einen ausführlichen Plan für den Aufbau eines kleinen Startups mit Schritten, Kosten und Risiken."],
 "ru": ["Подробно объясни {t}: основная идея, один пример, две частые ошибки и когда это не стоит использовать.",
        "Составь подробный план изучения английского языка за шесть месяцев с еженедельными целями."],
}
LANGS = list(Q)
TOPICS = ["a bloom filter", "the CAP theorem", "gradient descent", "public key cryptography", "database indexing", "OAuth 2.0",
          "load balancing", "recursion", "inflation", "photosynthesis", "compound interest", "supply and demand", "Docker containers", "the event loop"]
CODE = ["reverses a linked list", "finds the nth Fibonacci number", "merges two sorted arrays", "counts word frequency in a text",
        "implements binary search", "validates an email address"]
STOP = {"fr": r"\b(le|la|les|des|est|une|pour|dans|que)\b", "es": r"\b(el|la|los|las|es|una|para|que|con)\b",
        "de": r"\b(der|die|das|und|ist|nicht|mit|ein|eine)\b", "en": r"\b(the|and|is|of|to|that|with)\b",
        "roman": r"\b(hai|hain|ke|ki|ka|ko|mein|se|aur|ye|kar|karein)\b"}
SCRIPT = {"ur": r"[؀-ۿ]", "ar": r"[؀-ۿ]", "hi": r"[ऀ-ॿ]", "ru": r"[Ѐ-ӿ]"}
JUNK = re.compile(r"something went wrong|i found some related|internal server|traceback|undefined|\bNone\s*$", re.I)
MODELS = ["mus-7.01-polyglot", "mus-7.01", "mus-7.01-deep", "mus-7.01-code", "mus-7.01-agent"]
res, lock, T0 = [], threading.Lock(), time.time()


def lang_ok(lang, text):
    if lang in SCRIPT:
        letters = re.findall(r"[^\W\d_]", text)
        return bool(letters) and len(re.findall(SCRIPT[lang], text)) / len(letters) > .4
    return len(re.findall(STOP[lang], text.lower())) >= 4


def ask(i, j):
    r = random.Random(i * 97 + j)
    lang = LANGS[(i + j * 2) % len(LANGS)]
    q = r.choice(Q[lang]).format(t=r.choice(TOPICS), c=r.choice(CODE)) + f" ({i}-{j})"
    model = MODELS[(i + j) % len(MODELS)]
    body = json.dumps({"message": q, "private": True, "model": model, "history": []}).encode()
    req = urllib.request.Request(SITE + "/api/chat", body, {"Content-Type": "application/json", "X-Client-Id": "l5-%05d-%d" % (i, int(T0))})
    t0, first, mode, err, text = time.time(), None, "", None, ""
    try:
        with urllib.request.urlopen(req, timeout=330) as resp:
            ev = ""
            for raw in resp:
                line = raw.decode("utf8", "replace").strip()
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    try:
                        d = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if ev == "token" and isinstance(d, str):
                        first = first if first is not None else time.time() - t0
                        text += d
                    elif ev == "replace" and isinstance(d, str):
                        text = d
                    elif ev == "error":
                        err = "error " + (str(d.get("code")) if isinstance(d, dict) else "")
                    elif ev == "done" and isinstance(d, dict):
                        mode = d.get("mode", "")
    except urllib.error.HTTPError as e:
        err = f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        err = type(e).__name__
    issue = err
    if not issue and len(text.strip()) < 150:
        issue = "too-short"
    elif not issue and JUNK.search(text):
        issue = "junk-text"
    elif not issue and mode in ("extractive", "none", "needllm"):
        issue = "no-llm-fallback"
    elif not issue and not lang_ok(lang, text):
        issue = "wrong-language"
    with lock:
        res.append({"i": i, "lang": lang, "model": model, "q": q[:50], "total": time.time() - t0, "first": first, "mode": mode,
                    "issue": issue, "len": len(text), "head": text[:70].replace("\n", " ")})


def user(i):
    time.sleep((i / N) * RAMP)
    for j in range(5):
        ask(i, j)
        time.sleep(random.Random(i + j).uniform(2, 6))


with ThreadPoolExecutor(max_workers=N) as ex:
    list(ex.map(user, range(N)))
ok = [r for r in res if not r["issue"]]
print(f"USERS={N} questions={len(res)} good={len(ok)} ({100 * len(ok) // max(1, len(res))}%) wall={time.time() - T0:.0f}s")
uo = {}
for r in res:
    uo.setdefault(r["i"], []).append(r["issue"] is None)
print("users ALL 5 good:", sum(all(v) for v in uo.values()), "| >=3 good:", sum(sum(v) >= 3 for v in uo.values()), "| 0 good:", sum(not any(v) for v in uo.values()))
by = {}
for r in res:
    if r["issue"]:
        by.setdefault(r["issue"], []).append(r)
print("ISSUES:", {k: len(v) for k, v in by.items()})
pl = {}
for r in res:
    a = pl.setdefault(r["lang"], [0, 0]); a[1] += 1; a[0] += r["issue"] is None
print("PER LANG good/total:", json.dumps(pl))
tt = sorted(r["total"] for r in ok); fs = sorted(r["first"] for r in res if r["first"] is not None)
pc = lambda v, p: round(v[min(len(v) - 1, int(len(v) * p))], 1) if v else None
print(f"good total p50={pc(tt, .5)} p95={pc(tt, .95)} | first_token p50={pc(fs, .5)} p95={pc(fs, .95)}")
for k, v in by.items():
    print(f"--- {k} ({len(v)}):")
    for r in v[:3]:
        print(f"   [{r['lang']}/{r['model'][8:]}] {r['q']!r} mode={r['mode']} len={r['len']} :: {r['head']!r}")
try:
    print("health:", urllib.request.urlopen(SITE + "/api/health", timeout=30).read()[:200].decode())
except Exception as e:  # noqa: BLE001
    print("health FAILED", e)
