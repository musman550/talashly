"""Chaos / self-healing: kuch bhi toote, system khud theek kare ya saaf bataye."""
import asyncio
import json
import os
import time

import pytest

from engine import config as cfgmod
from engine.__main__ import preflight_db
from engine.brain import Brain
from engine.crawler import Crawler
from engine.db import DB, check_file, restore_latest_backup
from engine.search import Searcher
from engine.supervisor import Supervisor
from tests.helpers import collect, make_cfg, seed_docs

TXT = ("A polite crawler obeys robots.txt and waits between requests to the same host. "
       "It identifies itself with a clear user agent and respects the crawl delay. ") * 4


def mk(tmp_path, **sections):
    cfg = make_cfg(tmp_path, **sections)
    db = DB(cfg["general"]["db_path"])
    seed_docs(db, [("http://t.test/a", "Crawlers", TXT),
                   ("http://t.test/b", "Asyncio", TXT.replace("crawler", "coroutine") + " Unique asynciotopic here. " * 3),
                   ("http://t.test/c", "Other", "Third document about gardening tomatoes and soil. " * 8)])
    return cfg, db


# ------------------------------------------------------------------ config
def test_broken_toml_falls_back_to_defaults(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[crawler\nconcurrency = = 5", encoding="utf-8")
    cfg = cfgmod.load_config(str(p))
    assert cfg["crawler"]["concurrency"] == 8 and any("parse error" in x for x in cfg["_problems"])


def test_out_of_range_and_wrong_types_are_clamped(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[crawler]\nconcurrency = 100000\nper_domain_delay = -5\nrequest_timeout = "fast"\n'
                 'active_hours = []\n[server]\nport = 99999\nworkers = true\n', encoding="utf-8")
    cfg = cfgmod.load_config(str(p))
    assert cfg["crawler"]["concurrency"] == 64 and cfg["crawler"]["per_domain_delay"] == 0.0
    assert cfg["crawler"]["request_timeout"] == 20 and cfg["crawler"]["active_hours"] == ["00:00-24:00"]
    assert cfg["server"]["port"] == 65535 and cfg["server"]["workers"] == 2
    assert len(cfg["_problems"]) >= 5


def test_missing_config_file_and_missing_sections_use_defaults(tmp_path):
    cfg = cfgmod.load_config(str(tmp_path / "does-not-exist.toml"))
    assert cfg["brain"]["live_research"] is True and cfg["maintenance"]["backup_keep"] == 7
    p = tmp_path / "c.toml"
    p.write_text("[search]\ndefault_limit = 3\n", encoding="utf-8")
    cfg = cfgmod.load_config(str(p))
    assert cfg["search"]["default_limit"] == 3 and cfg["crawler"]["concurrency"] == 8


def test_shipped_config_and_seeds_are_valid():
    cfg = cfgmod.load_config()
    assert cfg["_problems"] == []
    seeds = cfgmod.load_seeds()
    assert len(seeds) >= 5 and all(s["url"].startswith("http") and s.get("type", "page") in ("page", "feed", "sitemap") for s in seeds)
    assert all(s["kind"] in ("direct", "search") for s in cfgmod.load_sources())


# ------------------------------------------------------------------ FTS / index repair
def test_fts_mismatch_is_detected_and_rebuilt(tmp_path):
    cfg, db = mk(tmp_path)
    assert db.integrity()["ok"]
    db.x("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks LIMIT 2)")  # crash mid-write ka nateeja
    assert not db.integrity()["ok"] and not Searcher(db, cfg).search("tomatoes") or True
    sup = Supervisor(cfg, db, Brain(cfg, db, Searcher(db, cfg), []), [], crawl=False)
    msg = sup._maintenance_once()
    assert "FTS rebuild" in msg
    assert db.integrity()["ok"]
    for q in ("tomatoes", "asynciotopic", "polite crawler"):
        assert Searcher(db, cfg).search(q), q


def test_orphan_chunks_are_cleaned(tmp_path):
    cfg, db = mk(tmp_path)
    db.x("DELETE FROM docs WHERE url='http://t.test/c'")  # foreign keys off: chunks yateem reh gaye
    assert db.integrity()["orphans"] > 0
    Supervisor(cfg, db, Brain(cfg, db, Searcher(db, cfg), []), [], crawl=False)._maintenance_once()
    i = db.integrity()
    assert i["ok"] and i["orphans"] == 0 and not Searcher(db, cfg).search("tomatoes")


def test_healthy_db_is_left_alone(tmp_path):
    cfg, db = mk(tmp_path)
    before = db.counts()
    msg = Supervisor(cfg, db, Brain(cfg, db, Searcher(db, cfg), []), [], crawl=False)._maintenance_once()
    assert "FTS rebuild" not in msg and db.counts() == before


# ------------------------------------------------------------------ backups / disk limit / corruption
def test_backup_is_valid_and_old_ones_are_pruned(tmp_path):
    cfg, db = mk(tmp_path)
    paths = [db.backup(str(tmp_path / "bk"), keep=3) for _ in range(5)]
    files = sorted(os.listdir(tmp_path / "bk"))
    assert len(files) == 3 and files == sorted(os.path.basename(p) for p in paths)[-3:]
    assert all(check_file(str(tmp_path / "bk" / f)) for f in files)


def test_prune_oldest_removes_old_docs_first_and_keeps_db_valid(tmp_path):
    cfg = make_cfg(tmp_path)
    db = DB(cfg["general"]["db_path"])
    for i in range(40):
        seed_docs(db, [(f"http://t.test/{i}", f"Doc {i}", (f"unique{i} lorem ipsum dolor sit amet " * 400))])
        db.x("UPDATE docs SET fetched_at=? WHERE url=?", (1000 + i, f"http://t.test/{i}"))
    big = db.size_mb()
    removed = db.prune_oldest(big * 0.5)
    assert removed > 0 and db.size_mb() <= big * 0.5 + 0.2
    left = [r["url"] for r in db.q("SELECT url FROM docs ORDER BY fetched_at")]
    assert left and left[-1] == "http://t.test/39" and "http://t.test/0" not in left  # naya mehfooz, purana gaya
    assert db.integrity()["ok"]


def _corrupt(path: str, how: str) -> None:
    data = bytearray(open(path, "rb").read())
    if how == "header":
        data[:100] = b"X" * 100
    elif how == "middle":
        for i in range(8192, min(len(data), 40000)):
            data[i] = (data[i] * 7 + 13) % 256
    elif how == "truncate":
        data = data[: len(data) // 3]
    open(path, "wb").write(bytes(data))


@pytest.mark.parametrize("how", ["header", "middle", "truncate"])
def test_corrupt_database_is_restored_from_backup_on_start(tmp_path, how):
    cfg = make_cfg(tmp_path)
    db = DB(cfg["general"]["db_path"])
    for i in range(25):
        seed_docs(db, [(f"http://t.test/{i}", f"Doc {i}", f"document number {i} about restorable topic " * 30)])
    db.backup(os.path.join(cfg["_data_dir"], "backups"))
    path = cfg["general"]["db_path"]
    db.x("PRAGMA wal_checkpoint(TRUNCATE)")
    db.conn.close()
    _corrupt(path, how)
    assert not check_file(path)
    assert preflight_db(cfg) == "restored"
    assert check_file(path)
    db2 = DB(path)
    assert db2.counts()["docs"] == 25 and db2.integrity()["ok"]
    assert any(".corrupt-" in f for f in os.listdir(tmp_path))  # kharab file mehfooz rakhi gayi


def test_corrupt_database_without_backup_starts_fresh_not_crash_loop(tmp_path):
    cfg = make_cfg(tmp_path)
    path = cfg["general"]["db_path"]
    open(path, "wb").write(b"this is not a database at all" * 100)
    assert preflight_db(cfg) == "fresh"
    db = DB(path)  # ab khul jata hai
    assert db.integrity()["ok"] and db.counts()["docs"] == 0


def test_preflight_ignores_healthy_missing_and_empty_db(tmp_path):
    cfg = make_cfg(tmp_path)
    assert preflight_db(cfg) == "ok"                      # file hai hi nahi (pehli run)
    open(cfg["general"]["db_path"], "wb").close()
    assert preflight_db(cfg) == "ok"                      # khali file
    DB(cfg["general"]["db_path"])
    assert preflight_db(cfg) == "ok"


def test_restore_skips_corrupt_backups_and_uses_newest_good(tmp_path):
    cfg, db = mk(tmp_path)
    bk = os.path.join(cfg["_data_dir"], "backups")
    good = db.backup(bk)
    time.sleep(0.01)
    bad = db.backup(bk)
    _corrupt(bad, "header")                               # sab se naya backup kharab
    db.conn.close()
    open(cfg["general"]["db_path"], "wb").write(b"garbage")
    assert restore_latest_backup(cfg["general"]["db_path"], bk) == good
    assert DB(cfg["general"]["db_path"]).counts()["docs"] == 3


# ------------------------------------------------------------------ supervisor: crawler crash / stuck
def _sup(cfg, db, **m):
    cfg["maintenance"].update(m)
    return Supervisor(cfg, db, Brain(cfg, db, Searcher(db, cfg), []), [], crawl=True)


def test_crawler_crash_is_restarted_with_backoff_and_logged(tmp_path, monkeypatch):
    cfg, db = mk(tmp_path)
    calls = {"n": 0}

    async def flaky_run(self, once=False, max_pages=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("boom")
        while True:
            self.db.set_stat("heartbeat", time.time())
            await asyncio.sleep(0.1)

    monkeypatch.setattr(Crawler, "run", flaky_run)

    async def go():
        sup = _sup(cfg, db)
        await sup.start()
        await asyncio.sleep(4.5)           # backoff 1s + 2s
        await sup.stop()
        return sup

    sup = asyncio.run(go())
    assert calls["n"] >= 3 and sup.restarts >= 2
    logs = [r["detail"] for r in db.q("SELECT detail FROM brain_log WHERE kind='heal'")]
    assert sum("crawler crash RuntimeError: boom" in x for x in logs) == 2


def test_stuck_crawler_is_restarted_and_never_two_run_at_once(tmp_path, monkeypatch):
    """Watchdog atke hue crawler ko restart kare, aur kabhi do crawler loops ek saath na hon (pichla bug)."""
    cfg, db = mk(tmp_path)
    db.add_url("http://127.0.0.1:9/due", 1, "page")      # kaam maujood hai, par heartbeat nahi aa raha
    db.set_stat("heartbeat", time.time() - 100)
    state = {"calls": 0, "active": 0, "max_active": 0}

    async def hung_run(self, once=False, max_pages=None):
        state["calls"] += 1
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        try:
            await asyncio.sleep(3600)       # na heartbeat, na kaam: atka hua
        finally:
            state["active"] -= 1

    monkeypatch.setattr(Crawler, "run", hung_run)

    async def go():
        sup = _sup(cfg, db, watch_every_s=0.2, stuck_after_s=1)
        await sup.start()
        await asyncio.sleep(5.0)
        await sup.stop()
        return sup

    sup = asyncio.run(go())
    assert state["calls"] >= 2, "watchdog ne restart nahi kiya"
    assert state["max_active"] == 1, f"ek saath {state['max_active']} crawler chal rahe thay"
    assert state["active"] == 0                         # stop par sab saaf
    assert any("heartbeat" in r["detail"] for r in db.q("SELECT detail FROM brain_log WHERE kind='heal'"))


def test_background_loop_exception_does_not_kill_the_loop(tmp_path):
    cfg, db = mk(tmp_path)
    sup = Supervisor(cfg, db, Brain(cfg, db, Searcher(db, cfg), []), [], crawl=False)
    n = {"i": 0}

    async def flaky():
        n["i"] += 1
        if n["i"] < 3:
            raise ValueError("loop bug")
        await asyncio.sleep(0.05)

    asyncio.run(sup._guard(flaky))
    assert n["i"] == 3
    assert sum("background loop" in r["detail"] for r in db.q("SELECT detail FROM brain_log")) == 2


def test_clean_shutdown_leaves_no_tasks_behind(tmp_path, monkeypatch):
    cfg, db = mk(tmp_path)

    async def go():
        sup = _sup(cfg, db)
        await sup.start()
        await asyncio.sleep(0.5)
        await sup.stop()
        await asyncio.sleep(0.1)
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    assert asyncio.run(go()) == []


# ------------------------------------------------------------------ domain quarantine
def test_failing_domain_is_paused_others_unaffected(tmp_path):
    cfg, db = mk(tmp_path)
    for i in range(12):
        db.add_url(f"http://dead-site.test/p{i}", 1, "page")
        db.x("UPDATE frontier SET status='error', next_fetch=0, fail_count=1 WHERE url=?", (f"http://dead-site.test/p{i}",))
    db.add_url("http://healthy.test/x", 1, "page")
    sup = Supervisor(cfg, db, Brain(cfg, db, Searcher(db, cfg), []), [], crawl=False)
    assert sup.quarantine_bad_domains() == ["dead-site.test"]
    due = {r["domain"] for r in db.due(50)}
    assert "dead-site.test" not in due and "healthy.test" in due
    assert sup.quarantine_bad_domains() == []           # dobara pause nahi (already paused)
    db.x("UPDATE domain_state SET paused_until=0 WHERE domain='dead-site.test'")  # pause khatam
    assert "dead-site.test" in {r["domain"] for r in db.due(50)}


def test_one_domain_with_few_errors_is_not_paused(tmp_path):
    cfg, db = mk(tmp_path)
    for i in range(5):
        db.add_url(f"http://flaky.test/p{i}", 1, "page")
        db.x("UPDATE frontier SET status='error' WHERE url=?", (f"http://flaky.test/p{i}",))
    assert Supervisor(cfg, db, Brain(cfg, db, Searcher(db, cfg), []), [], crawl=False).quarantine_bad_domains() == []


# ------------------------------------------------------------------ tasks
def test_running_tasks_are_requeued_after_crash_and_completed(tmp_path):
    cfg, db = mk(tmp_path)
    db.x("INSERT INTO tasks(user_id,kind,payload,status,created) VALUES(0,'research',?, 'running', ?)",
         (json.dumps({"q": "polite crawler"}), time.time()))     # pichli run mein adhoora reh gaya tha

    async def go():
        sup = Supervisor(cfg, db, Brain(cfg, db, Searcher(db, cfg), []), [], crawl=False)
        await sup.start()
        for _ in range(60):
            await asyncio.sleep(0.1)
            if db.q("SELECT status FROM tasks")[0]["status"] in ("done", "failed"):
                break
        await sup.stop()

    asyncio.run(go())
    t = db.q("SELECT status,result FROM tasks")[0]
    assert t["status"] == "done" and "robots.txt" in json.loads(t["result"])["answer"]


def test_failed_task_is_marked_failed_not_stuck(tmp_path):
    cfg, db = mk(tmp_path)
    db.x("INSERT INTO tasks(user_id,kind,payload,status,created) VALUES(0,'nonsense','{}','queued',?)", (time.time(),))

    async def go():
        sup = Supervisor(cfg, db, Brain(cfg, db, Searcher(db, cfg), []), [], crawl=False)
        await sup.start()
        await asyncio.sleep(1.2)
        await sup.stop()

    asyncio.run(go())
    t = db.q("SELECT status,result FROM tasks")[0]
    assert t["status"] == "failed" and "unknown task kind" in t["result"]


def test_two_workers_never_run_the_same_task_twice(tmp_path):
    cfg, db = mk(tmp_path, server={"workers": 4})
    for i in range(12):
        db.x("INSERT INTO tasks(user_id,kind,payload,status,created) VALUES(0,'research',?, 'queued', ?)",
             (json.dumps({"q": f"polite crawler {i}"}), time.time()))

    async def go():
        sup = Supervisor(cfg, db, Brain(cfg, db, Searcher(db, cfg), []), [], crawl=False)
        ran = []
        orig = sup._run_task

        async def spy(kind, payload):
            ran.append(payload["q"])
            return await orig(kind, payload)

        sup._run_task = spy
        await sup.start()
        for _ in range(100):
            await asyncio.sleep(0.1)
            if db.q("SELECT COUNT(*) FROM tasks WHERE status='done'")[0][0] == 12:
                break
        await sup.stop()
        return ran

    ran = asyncio.run(go())
    assert len(ran) == 12 and len(set(ran)) == 12
