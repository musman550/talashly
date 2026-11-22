from engine.search import CTX_CHARS, CTX_HITS, Hit, ground_check, llm_prompt


def _many(n=8):
    return [Hit(i, f"http://t{i}.test/p", f"Title{i}", f"t{i}.test", None, 0, 1.0, ["word " * 400, "more " * 400]) for i in range(1, n + 1)]


def test_llm_prompt_is_compact_for_fast_first_token():
    p = llm_prompt("what is x", _many())
    assert p.count("\n[") + (1 if "SOURCES:\n[1]" in p else 0) >= CTX_HITS - 1
    assert "[4]" not in p and f"Title{CTX_HITS + 1}" not in p
    assert len(p) < 600 + CTX_HITS * (CTX_CHARS + 80)   # ~1000 tokens se kam: CPU par 5-10 s prefill


def test_ground_check_only_trusts_cited_sources_in_prompt():
    hits = _many()
    clean, dropped = ground_check("word word word [1]. word word word [5].", hits)   # [5] prompt mein tha hi nahi
    assert "[5]" not in clean and dropped == 1
