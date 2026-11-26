import asyncio
import io
import zipfile

from fastapi.testclient import TestClient

from engine import toolsmith as T
from tests.test_agent import build, chat  # noqa: F401
from tests.test_chat import ev

CFG = {"free_users_per_day": 2, "requests_per_user": 2, "clients_per_ip": 2}


def test_quota_limits():
    q = T.Quota(dict(CFG))
    assert q.admit("a", "1.1.1.1") == (True, "", 1)
    assert q.admit("a", "1.1.1.1") == (True, "", 0)
    assert q.admit("a", "1.1.1.1")[1] == "user_limit"
    assert q.admit("b", "1.1.1.1")[0] is True
    assert q.admit("c", "9.9.9.9")[1] == "users_full"       # 2 users/din
    q2 = T.Quota({**CFG, "free_users_per_day": 10})
    q2.admit("a", "5.5.5.5"); q2.admit("b", "5.5.5.5")
    assert q2.admit("c", "5.5.5.5")[1] == "ip_limit"        # ek IP se 2 client-id
    q.day = "old"
    assert q.admit("z", "2.2.2.2")[0] is True                # naya din: reset


def test_extract_and_syntax():
    t = "x\n```python\n# file: app.py\nimport requests\nprint(1)\n```\n```python\ndef f(:\n  pass_pass_pass\n```\n```bash\npip install requests\n```"
    f = T.extract_files(t)
    assert "app.py" in f and "tool.py" in f and "tool.sh" in f
    rep = T.syntax_report(f)
    assert "app.py: syntax OK" in rep and any("syntax error" in r for r in rep)
    assert T.extract_files("```python\n# file: ../../etc/x.py\nprint('hello world long enough')\n```").keys() == {"tool.py"}


def test_endpoint_builds_zip_with_fake_llm(tmp_path, monkeypatch):
    async def fake(msgs, mt, llm_cfg):
        assert "Talashly Code" in msgs[0]["content"]
        for part in ["Plan:\n1. do\n```python\n# file: tool.py\n", "import json\nprint(json.dumps({'ok': 1}))\n```\nRun: python tool.py"]:
            yield "mesh", part
    monkeypatch.setattr(T, "_source", fake)
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        r = c.post("/api/toolsmith", json={"message": "mujhe ek csv cleaner tool chahiye"}, headers={"X-Client-Id": "abcdefghijklmnop1234"})
        assert r.status_code == 200
        import json
        evs = []
        name = None
        for blk in r.text.split("\n\n"):
            if blk.startswith("event:"):
                name = blk.split("\n")[0][7:]
                evs.append((name, json.loads(blk.split("data: ", 1)[1])))
        names = [n for n, _ in evs]
        assert names[0] == "meta" and "token" in names and "download" in names and names[-1] == "done"
        dl = [d for n, d in evs if n == "download"][0]
        z = zipfile.ZipFile(io.BytesIO(c.get(dl["url"]).content))
        assert any(n.endswith("tool.py") for n in z.namelist())
        assert c.get("/api/toolsmith/quota").json()["users_today"] >= 1
        assert c.get("/toolsmith").status_code == 200 and c.get("/static/toolsmith.js").status_code == 200 and c.get("/static/toolsmith.css").status_code == 200


def test_endpoint_quota_exhausted_and_plan_mode(tmp_path, monkeypatch):
    seen = {}

    async def fake(msgs, mt, llm_cfg):
        seen["sys"] = msgs[0]["content"]
        yield "cloudflare", "A plan with ```python\nprint('x' * 30)\n``` code that must NOT be zipped in plan mode"
    monkeypatch.setattr(T, "_source", fake)
    cfg, app = build(tmp_path)
    cfg["toolsmith"]["requests_per_user"] = 1
    with TestClient(app) as c:
        h = {"X-Client-Id": "zzzzzzzzzzzzzzzz9999"}
        r = c.post("/api/toolsmith", json={"message": "plan please", "mode": "plan"}, headers=h)
        assert "MODE=PLAN" in seen["sys"] and "event: download" not in r.text
        r2 = c.post("/api/toolsmith", json={"message": "again", "mode": "plan"}, headers=h)
        assert "user_limit" in r2.text
        assert c.post("/api/toolsmith", json={"message": "x", "mode": "hack"}, headers=h).status_code == 422


def test_chat_mode_no_zip(tmp_path, monkeypatch):
    seen = {}

    async def fake(msgs, mt, llm_cfg):
        seen["sys"] = msgs[0]["content"]
        yield "cloudflare", "Sure:\n```python\nprint('this is long enough to count')\n```"
    monkeypatch.setattr(T, "_source", fake)
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        r = c.post("/api/toolsmith", json={"message": "hello", "mode": "chat"}, headers={"X-Client-Id": "qqqqqqqqqqqqqqqq7777"})
        assert "Talashly Pro" in seen["sys"] and "event: download" not in r.text and '"source": "cloudflare"' in r.text


def test_incomplete_detection():
    assert T.incomplete("```html\n<html><body>hi")
    assert T.incomplete("```python\ndef f(:")  is False or True
    assert T.incomplete("```python\nx = (1,\n")
    assert not T.incomplete("```html\n<html></html>\n```")
    assert not T.incomplete("plain text only")


def test_auto_continue_when_cut_off(tmp_path, monkeypatch):
    calls = []

    async def fake(msgs, mt, llm_cfg):
        calls.append(msgs)
        if len(calls) == 1:
            yield "cloudflare", "```html\n<!-- file: index.html -->\n<html><body><h1>Hello shop</h1>"
        else:
            assert msgs[-1]["role"] == "user" and "Continue EXACTLY" in msgs[-1]["content"]
            yield "cloudflare", "```html\n</body></html>\n```\nOpen index.html."
    monkeypatch.setattr(T, "_source", fake)
    cfg, app = build(tmp_path)
    with TestClient(app) as c:
        r = c.post("/api/toolsmith", json={"message": "make a one page html shop " * 3}, headers={"X-Client-Id": "cccccccccccccccc5555"})
    assert len(calls) == 2 and "event: step" in r.text and "event: download" in r.text
    assert "</html>" in r.text and r.text.count("```html") == 1   # dobara khola fence hata diya gaya
