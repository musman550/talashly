# Talashly

**A free, self-hosted AI answer engine with its own crawler, its own index and cited answers in many languages.**
No third-party search or AI APIs. Chat UI, five free `MUS 7.01` models, and an OpenAI-compatible API with free keys.

Website: https://musman550.github.io/talashly/ · Hosting guide: [HOSTING.md](HOSTING.md)

<!-- AUTO-SEO:START -->
> **Summary:** Talashly is a self-hosted AI answer engine with its own crawler and index, supporting multilingual queries and offering a chat UI.

**Keywords:** sqlite · self-hosted · openai-api · crawler · search-engine · multilingual · chat-ui · python

### FAQ

**How do I run Talashly on Windows?**  
To run Talashly on Windows, first execute INSTALL.bat once, then run START.bat and open http://127.0.0.1:8000 in your browser.

**What models does Talashly support?**  
Talashly supports five free MUS 7.01 models: balanced, flash (fastest), deep (multi-step research), code (prioritizes code), and polyglot (non-English).

**How do I use the Talashly API?**  
To use the Talashly API, you can generate a key with a POST request to /api/keys and then use that key to make requests to the /v1/chat/completions endpoint.

### Related projects
- [talashly-monitor](https://github.com/musman550/talashly-monitor)
- [Website](https://musfiraai.com)

<sub>Generated automatically from this repository's own content. [How this works](https://musman550.github.io/github-traffic-analytics-dashboard/optimizer/)</sub>
<!-- AUTO-SEO:END -->

## What you get
- **Chat website** (Claude-style): streaming answers, conversation history, model picker, dark/light, mobile-ready.
- **Own crawler**: robots.txt and crawl-delay aware, sitemaps, ETag, dedupe, adaptive recrawl, 24/7 or time windows.
- **Own index**: SQLite FTS5 (BM25 + freshness + domain diversity) with a trigram index for Chinese/Japanese/Korean/Thai.
- **Grounded answers**: sentences come from indexed pages. Sources, citations and research steps are hidden by default and shown only for the Deep model or when the user asks ("with sources", "deep research"). No evidence means it says so; it never invents.
- **Models (all free)**: `mus-7.01` (balanced), `-flash` (fastest), `-deep` (multi-step research), `-code` (your code first), `-polyglot` (non-English).
  These are answer-engine profiles over the same index, not separately trained neural networks. A local LLM (llama.cpp) can be attached to write the text; every sentence is still verified against the sources.
- **API**: `POST /v1/chat/completions` (OpenAI format, streaming), `GET /v1/models`, `POST /api/keys` (free self-serve keys), plus `/api/search`, `/api/code`, `/api/chat`.
- **Human-like conversation**: greetings, thanks and "who are you" are answered directly (honestly: it is an AI), follow-ups ("why is that important?") use the previous question, replies match the question's language (English / Roman Urdu / Urdu).
- **Protection from overload**: per-key rate limits, daily quotas, a concurrency cap with a waiting queue and a clean "server busy" reply.
- **Self-healing**: crawler supervisor with restarts, DB integrity checks, rotating backups, automatic restore.
- **SEO / AEO / GEO**: canonical + Open Graph + Twitter tags, JSON-LD (WebApplication, WebSite SearchAction, FAQPage, TechArticle), `robots.txt` (AI crawlers allowed), `sitemap.xml`, `llms.txt`, web manifest.
- **Code search**: index your own folders and search functions, classes and identifiers.

## Run on Windows
1. `INSTALL.bat` once (Python 3.11+, tick "Add to PATH").
2. `START.bat`, then open http://127.0.0.1:8000
3. Others: `DOCTOR.bat`, `ADD_USER.bat`, `INDEX_CODE.bat`, `ASK.bat`, `TEST.bat`, `AUTOSTART_ON/OFF.bat`, `RESTORE_BACKUP.bat`.

Linux/macOS: `pip install -r requirements.txt && python -m engine serve`. Docker: `docker compose up -d`.

## Use the API
```bash
curl http://127.0.0.1:8000/api/keys -X POST -H "Content-Type: application/json" -d '{"label":"my-app"}'
curl http://127.0.0.1:8000/v1/chat/completions -H "Authorization: Bearer YOUR_KEY" -H "Content-Type: application/json" \
  -d '{"model":"mus-7.01","messages":[{"role":"user","content":"What is a polite crawler?"}]}'
```
Works with the OpenAI SDK (`base_url="http://127.0.0.1:8000/v1"`) and no-code tools that accept a custom OpenAI base URL.

## Tested (in a sandbox, one machine)
- 300+ automated tests: crawler, languages (11+), code search, brain, self-healing, API, chat, OpenAI endpoint, quotas, concurrency, SEO files, and a real-Chromium end-to-end UI suite (needs Playwright; skipped otherwise). Mutation checks confirm the tests catch broken logic.
- Chat load test, 400 requests with 80 at once on one process: all answered, ~100 requests/s, p95 about 0.9 s.
- Search over 60,000 chunks: English p50 ~85 ms, CJK 4+ chars ~3 ms.

## Honest limits
- No one can promise "zero bugs" or never-wrong answers. The design prevents invention (answers only from sources, citations, "no answer" when evidence is missing), but sources themselves can be wrong or outdated.
- It does **not** scrape Google, Bing or DuckDuckGo (their terms forbid it). Choose sources in `seeds.toml` / `sources.toml`; seed URLs could not be verified from the build sandbox.
- JavaScript-rendered sites and semantic/vector search are not included yet.
- `.bat` launchers were not run on a real Windows machine (lint-tested only).
- GitHub Pages cannot run the Python server; see [HOSTING.md](HOSTING.md) for free ways to put the app online.
- SEO files and structured data follow best practice, but no one can guarantee search rankings.

License: MIT.


## Smart layer, private chat and optional free AI writer
- **Private by default**: the browser keeps the conversation in memory only and sends the last few turns; the server saves no chat text. Refresh = clean slate. (`[chat] private_default = false` brings back saved history.) Only unanswered questions are noted anonymously so the crawler can learn what is missing.
- **Website builder** (no AI key needed): "make a website for Noor Bakery" returns a complete responsive Tailwind CSS page.
- **Optional AI writer** (any OpenAI-compatible provider, e.g. Groq, Gemini, OpenRouter, or your own llama.cpp): set `SS_LLM_URL`, `SS_LLM_KEY`, `SS_LLM_MODEL` (and `SS_LLM_PATH` if the path is not `/v1/chat/completions`) in `.env`. Then tasks (write, code, translate...) and questions the index cannot answer go to the AI writer; Deep model still uses research + sources.

## No third-party APIs
Outbound traffic is only: your crawler fetching public pages (polite, robots.txt), live research on the public sites in `sources.toml`, and the optional LLM URL *you* configure (your own llama.cpp). There are no API keys for search or AI, so no vendor quota can block you. Public sites can still rate-limit the crawler; it backs off automatically.


## Autopilot (khud test + khud mushkil hota level)

`python -m engine autopilot` (Docker mein `autopilot` service 24/7 chalti hai; GitHub Actions `autopilot` har ghante live site ko test karta hai).
Sawal index ke asli text se khud bante hain; har level pichle se mushkil (typos, filler, mixed language, compare, follow-up, injection...).
95% pass hone par level badhta hai. Yaad `data/autopilot/memory.db`, report `data/autopilot/report.md` (admin: `GET /admin/autopilot`),
fail sawal `failures.jsonl` (training/fixing ke liye). Optional LLM sawal-saaz: `AP_LLM_URL`, `AP_LLM_KEY`, `AP_LLM_MODEL` (koi bhi OpenAI-compatible).
