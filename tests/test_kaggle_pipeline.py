"""Kaggle pipeline ka CPU par test: teacher ko nakli generator se badal kar data-building poori chalti hai."""
import importlib.util
import json
import os
import random
import re
from pathlib import Path

import pytest

from engine import trainprep as tp

ROOT = Path(__file__).resolve().parent.parent


def load_train(tmp_path):
    spec = importlib.util.spec_from_file_location("kaggle_train", ROOT / "kaggle" / "train.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.W = str(tmp_path)
    os.symlink(ROOT, tmp_path / "repo")
    return m


def fake_gen(msgs_list, max_new):
    out = []
    for m in msgs_list:
        u = m[-1]["content"]
        if u.startswith("Passage:"):
            p = u.split("Passage:\n", 1)[1].split("\n\nWrite ONE", 1)[0]
            first = re.split(r"(?<=[.!?])\s", p)[0]
            out.append(json.dumps({"question": f"What does the text say about {first.split()[0]} and related facts?", "answer": first}))
        elif re.search(r"bomb|ransomware|keylogger|meth|wifi|stalk|fake bank", u, re.I):
            out.append("Sorry, I can't help with that, but I can suggest a safe alternative.")
        elif "website" in u.lower() or "landing" in u.lower():
            out.append("```html\n<!doctype html><html><body>hi</body></html>\n```")
        else:
            out.append("Here is a helpful answer to your request, written clearly and warmly.")
    return out


def test_teacher_stage_builds_runtime_format_examples(tmp_path, monkeypatch):
    m = load_train(tmp_path)
    monkeypatch.setattr(m, "make_generator", lambda *a, **k: (fake_gen, object()))
    monkeypatch.setitem(__import__("sys").modules, "torch", type("T", (), {"cuda": type("C", (), {"empty_cache": staticmethod(lambda: None)})})())
    m.N_GROUNDED, m.N_DIRECT = 40, 60
    passages = [{"title": f"T{i}", "url": f"https://e.org/{i}", "passage": f"Alpha{i} is a fine topic about widgets and gadgets. It was described in {1900 + i}."} for i in range(60)]
    ex = m.teacher.__wrapped__(passages) if hasattr(m.teacher, "__wrapped__") else m.teacher(passages)
    assert ex and len(ex) > 50
    kinds = {e["kind"] for e in ex}
    assert kinds == {"grounded", "unknown", "direct"}
    g = next(e for e in ex if e["kind"] == "grounded")
    assert g["messages"][0]["content"].startswith("Neeche diye gaye SOURCES") and g["messages"][0]["content"].endswith("/no_think")
    assert re.search(r"\[[123]\]$", g["messages"][1]["content"])
    d = next(e for e in ex if e["kind"] == "direct")
    assert d["messages"][0]["role"] == "system" and "Talashly's assistant" in d["messages"][0]["content"]
    assert (tmp_path / "training_data.jsonl").exists()
    assert json.loads((tmp_path / "status.json").read_text())["stages"]["teacher"]["ok"]


def test_builders_and_filters():
    rng = random.Random(1)
    t = {"title": "Python", "url": "https://x.org/p", "passage": "Python was created by Guido van Rossum in 1991."}
    ds = [{"title": "Rain", "url": "https://y.org/r", "passage": "Rain falls from clouds."}]
    e = tp.grounded_example("who created Python?", "Guido van Rossum created it [9]", t, ds, rng)
    assert e["messages"][1]["content"].count("[") == 1
    assert tp.grounded("Guido van Rossum created Python", t["passage"])
    assert not tp.grounded("Napoleon invaded Russia in winter quickly", t["passage"])
    assert tp.unknown_example("Zorkan kya hai", ds)["messages"][1]["content"] == tp.NOT_FOUND["roman"]
    assert tp.parse_teacher_json("no json here") is None
    assert len(tp.direct_prompts(100)) > 60
    assert tp.lang_of("پاکستان کیا ہے") == "ur"


def test_export_passages_from_live_like_server(tmp_path, monkeypatch):
    import asyncio

    import httpx

    def handler(req: httpx.Request):
        if req.url.path == "/api/search":
            return httpx.Response(200, json={"results": [{"title": "Doc A", "url": "https://s.test/a", "snippet": "<mark>python</mark> " + "word " * 40}]})
        return httpx.Response(404)

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    n = asyncio.run(tp.export_passages("http://srv.test", 1, tmp_path, per_min=100000))
    assert n == 1
    row = json.loads((tmp_path / "passages.jsonl").read_text().splitlines()[0])
    assert row["url"] == "https://s.test/a" and "<mark>" not in row["passage"]
