import asyncio
import json

import pytest

from engine.brain import Brain
from engine.crawler import Crawler
from engine.db import DB
from engine.search import Hit, Searcher, build_answer, coverage, ground_check, terms
from tests.helpers import collect, make_cfg, seed_docs
from tests.site import FakeLLM, Site

CRAWLER_TXT = ("A polite crawler obeys robots.txt and waits between requests to the same host. "
               "It identifies itself with a clear user agent and respects the crawl delay that every site declares. ") * 4
ASYNC_TXT = ("The asyncio event loop schedules coroutines cooperatively on a single thread without blocking. "
             "Awaiting a coroutine yields control back to the loop so other tasks can run in the meantime. ") * 4


@pytest.fixture()
def env(tmp_path):
    cfg = make_cfg(tmp_path)
    db = DB(cfg["general"]["db_path"])
    seed_docs(db, [("http://t.test/crawler", "Understanding web crawlers", CRAWLER_TXT),
                   ("http://t.test/asyncio", "Python asyncio explained", ASYNC_TXT)])
    return cfg, db, Brain(cfg, db, Searcher(db, cfg), [])


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- planning
def test_decompose_splits_complex_questions_and_keeps_original_first(env):
    cfg, db, b = env
    subs = b.decompose("compare asyncio vs threading for crawlers, and explain robots.txt")
    assert subs[0].startswith("compare asyncio")
    assert any("threading" in s for s in subs) and any("robots.txt" in s for s in subs)
    assert len(subs) <= 4
    assert b.decompose("salt and pepper") == ["salt and pepper"]      # chhota sawal: na todo
    assert b.decompose("python asyncio", deep=True) == ["python asyncio"]


def test_decompose_urdu_and_chinese_connectors(env):
    cfg, db, b = env
    assert len(b.decompose("پاکستان کی معیشت اور بھارت کی معیشت کا موازنہ کریں")) >= 2
    assert len(b.decompose("异步编程是什么，线程和协程的区别是什么，如何使用")) >= 2


def test_grounded_answer_with_citations(env):
    cfg, db, b = env
    r = run(collect(b, "what does a polite crawler do"))
    assert r["done"]["mode"] == "extractive" and "robots.txt" in r["text"] and "[1]" in r["text"]
    assert r["sources"][0]["url"].endswith("/crawler")
    assert r["done"]["coverage"] >= 0.6


def test_unknown_question_gives_no_fake_answer_and_records_gap(env):
    cfg, db, b = env
    r = run(collect(b, "zzyzx frobnicate wibblewobble"))
    assert r["sources"] == [] and "good answer" in r["text"].lower()
    assert db.q("SELECT n,resolved FROM gaps WHERE q LIKE 'zzyzx%'")[0]["n"] == 1
    run(collect(b, "zzyzx frobnicate wibblewobble"))
    assert db.q("SELECT n FROM gaps WHERE q LIKE 'zzyzx%'")[0]["n"] == 2


# ---------------------------------------------------------------- gap -> learn -> resolve
def test_gap_resolves_when_content_arrives_later(env):
    cfg, db, b = env
    run(collect(b, "what is photosynthesis chlorophyll"))
    assert db.q("SELECT resolved FROM gaps")[0]["resolved"] == 0
    seed_docs(db, [("http://t.test/bio", "Photosynthesis", "Photosynthesis is how chlorophyll in plants converts light into chemical energy. " * 5)])
    assert run(b.work_gap()) is not None
    assert db.q("SELECT resolved FROM gaps")[0]["resolved"] == 1
    assert run(b.work_gap()) is None  # ab kuch baqi nahi


def test_gap_gives_up_after_retries(env):
    cfg, db, b = env
    run(collect(b, "completely unknowable xyzzy plugh"))
    for _ in range(5):
        run(b.work_gap())
    assert db.q("SELECT tries,resolved FROM gaps")[0]["tries"] == cfg["brain"]["gap_retries"]
    assert db.q("SELECT resolved FROM gaps")[0]["resolved"] == 0


