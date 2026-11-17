"""Supervisor: system ka khud ka nigehbaan. Koi bhi hissa gire ya atke to khud theek karta hai.

 - crawler loop     : crash par exponential backoff ke saath dobara shuru
 - watchdog         : heartbeat band ho (atka hua) to crawler ko restart; kharab domains ko pause
 - maintenance      : DB integrity, FTS index kharab ho to rebuild, rozana backup, disk limit, naye domains
 - gap worker       : jo sawal hal nahi hue, unhe background mein dobara try
 - task workers     : users ke bheje hue lambay kaam (research/crawl); crash par task wapas queue mein
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from typing import Any

from .brain import Brain
from .crawler import Crawler, in_active_hours
from .db import DB

log = logging.getLogger("supervisor")


class Supervisor:
    def __init__(self, cfg: dict, db: DB, brain: Brain, seeds: list[dict] | None = None, crawl: bool = True):
        self.cfg, self.db, self.brain, self.seeds, self.do_crawl = cfg, db, brain, seeds, crawl
        self.m = cfg["maintenance"]
        self.crawler: Crawler | None = None
        self._crawl_task: asyncio.Task | None = None
        self._crawler_run: asyncio.Task | None = None
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self.restarts = 0
        self.brain.get_crawler = lambda: self.crawler
        self.brain.heal_now = self.heal_now
        self.started = time.time()

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        n = self.db.x("UPDATE tasks SET status='queued' WHERE status='running'").rowcount  # crash ke baad wapas queue
        if n:
            self.db.log("heal", f"{n} adhoore task wapas queue mein")
        for p in self.cfg.get("_problems", []):
            self.db.log("config", p)
        loops = [self._watchdog, self._maintenance, self._gap_worker]
        if self.do_crawl:
            self._crawl_task = asyncio.create_task(self._crawler_loop())
        loops += [functools.partial(self._task_worker, i) for i in range(self.cfg["server"]["workers"])]
        self._tasks = [asyncio.create_task(self._guard(f)) for f in loops]

    async def stop(self) -> None:
        self._stop.set()
        if self.crawler:
            self.crawler.stop()
        for t in [*self._tasks, *( [self._crawl_task] if self._crawl_task else [] )]:
            t.cancel()
        await asyncio.gather(*self._tasks, *( [self._crawl_task] if self._crawl_task else [] ), return_exceptions=True)
        if self.crawler:
            await self.crawler.aclose()

    async def _guard(self, factory) -> None:
        """Koi bhi background loop exception se mare to dobara chalao (system kabhi 'adhoora' nahi rehta)."""
        while not self._stop.is_set():
            try:
                await factory()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.restarts += 1
                try:
                    self.db.log("heal", f"background loop {getattr(factory, '__name__', 'task')} gira "
                                        f"({type(e).__name__}: {e}), restart"[:300])
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(2)

    # ---------------------------------------------------------------- crawler
    async def _crawler_loop(self) -> None:
        """Ek hi loop: crawler.run() ko inner task mein chalata hai. Crash ya watchdog-cancel par naya Crawler banata hai."""
        backoff = 1
        while not self._stop.is_set():
            started = time.time()
            self.crawler = Crawler(self.cfg, self.seeds)
            run_task = asyncio.create_task(self.crawler.run())
            self._crawler_run = run_task
            try:
                await asyncio.wait({run_task})  # inner ka exception/cancel yahan raise nahi hota
            except asyncio.CancelledError:  # bahar se (shutdown) cancel hua
                run_task.cancel()
                await asyncio.gather(run_task, return_exceptions=True)
                raise
            if self._stop.is_set():
                return
            if run_task.cancelled():
                self.db.log("heal", "crawler atka hua tha (watchdog): restart")
            elif run_task.exception() is not None:
                exc = run_task.exception()
                self.db.log("heal", f"crawler crash {type(exc).__name__}: {exc}"[:300])
                log.error("crawler crashed: %r", exc)
            self.restarts += 1
            if time.time() - started > 300:
                backoff = 1  # der tak theek chala tha to backoff reset
            await asyncio.sleep(min(60, backoff))
            backoff = min(60, backoff * 2)

    async def _watchdog(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.m.get("watch_every_s", 30))
            try:
                if self.do_crawl and self._crawl_task and not self._crawl_task.done() \
                        and in_active_hours(self.cfg["crawler"]["active_hours"], self.cfg["crawler"]["timezone_offset_hours"]):
                    beat = self.db.get_stat("heartbeat", 0)
                    if beat and time.time() - beat > self.m["stuck_after_s"] and self.db.due(1):
                        self.db.log("heal", f"heartbeat {int(time.time() - beat)}s purana: crawler restart")
                        self.db.set_stat("heartbeat", time.time())
                        if self._crawler_run and not self._crawler_run.done():
                            self._crawler_run.cancel()  # _crawler_loop khud naya crawler bana lega (dusra loop nahi banta)
                self.quarantine_bad_domains()
            except Exception as e:  # noqa: BLE001
                log.warning("watchdog error: %s", e)

    def quarantine_bad_domains(self, hours: float = 6) -> list[str]:
        """Jo domain lagatar fail ho raha ho usay kuch ghantay ke liye pause karo (baqi sites par asar na pare)."""
        rows = self.db.q(
            "SELECT domain, SUM(status='error') e, SUM(status='done') d FROM frontier GROUP BY domain "
            "HAVING e>=10 AND e > 3*d AND domain NOT IN (SELECT domain FROM domain_state WHERE paused_until>?)",
            (time.time(),))
        out = []
        for r in rows:
            self.db.pause_domain(r["domain"], hours * 3600)
            self.db.log("heal", f"domain pause {hours}h: {r['domain']} (errors={r['e']}, ok={r['d']})")
            out.append(r["domain"])
        return out

    # ---------------------------------------------------------------- maintenance
    async def heal_now(self) -> str:
        return await asyncio.to_thread(self._maintenance_once, True)

    def _maintenance_once(self, force_backup: bool = False) -> str:
        db = DB(self.db.path)  # thread-safe: apna connection
        report = []
        try:
            full_due = force_backup or time.time() - db.get_stat("last_full_check", 0) > 86400 \
                or not db.integrity_light()
            integ = {"ok": True}
            if full_due:
                integ = db.integrity()
                if integ.get("ok"):
                    db.set_stat("last_full_check", time.time())
            if not integ["ok"]:
                report.append(f"integrity problem {json.dumps(integ)[:200]}")
                if integ["sqlite"] != "ok":
                    path = db.backup(self.cfg["_data_dir"] + "/backups", self.m["backup_keep"])
                    report.append(f"SQLite kharab: backup liya {path}; REINDEX")
                    db.x("REINDEX")
                n = db.rebuild_fts()
                report.append(f"FTS rebuild: {n} chunks")
                db.log("heal", "; ".join(report))
            last = db.get_stat("last_backup", 0)
            if force_backup or time.time() - last > 86400:
                p = db.backup(self.cfg["_data_dir"] + "/backups", self.m["backup_keep"])
                db.set_stat("last_backup", time.time())
                report.append(f"backup: {p}")
            if db.size_mb() > self.m["max_db_mb"]:
                removed = db.prune_oldest(self.m["max_db_mb"] * 0.9)
                report.append(f"disk limit: {removed} purane docs hataye")
                db.log("heal", report[-1])
            db.x("DELETE FROM query_log WHERE id < (SELECT MAX(id) FROM query_log) - 10000")
            added = self.brain.expand_domains(db)
            if added:
                report.append(f"naye domains: {', '.join(added)}")
        finally:
            db.conn.close()
        return "OK. " + ("; ".join(report) if report else "sab theek hai, kuch theek karne ki zarurat nahi")

    async def _maintenance(self) -> None:
        await asyncio.sleep(5)
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self._maintenance_once)
            except Exception as e:  # noqa: BLE001
                self.db.log("heal", f"maintenance error {type(e).__name__}: {e}")
            await asyncio.sleep(self.m["check_every_s"])

    async def _gap_worker(self) -> None:
        await asyncio.sleep(20)
        while not self._stop.is_set():
            try:
                if await self.brain.work_gap():
                    continue
            except Exception as e:  # noqa: BLE001
                log.warning("gap worker: %s", e)
            await asyncio.sleep(self.m.get("gap_every_s", 120))

    # ---------------------------------------------------------------- tasks (multi-user)
    def _claim_task(self) -> Any:
        r = self.db.q(
            "UPDATE tasks SET status='running' WHERE id=(SELECT id FROM tasks WHERE status='queued' ORDER BY id LIMIT 1) "
            "RETURNING id, user_id, kind, payload")
        return r[0] if r else None

    async def _task_worker(self, idx: int) -> None:
        while not self._stop.is_set():
            try:
                t = self._claim_task()
                if t is None:
                    await asyncio.sleep(0.5)
                    continue
                payload = json.loads(t["payload"] or "{}")
                try:
                    result = await asyncio.wait_for(self._run_task(t["kind"], payload), timeout=300)
                    status = "done"
                except Exception as e:  # noqa: BLE001
                    result, status = {"error": f"{type(e).__name__}: {e}"}, "failed"
                self.db.x("UPDATE tasks SET status=?, result=?, finished=? WHERE id=?",
                          (status, json.dumps(result, ensure_ascii=False), time.time(), t["id"]))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("task worker %s: %s", idx, e)
                await asyncio.sleep(1)

    async def _run_task(self, kind: str, payload: dict) -> dict:
        if kind == "research":
            steps, sources, answer, done = [], [], "", {}
            async for ev, data in self.brain.run(payload["q"], role="user", deep=True, stream_delay=0):
                if ev == "step":
                    steps.append(data["msg"])
                elif ev == "sources":
                    sources = data
                elif ev == "token":
                    answer += data
                elif ev == "replace":
                    answer = data
                elif ev == "done":
                    done = data
            return {"answer": answer.strip(), "sources": sources, "steps": steps, "meta": done}
        if kind == "crawl":
            msg = await self.brain.command("/add " + payload["url"])
            return {"message": msg}
        raise ValueError(f"unknown task kind {kind}")
