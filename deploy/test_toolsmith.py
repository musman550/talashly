#!/usr/bin/env python3
"""Asli server par Toolsmith ka test (stdlib, koi paid token nahi). Usage: test_toolsmith.py SITE"""
import ast, json, re, sys, time, urllib.request

SITE = (sys.argv[1] if len(sys.argv) > 1 else "http://137.23.39.13:8000").rstrip("/")
CASES = [
    ("shop-html", "ik html website bnao jis m shopify store ka all data hn dummy, professional, cart drawer, search, filter, responsive", "html"),
    ("py-roman", "mujy ek python script bnao jo csv file parh kar har column ka average nikaly, error handling ke sath", "py"),
    ("flask-api", "Write a Flask API with /todos CRUD endpoints using an in-memory list, with input validation.", "py"),
    ("scraper-en", "Python script that downloads a web page with requests, extracts all links with BeautifulSoup and saves them to links.txt", "py"),
    ("landing-ur", "ایک لینڈنگ پیج html میں بنائیں ایک کیفے کے لیے مینو اور رابطہ فارم کے ساتھ", "html"),
]


def call(msg):
    body = json.dumps({"message": msg}).encode()
    req = urllib.request.Request(SITE + "/api/toolsmith", body, {"Content-Type": "application/json", "X-Client-Id": "tt%d" % int(time.time() * 1000)})
    text, steps, dl, err, src, ev = "", [], None, None, "", ""
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=420) as r:
        for raw in r:
            line = raw.decode("utf8", "replace").strip()
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    d = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if ev == "token":
                    text += d
                elif ev == "step":
                    steps.append(d.get("msg"))
                elif ev == "download":
                    dl = d
                elif ev == "error":
                    err = d.get("message")
                elif ev == "done":
                    src = d.get("source", "")
    return text, steps, dl, err, src, time.time() - t0


bad = 0
for name, msg, kind in CASES:
    try:
        text, steps, dl, err, src, secs = call(msg)
    except Exception as e:  # noqa: BLE001
        print(f"[{name}] FAIL request: {e!r}"); bad += 1; continue
    blocks = re.findall(r"```(\w*)\n(.*?)```", text, re.S)
    code = "\n".join(b for _, b in blocks)
    probs = []
    if err: probs.append("error:" + err)
    if text.count("```") % 2: probs.append("unclosed-fence")
    if not blocks: probs.append("no-code")
    if kind == "html":
        low = code.lower()
        for t in ("</html>", "</script>", "<nav", "<footer"):
            if t not in low: probs.append("missing " + t)
        if "cart" in msg.lower() and "cart" not in low: probs.append("no-cart")
    else:
        for lang, b in blocks:
            if lang in ("python", "py", ""):
                try: ast.parse(b)
                except SyntaxError as e: probs.append(f"py-syntax line {e.lineno}")
    if not dl: probs.append("no-zip")
    if "(Jawab adhoora" in text: probs.append("cut-off-note")
    bad += bool(probs)
    print(f"[{name}] {'OK ' if not probs else 'BAD'} src={src} {secs:.0f}s chars={len(text)} steps={len(steps)} {probs or ''}")
    if probs: print("   head:", text[:160].replace("\n", " "), "| tail:", text[-120:].replace("\n", " "))
try:
    print("status:", urllib.request.urlopen(SITE + "/api/mesh/status", timeout=30).read().decode()[:400])
except Exception as e:  # noqa: BLE001
    print("status FAILED", e)
print("RESULT:", "ALL OK" if not bad else f"{bad} of {len(CASES)} bad")
