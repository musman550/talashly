"""Transcript mein mile masle: date/time, vague, FAQ, history gating, page edit, short facts."""
from engine.chat import _follow_up, _page_edit, clock, smalltalk
from engine.models import get_model
from engine.policy import EXPLAIN, is_vague, short_fact

M = get_model(None)


def test_date_is_deterministic_not_llm():
    r = smalltalk("what is today's date", M)
    assert r and "UTC" in r
    assert smalltalk("what time is it", M)
    assert clock("hello there", "hello there") is None
    assert smalltalk("what is the date of the french revolution", M) is None


def test_vague_and_faq():
    assert is_vague("tell me") and is_vague("Tell me something")
    assert not is_vague("tell me about france")
    assert "free" in smalltalk("is talashly free", M).lower()
    assert "stored" in smalltalk("do you store my chats", M).lower()
    assert smalltalk("why should i use you", M)
    assert smalltalk("what can't you do", M)


def test_history_only_for_followups():
    assert not _follow_up("create a website for a gym")
    assert not _follow_up("write a palindrome checker in python please now")  # fresh task
    assert _follow_up("and in javascript?")
    assert _follow_up("make it blue")


def test_page_edit_vs_new_site():
    h = [{"role": "assistant", "content": "```html\n<html></html>\n```"}]
    assert _page_edit("upgrade this website", h)
    assert not _page_edit("create a new website for a gym", h)
    assert not _page_edit("upgrade this website", [])


def test_short_fact_and_explain():
    assert short_fact("who is the president of france")
    assert not short_fact("what is the latest news")
    assert not short_fact("what is a bloom filter and how is it used in databases in detail please")
    assert EXPLAIN.search("how to create multi agents")
