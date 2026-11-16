# Talashly

**A free, self-hosted AI answer engine with its own crawler, its own index and cited answers in many languages.**
No third-party search or AI APIs. Chat UI, five free `MUS 7.01` models, and an OpenAI-compatible API with free keys.

Website: https://musman550.github.io/talashly/ · Hosting guide: [HOSTING.md](HOSTING.md)

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
