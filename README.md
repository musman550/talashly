# Talashly v2

Apna khud ka search engine + RAG + AI-jawab system. Python mein, **koi third-party search/AI API nahi**.
Apna crawler, apna index (SQLite FTS5), apna "brain". Optional: apna local LLM (llama.cpp server).

## Windows par chalane ka tareeqa
1. `INSTALL.bat` (sirf pehli dafa; Python 3.11+ chahiye, "Add to PATH" tick ho)
2. `START.bat` -> browser mein http://127.0.0.1:8000
3. Dusre `.bat`: `DOCTOR.bat` (system check), `ADD_USER.bat` (user/API key), `INDEX_CODE.bat` (apna code index),
   `ASK.bat` (terminal se sawal), `TEST.bat` (tests), `AUTOSTART_ON/OFF.bat` (Windows ke saath auto-start),
   `RESTORE_BACKUP.bat` (backup se wapas).

## Kya kya hai
- **Crawler**: robots.txt aur Crawl-delay ka ehtram, per-domain delay, ETag/If-Modified-Since, sitemaps, dedupe,
  backpressure, timeout, kharab domain ki quarantine, adaptive recrawl. 24/7 ya `config.toml` ke mutabiq.
- **Index**: SQLite FTS5 (BM25 + taaza content + domain diversity). CJK/Thai ke liye trigram index.
- **Brain**: sawal ki qism (sawal/code/command) pehchanta hai, bare sawal todta hai, zarurat par live research
  (seeds + `sources.toml` ke direct sources) karta hai, jo jawab nahi mila usay "gap" mein likhta hai.
- **RAG jawab**: citations ke saath. Local LLM chalu ho to wo jawab likhta hai aur `ground_check` verify karta hai;
  warna extractive jawab (sirf index mein mojood text se).
- **Code search**: `code: fetch_user_profile` — apne folders index karke function/class/identifier search.
- **Self-healing**: crawler crash par restart (backoff), watchdog, DB integrity check, FTS rebuild, rotating backups,
  startup par kharab DB ho to latest valid backup se restore.
- **Multi-user**: hashed API keys, roles, rate limit, background tasks (research/crawl).
- **Zubanein**: Urdu, Arabic, Hindi, Russian, Chinese, Japanese, Korean, Thai, French, German, Spanish, English
  (tests mein 11+ zubanon par verify).

## Tested numbers (sandbox, 1 machine)
- 238 automated tests pass (pipeline, languages, crawler, code, brain, healing, API, CLI, DB).
- 60,000 chunks index: English multi-word search p50 ~85 ms; CJK 4+ chars ~3 ms; 2-char CJK ~70 ms.
- Local crawl ~89 pages/sec (apni test site par); ~20 MB per 1000 pages.

## Ehtiyaat / honest limits
- **Google/Bing/DuckDuckGo scrape nahi hote** (unki ToS mana karti hai). Sources apni `seeds.toml` / `sources.toml` hain — unhein badlein.
- Seeds ke asli URLs sandbox se verify nahi ho sake; pehli dafa `DOCTOR.bat` aur crawl logs dekhein.
- `.bat` files asli Windows par mujh se test nahi hui (static lint pass hai).
- JavaScript-rendered sites (Playwright) aur semantic/vector search abhi shamil nahi.
- "Zero bugs" ka dawa koi nahi kar sakta; tests ne jo bugs pakre wo theek kiye, aur mutation checks se tests ki quwwat parkhi gayi.
- Crawler sirf wahi data lata hai jo sites public rakhti hain; `/private` jaise disallowed paths kabhi index nahi hote.

## Docker
`docker compose up -d` (data `./data` mein; `python -m engine serve --host 0.0.0.0`).
