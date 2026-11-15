"""Har zaban mein: crawl -> index -> search -> jawab. Sab pages sirf sitemap se milte hain (kahin link nahi)."""
import asyncio

import pytest

from engine.brain import Brain
from engine.extract import chunk_text, weight
from engine.search import Searcher, build_answer, detect_script, terms
from tests.helpers import collect
from tests.site import LANGS

SCRIPT = {"ur": "urdu/arabic", "ar": "urdu/arabic", "hi": "devanagari", "ru": "cyrillic", "zh": "cjk", "ja": "cjk",
          "fr": "latin", "de": "latin", "th": "thai", "mixed": "cjk", "ko": "cjk", "es": "latin"}


@pytest.mark.parametrize("code", list(LANGS))
def test_search_finds_right_page_in_every_language(crawled, code):
    cfg, db, site = crawled
    hits = Searcher(db, cfg).search(LANGS[code][2])
    assert hits, f"{code}: koi result nahi mila (query={LANGS[code][2]!r})"
    assert hits[0].url.endswith(f"/ml/{code}"), [h.url for h in hits[:4]]


@pytest.mark.parametrize("code", list(LANGS))
def test_script_detection(code):
    assert detect_script(LANGS[code][1]) == SCRIPT[code]


def test_two_character_chinese_query_uses_like_fallback(crawled):
    cfg, db, site = crawled
    hits = Searcher(db, cfg).search("协程")
    assert hits and hits[0].url.endswith("/ml/zh")


def test_accents_fold_both_directions(crawled):
    cfg, db, site = crawled
    s = Searcher(db, cfg)
    assert s.search("résumé café")[0].url.endswith("/ml/fr")
    assert s.search("resume cafe")[0].url.endswith("/ml/fr")


def test_no_cross_language_leakage(crawled):
    cfg, db, site = crawled
    hits = Searcher(db, cfg).search("کریولر معلومات")
    assert hits and all(h.url.endswith("/ml/ur") for h in hits)


def test_mixed_script_tokens_are_split():
    assert terms("使用Python开发 web") == ["使用", "python", "开发", "web"]


@pytest.mark.parametrize("code", ["ur", "zh", "ja", "hi", "ru", "th"])
def test_extractive_answer_is_native_language_and_cited(crawled, code):
    cfg, db, site = crawled
    q = LANGS[code][2]
    hits = Searcher(db, cfg).search(q)
    ans = build_answer(q, hits)
    assert ans["answer"], f"{code}: jawab khali"
    assert "[1]" in ans["answer"]
    core = LANGS[code][1].rstrip("۔。.").split()[0][:6]
    assert core in ans["answer"]


def test_brain_end_to_end_urdu_question(crawled):
    cfg, db, site = crawled
    brain = Brain(cfg, db, Searcher(db, cfg), [])
    r = asyncio.run(collect(brain, "کریولر معلومات کیسے جمع کرتا ہے؟"))
    assert r["plan"]["script"] == "urdu/arabic"
    assert r["sources"] and r["sources"][0]["url"].endswith("/ml/ur")
    assert "[1]" in r["text"]


@pytest.mark.parametrize("name,text", [
    ("en", "\n".join(f"Sentence number {i} has several words in it to count properly." for i in range(150))),
    ("zh", "\n".join("异步编程是一种并发模型，事件循环在单个线程上协作地调度协程。" * 3 for _ in range(60))),
    ("ja", "\n".join("検索エンジンはウェブページをインデックスして情報を収集します。" * 3 for _ in range(60))),
    ("ur", "\n".join("یہ ایک مثال ہے کہ کریولر کیسے کام کرتا ہے اور ڈیٹا کیسے جمع کرتا ہے۔ " * 4 for _ in range(60))),
    ("th", "\n".join("โปรแกรมค้นหาจัดทำดัชนีหน้าเว็บและรวบรวมข้อมูลจากอินเทอร์เน็ต" * 4 for _ in range(60))),
    ("nopunct", "word " * 3000),
])
def test_chunking_is_bounded_and_lossless_in_every_script(name, text):
    ch = chunk_text(text)
    assert len(ch) > 3, name
    assert max(weight(c) for c in ch) <= 180 * 1.7, (name, max(weight(c) for c in ch))
    # koi hissa ghaib nahi: aakhri aur pehli line kisi chunk mein maujood
    assert text.split("\n")[0].strip()[:20] in ch[0]
    assert text.split("\n")[-1].strip()[-15:] in ch[-1] or name == "nopunct"
