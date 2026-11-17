"""Website banane wala (LLM ke bina bhi chalta hai): user ki baat se naam, qism aur rang samajh kar
Tailwind CSS wala single-file HTML banata hai. Sab kuch escape hota hai (XSS nahi)."""
from __future__ import annotations

import html
import re

SITE = re.compile(r"\b(web ?site|web ?page|landing page|home ?page|portfolio|online store|site)\b|ویب ?سائٹ|वेबसाइट", re.I)
BUILD = re.compile(r"\b(make|build|create|design|generate|develop|code|write|need|want|can you|could you|please|banao|bana|bnao|bnado|banado|chahiye|chahye|chhye|dedo|do)\b|بنا", re.I)
NAME = re.compile(r"(?:called|named|name is|naam|name)\s+[\"'“]?([A-Za-z0-9][\w &.'-]{1,40}?)[\"'”]?(?=\s+(?:website|site|for|with|in|and|using|ka|ki|ke)\b|[,.!?]|$)", re.I)
KI = re.compile(r"([A-Za-z0-9][\w &.'-]{1,40}?)\s+(?:ki|ka|ke)\s+(?:web ?site|site)\b", re.I)
FOR = re.compile(r"\bfor\s+(?:my|our|a|an|the)?\s*([A-Za-z0-9][\w &.'-]{1,40}?)(?=\s+(?:with|in|and|using|that|which|website|site)\b|[,.!?]|$)", re.I)

KINDS = {
    "restaurant": (("restaurant", "cafe", "café", "food", "bakery", "pizza", "burger", "biryani"),
                   "Fresh food, made with love", "Come hungry. Leave happy.", ("Fresh ingredients", "Daily-baked favourites", "Easy online ordering"), "View menu"),
    "gym": (("gym", "fitness", "yoga", "trainer", "workout"),
            "Stronger every single day", "Coaching, classes and a community that keeps you going.", ("Expert trainers", "Flexible memberships", "Results you can see"), "Join today"),
    "clinic": (("clinic", "doctor", "dental", "dentist", "hospital", "medical", "health"),
               "Care you can trust", "Friendly, professional healthcare for the whole family.", ("Experienced doctors", "Easy appointments", "Modern equipment"), "Book appointment"),
    "agency": (("agency", "marketing", "seo", "ai", "software", "startup", "saas", "tech", "app"),
               "We turn ideas into growth", "Strategy, design and technology that move your business forward.", ("Clear strategy", "Beautiful design", "Measurable results"), "Get a free quote"),
    "shop": (("shop", "store", "ecommerce", "e-commerce", "boutique", "clothing", "fashion", "shoes", "jewelry"),
             "Find something you'll love", "Quality products, fair prices, fast delivery.", ("Curated products", "Secure checkout", "Fast delivery"), "Shop now"),
    "school": (("school", "academy", "course", "tutor", "training", "education", "institute"),
               "Learn. Grow. Succeed.", "Practical courses taught by people who know the subject.", ("Expert teachers", "Learn at your pace", "Certificates"), "Enroll now"),
    "portfolio": (("portfolio", "freelancer", "designer", "developer", "photographer", "resume", "cv", "personal"),
                  "Hi, I build things people enjoy", "A selection of my recent work and how we can work together.", ("Clean, thoughtful design", "Reliable delivery", "Friendly communication"), "Contact me"),
    "law": (("law", "lawyer", "legal", "attorney", "advocate"),
            "Clear legal advice", "Straight answers and strong representation when it matters.", ("Experienced counsel", "Confidential consultations", "Clear fees"), "Book consultation"),
}
COLORS = {"blue": "blue", "green": "emerald", "red": "rose", "purple": "violet", "orange": "orange", "pink": "pink",
          "yellow": "amber", "teal": "teal", "black": "slate", "dark": "slate", "indigo": "indigo"}


def wants_site(text: str) -> bool:
    return bool(SITE.search(text) and BUILD.search(text))


def _guess(text: str) -> tuple[str, str]:
    low = text.lower()
    for kind, (words, *_rest) in KINDS.items():
        if any(re.search(rf"\b{re.escape(w)}\b", low) for w in words):
            return kind, ""
    return "agency", ""