# ---------------------------------------------------------------- live research
def test_live_research_fetches_missing_topic_then_serves_from_index(tmp_path):
    with Site() as site:
        cfg = make_cfg(tmp_path)
        db = DB(cfg["general"]["db_path"])
        seed_docs(db, [("http://t.test/asyncio", "Python asyncio explained", ASYNC_TXT)])
        sources = [{"kind": "direct", "script": "latin", "url": site.url + "/wiki/{title}"}]
        b = Brain(cfg, db, Searcher(db, cfg), sources)
        crawler = Crawler(cfg, [])
        b.get_crawler = lambda: crawler

        async def go():
            try:
                r1 = await collect(b, "quantum computing qubits")
                first_hits = site.stats.get("/wiki/Quantum_computing", 0)
                r2 = await collect(b, "quantum computing qubits")
                return r1, r2, first_hits
            finally:
                await crawler.aclose()

        r1, r2, first_hits = run(go())
        assert any("new pages" in s for s in r1["steps"]), r1["steps"]
        assert r1["sources"] and r1["sources"][0]["url"].endswith("/wiki/Quantum_computing")
        assert "qubits" in r1["text"] and "[1]" in r1["text"]
        assert first_hits == 1
        assert site.stats.get("/wiki/Quantum_computing", 0) == first_hits  # dusri baar live fetch nahi
        assert not r2["steps"]
        # galat guesses (404) frontier ko gandha nahi karte
        bad = [site.url + "/wiki/" + t for t in ("Quantum_computing_qubits", "Quantum_Computing_Qubits", "Quantum")]
        assert not db.q(f"SELECT url FROM frontier WHERE url IN ({','.join('?' * len(bad))})", bad)
        assert db.q("SELECT status FROM frontier WHERE url=?", (site.url + "/wiki/Quantum_computing",))[0]["status"] == "done"
        assert not db.q("SELECT 1 FROM gaps")  # jawab mil gaya: gap nahi


def test_live_research_failure_is_graceful(tmp_path):
    with Site() as site:
        cfg = make_cfg(tmp_path)
        db = DB(cfg["general"]["db_path"])
        sources = [{"kind": "direct", "url": site.url + "/wiki/{title}"}]
        b = Brain(cfg, db, Searcher(db, cfg), sources)
        crawler = Crawler(cfg, [])
        b.get_crawler = lambda: crawler

        async def go():
            try:
                return await collect(b, "nothingness voidness emptiness")
            finally:
                await crawler.aclose()

        r = run(go())
        assert r["sources"] == [] and r["done"]["mode"] == "none"
        assert db.q("SELECT COUNT(*) FROM gaps")[0][0] == 1
        assert not db.q("SELECT 1 FROM frontier WHERE status='error'")


def test_direct_url_candidates_wikipedia_style(env):
    cfg, db, b = env
    b.sources = [{"kind": "direct", "script": "latin", "url": "https://en.wikipedia.org/wiki/{title}"},
                 {"kind": "direct", "script": "urdu/arabic", "url": "https://ur.wikipedia.org/wiki/{title}"}]
    urls = b._direct_urls(terms("quantum computing qubits"), "latin")
    assert urls[0].endswith("/Quantum_computing_qubits") and all("ur.wikipedia" not in u for u in urls)
    assert any(u.endswith("/Quantum_computing") for u in urls)
    ur = b._direct_urls(terms("پاکستان معیشت"), "urdu/arabic")
    assert ur and all("ur.wikipedia" in u for u in ur) and "%" in ur[0]  # unicode percent-encoded


# ---------------------------------------------------------------- admin commands
def test_commands_are_admin_only(env):
    cfg, db, b = env
    r = run(collect(b, "/add https://example.org/feed.xml", role="admin"))
    assert r["plan"]["intent"] == "command" and "Add kar diya" in r["text"]
    row = db.q("SELECT kind,status FROM frontier WHERE url='https://example.org/feed.xml'")[0]
    assert row["kind"] == "feed"
    assert db.q("SELECT 1 FROM allowed_domains WHERE domain='example.org'")
    # normal user wahi likhe to sirf sawal samjha jata hai, kuch add nahi hota
    r2 = run(collect(b, "/add https://evil.example/x", role="user"))
    assert r2["plan"]["intent"] != "command"
    assert not db.q("SELECT 1 FROM frontier WHERE url LIKE '%evil.example%'")


def test_status_gaps_log_and_bad_input_commands(env):
    cfg, db, b = env
    assert "Pages: 2" in run(collect(b, "/status", role="admin"))["text"]
    run(collect(b, "unknown thing qqq"))
    assert "unknown thing qqq" in run(collect(b, "/gaps", role="admin"))["text"]
    assert run(collect(b, "/log", role="admin"))["text"]
    assert "Format" in run(collect(b, "/add notaurl", role="admin"))["text"]
    assert "Format" in run(collect(b, "/index", role="admin"))["text"]


# ---------------------------------------------------------------- verification of LLM output
def _hits():
    return [Hit(1, "http://t.test/crawler", "Crawlers", "t.test", None, 0, 1.0, [CRAWLER_TXT[:300]])]


def test_ground_check_drops_unsupported_and_uncited_sentences():
    text = ("A polite crawler obeys robots.txt and waits between requests [1]. "
            "The moon is made of green cheese and aliens built it [1]. "
            "Bananas are blue. "
            "Fabricated citation to a source that does not exist [7].")
    clean, dropped = ground_check(text, _hits())
    assert "robots.txt" in clean and "cheese" not in clean and "Bananas" not in clean and "[7]" not in clean
    assert dropped == 3


