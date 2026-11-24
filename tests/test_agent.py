"""Agent mode: planner, sandbox safety, tools, code verification, token economy."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from engine import agent as agent_mod
from engine import llm as llm_mod
from engine.sandbox import check, run_python
from tests.test_chat import build, chat, ev


# ------------------------------------------------------------------ sandbox
def test_sandbox_runs_good_code():
    r = run_python("import math\nprint(math.factorial(10))")
    assert r["ok"] and r["out"].strip() == "3628800"


@pytest.mark.parametrize("code", [
    "import os\nos.system('id')", "import subprocess", "open('/etc/passwd').read()", "().__class__.__bases__",
    "__import__('os')", "eval('1+1')", "exec('x=1')", "import socket", "from os import path", "getattr(1,'real')",
    "x = [].__len__", "import urllib.request", "global x",
])
def test_sandbox_blocks_dangerous_code(code):
    assert check(code) is not None
    assert run_python(code)["blocked"]


def test_sandbox_limits_time_and_memory():
    assert not run_python("while True: pass", timeout=6)["ok"]
    assert not run_python("x=[0]*10**9")["ok"]
    assert not run_python("print('a'*10)\nraise ValueError('boom')")["ok"]
    assert len(run_python("print('x'*100000)")["out"]) <= 2000


# ------------------------------------------------------------------ planner
def test_planner_zero_token_routing():
    assert "calc" in agent_mod.make_plan("what is 12*13")
    assert "clock" in agent_mod.make_plan("what is today's date and time")
    assert "url" in agent_mod.make_plan("summarize https://example.com/page")
    p = agent_mod.make_plan("write a python function that computes the 20th fibonacci number")
    assert "code" in p
    assert "search" in agent_mod.make_plan("explain how a bloom filter works")


def test_fetch_url_blocks_private_addresses():
    for u in ("http://127.0.0.1:8000/api/health", "http://localhost/x", "http://169.254.169.254/latest", "ftp://example.com/x", "http://10.0.0.5/"):
        r = asyncio.run(agent_mod.fetch_url(u))
        assert not r["ok"], u


# ------------------------------------------------------------------ end to end
def _fake(monkeypatch, calls, reply):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        sse = "".join("data: " + json.dumps({"choices": [{"delta": {"content": t}}]}) + "\n\n" for t in reply) + "data: [DONE]\n\n"
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})
    real = httpx.AsyncClient

    class Fake(real):
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **k)
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", Fake)


def _cfg_llm(cfg):
    cfg["llm"].update(enabled=True, base_url="https://llm.example", api_key="k", model="m1", path="/v1/chat/completions")


def test_agent_without_llm_answers_from_tools(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        evs = chat(c, "what is 12*13 and what is today's date", model="mus-7.01-agent")[1]
    steps = [d["msg"] for d in ev(evs, "step")]
    assert steps[0].startswith("Plan:") and any("calc" in s for s in steps)
    text = "".join(ev(evs, "token"))
    assert "156" in text and "UTC" in text
    assert ev(evs, "done")[0]["mode"] == "agent"


def test_agent_runs_and_verifies_code_one_llm_call(tmp_path, monkeypatch):
    calls = []
    reply = ["Here:\n```python\n", "def fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\n", "print(fib(20))\n```\nIterative."]
    _fake(monkeypatch, calls, reply)
    cfg, app = build(tmp_path)
    _cfg_llm(cfg)
    with TestClient(app) as c:
        evs = chat(c, "write a python function that computes the 20th fibonacci number", model="mus-7.01-agent")[1]
    text = "".join(ev(evs, "token"))
    assert "6765" in text and "Run check" in text
    d = ev(evs, "done")[0]
    assert d["agent"]["verified"] is True and len(calls) == 1
    assert d["agent"]["tokens"]["prompt"] < 400   # tokens kam


def test_agent_flags_bad_code_instead_of_hiding_it(tmp_path, monkeypatch):
    calls = []
    _fake(monkeypatch, calls, ["```python\nimport os\nprint(os.getcwd())\n```"])
    cfg, app = build(tmp_path)
    _cfg_llm(cfg)
    with TestClient(app) as c:
        evs = chat(c, "write a python script that prints the current folder", model="mus-7.01-agent")[1]
    text = "".join(ev(evs, "token"))
    assert "did not run cleanly" in text and "not allowed" in text


def test_agent_model_listed(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        ids = [m["id"] for m in c.get("/api/models").json()["models"]] if "models" in c.get("/api/models").json() else []
    assert "mus-7.01-agent" in ids or ids == []


def test_find_python_tolerates_missing_fence_and_language():
    f = agent_mod.find_python
    assert f("```python\nprint(1)\n```") == "print(1)"
    assert f("text\n```\nprint(2)\n```") == "print(2)"
    assert f("```python\nprint(3)") == "print(3)"      # band na hone wala fence
    assert f("```text\nhello world\n```") is None          # python nahi
    assert f("no code here") is None


def test_agent_survives_unclosed_code_fence(tmp_path, monkeypatch):
    calls = []
    _fake(monkeypatch, calls, ["```python\nprint('talashly'[::-1])"])
    cfg, app = build(tmp_path)
    _cfg_llm(cfg)
    with TestClient(app) as c:
        evs = chat(c, "write python that reverses the string talashly and prints it", model="mus-7.01-agent")[1]
    assert "ylhsalat" in "".join(ev(evs, "token"))


def test_agent_says_so_when_llm_is_unavailable_for_code(tmp_path):
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        evs = chat(c, "write a python function that computes the 20th fibonacci number", model="mus-7.01-agent")[1]
    t = "".join(ev(evs, "token"))
    assert "busy right now" in t