def _name(text: str) -> str:
    for rx in (NAME, FOR, KI):
        m = rx.search(text)
        if m:
            n = re.sub(r"\s+", " ", m.group(1)).strip(" .'-")
            n = re.sub(r"^(?:(?:bana|banao|bnao|bnado|banado|do|dedo|de|mere|meri|mera|please|pls|make|build|create|a|an|the)\s+)+", "", n, flags=re.I)
            if n and n.lower() not in {"me", "you", "us", "it", "website", "site", "business", "company", "my", "do", "mere", "meri", "mera", "bana do", "bana"}:
                return n[:40]
    return "Your Brand"


def build_site(text: str) -> str:
    kind, _ = _guess(text)
    _, headline, sub, feats, cta = (None, *KINDS[kind][1:])
    name = _name(text)
    low = text.lower()
    color = next((v for k, v in COLORS.items() if re.search(rf"\b{k}\b", low)), "indigo")
    e = html.escape
    cards = "\n".join(
        f'      <div class="rounded-2xl border border-slate-200 dark:border-slate-800 p-6 shadow-sm">\n'
        f'        <div class="mb-3 inline-flex h-10 w-10 items-center justify-center rounded-lg bg-{color}-100 text-{color}-700 font-bold">{i}</div>\n'
        f'        <h3 class="text-lg font-semibold">{e(f)}</h3>\n'
        f'        <p class="mt-2 text-sm text-slate-600 dark:text-slate-400">Write a short line here about "{e(f.lower())}".</p>\n      </div>'
        for i, f in enumerate(feats, 1))
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{e(name)}</title>
  <meta name="description" content="{e(sub)}">
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-white text-slate-900 dark:bg-slate-950 dark:text-slate-100 antialiased">
  <header class="mx-auto flex max-w-5xl items-center justify-between px-6 py-5">
    <a href="#" class="text-xl font-bold text-{color}-600">{e(name)}</a>
    <nav class="hidden gap-6 text-sm sm:flex"><a href="#features" class="hover:text-{color}-600">Features</a><a href="#contact" class="hover:text-{color}-600">Contact</a></nav>
  </header>
  <main>
    <section class="mx-auto max-w-5xl px-6 py-20 text-center">
      <h1 class="text-4xl font-extrabold tracking-tight sm:text-6xl">{e(headline)}</h1>
      <p class="mx-auto mt-6 max-w-2xl text-lg text-slate-600 dark:text-slate-400">{e(sub)}</p>
      <a href="#contact" class="mt-10 inline-block rounded-xl bg-{color}-600 px-8 py-3 font-semibold text-white shadow hover:bg-{color}-700">{e(cta)}</a>
    </section>
    <section id="features" class="mx-auto grid max-w-5xl gap-6 px-6 pb-20 sm:grid-cols-3">
{cards}
    </section>
    <section id="contact" class="bg-{color}-50 dark:bg-slate-900">
      <div class="mx-auto max-w-xl px-6 py-16 text-center">
        <h2 class="text-2xl font-bold">Get in touch</h2>
        <p class="mt-2 text-slate-600 dark:text-slate-400">Tell us what you need and we'll reply soon.</p>
        <form class="mt-6 grid gap-3 text-left" onsubmit="event.preventDefault();alert('Thanks! We will contact you soon.')">
          <input required placeholder="Your name" class="rounded-lg border border-slate-300 bg-white px-4 py-3 text-slate-900">
          <input required type="email" placeholder="Email" class="rounded-lg border border-slate-300 bg-white px-4 py-3 text-slate-900">
          <textarea rows="3" placeholder="Message" class="rounded-lg border border-slate-300 bg-white px-4 py-3 text-slate-900"></textarea>
          <button class="rounded-lg bg-{color}-600 px-6 py-3 font-semibold text-white hover:bg-{color}-700">Send</button>
        </form>
      </div>
    </section>
  </main>
  <footer class="px-6 py-8 text-center text-sm text-slate-500">&copy; {e(name)}. All rights reserved.</footer>
</body>
</html>"""
    return (f"Here's a complete, responsive website for **{name}** using Tailwind CSS. Save it as `index.html` and open it in your browser.\n\n"
            f"```html\n{page}\n```\n\nTell me the business name, colours, or sections you want changed and I'll update it.")