def test_ground_check_keeps_fully_grounded_text():
    text = "A polite crawler obeys robots.txt and waits between requests to the same host [1]."
    clean, dropped = ground_check(text, _hits())
    assert dropped == 0 and clean == text


def test_coverage_measures_how_much_of_the_question_is_answered(env):
    cfg, db, b = env
    s = Searcher(db, cfg)
    assert coverage("polite crawler robots", s.search("polite crawler robots")) >= 0.9
    q = "polite crawler giraffes zeppelins"
    assert coverage(q, s.search(q)) < 0.6


# ---------------------------------------------------------------- local LLM scenarios
def _llm_env(env, llm_url, enabled=True):
    cfg, db, b = env
    cfg["llm"].update(enabled=enabled, base_url=llm_url)
    return b


def test_llm_good_answer_passes_verification_untouched(env):
    good = "A polite crawler obeys robots.txt and waits between requests to the same host [1]."
    llm = FakeLLM(good)
    try:
        r = run(collect(_llm_env(env, llm.url), "what does a polite crawler do"))
    finally:
        llm.close()
    assert r["done"]["mode"] == "llm" and "replace" not in r["events"]
    assert r["text"].strip() == good


def test_llm_hallucination_is_removed_and_user_sees_only_grounded_text(env):
    bad = ("A polite crawler obeys robots.txt and waits between requests to the same host [1]. "
           "The moon is made of green cheese and aliens built it [1]. Bananas are blue.")
    llm = FakeLLM(bad)
    try:
        r = run(collect(_llm_env(env, llm.url), "what does a polite crawler do"))
    finally:
        llm.close()
    assert "replace" in r["events"]
    assert "robots.txt" in r["text"] and "cheese" not in r["text"] and "Bananas" not in r["text"]
    assert any(x["kind"] == "verify" for x in map(dict, env[1].q("SELECT kind FROM brain_log")))


def test_llm_all_garbage_falls_back_to_extractive(env):
    llm = FakeLLM("Totally unrelated nonsense without any citation at all")
    try:
        r = run(collect(_llm_env(env, llm.url), "what does a polite crawler do"))
    finally:
        llm.close()
    assert "nonsense" not in r["text"] and "robots.txt" in r["text"] and "[1]" in r["text"]


def test_llm_says_sources_dont_cover_it_then_answers_directly_not_extractive_garbage(env):
    llm = FakeLLM("The sources do not contain this information.", direct="A polite crawler is one that respects the rules of a site.")
    try:
        r = run(collect(_llm_env(env, llm.url), "what does a polite crawler do"))
    finally:
        llm.close()
    assert r["done"]["mode"] == "llm-direct"
    assert "respects the rules" in r["text"] and "do not contain" not in r["text"] and "robots.txt" not in r["text"]


def test_llm_crash_mid_stream_recovers_with_extractive_answer(env):
    llm = FakeLLM("A polite crawler obeys robots.txt and waits between requests to the same host [1].", mode="crash")
    try:
        r = run(collect(_llm_env(env, llm.url), "what does a polite crawler do"))
    finally:
        llm.close()
    assert r["done"]["mode"] == "extractive"
    assert "robots.txt" in r["text"] and "[1]" in r["text"]
    assert r["text"].count("A polite crawler obeys") <= 1  # adhoora LLM text + extractive dohra nahi
    assert db_logged(env[1], "heal")


def test_llm_dead_server_uses_extractive_without_error(env):
    r = run(collect(_llm_env(env, "http://127.0.0.1:1"), "what does a polite crawler do"))
    assert r["done"]["mode"] == "extractive" and "robots.txt" in r["text"]


def db_logged(db, kind):
    return bool(db.q("SELECT 1 FROM brain_log WHERE kind=?", (kind,)))


# ---------------------------------------------------------------- hostile input
@pytest.mark.parametrize("q", ['"', "'", "(", ")", "*", "AND", "OR NOT", "NEAR(a b)", "a OR", "-", "--", "%", "_", "\\",
                               "💥💥", "\u0000", "'; DROP TABLE docs;--", "<script>alert(1)</script>", " ", "?", "،،،",
                               "the", "کیا ہے", "a" * 500, "word " * 200, "\n\n\n", "{{7*7}}", "../../etc/passwd"])
def test_hostile_questions_never_crash_or_damage(env, q):
    cfg, db, b = env
    r = run(collect(b, q))
    assert r["done"] is not None
    assert db.q("SELECT COUNT(*) FROM docs")[0][0] == 2
    assert db.integrity()["ok"]
