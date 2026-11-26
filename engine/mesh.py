"""Compute Mesh: kai LLM-nodes (apni machine, Kaggle/Colab notebook, koi bhi laptop) ek saath, auto-failover ke saath.

* Static node  : config/env ka OpenAI-compatible endpoint (apni 24/7 machine par llama.cpp) - sab se pehle.
* Relay node   : koi bhi machine jo sirf *bahar* (outbound HTTP) se judti hai: VM se kaam "kheenchti" (pull) hai aur
                 token wapas "dhakelti" (push) hai. Na tunnel, na khula port, na URL-badalna. Kaggle notebook isi tarah judta hai.
Koi bhi node na ho / gir jaye / dheema ho -> router agla node aazmata hai, aakhir mein Talashly ka apna search-brain (hamesha chalta hai).
Auth: HMAC-SHA256 (shared secret kabhi wire par nahi jata), time-window + replay-guard.
"""
from __future__ import annotations

import asyncio
import collections
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

import httpx

PULL_TTL_S = 45          # itni der se pull na aaye to node mara hua
PICKUP_TIMEOUT_S = 4.0   # job itni der mein na uthe to agla node/fallback
FIRST_TOKEN_S = 30.0     # chhota prompt (3 source) CPU par ~5-10 s; isse der ho to agla node/fallback, user 60 s na ruke
CHUNK_TIMEOUT_S = 25.0
MAX_PENDING = 8
SLOT_WAIT_S = 90.0     # sab slot bhare: itni der khali slot ka intezar (phir fallback)
SKEW_S = 60
BREAK_AFTER, BREAK_FOR_S = 3, 30


class NodeError(RuntimeError):
    """Is node se jawab nahi mila (agla node aazmao)."""


def load_secret(data_dir: str) -> str:
    """Shared secret: env MESH_SECRET ya data/mesh_secret.txt (pehli baar khud bante)."""
    env = os.environ.get("MESH_SECRET", "").strip()
    if len(env) >= 16:
        return env
    f = Path(data_dir) / "mesh_secret.txt"
    try:
        if f.exists() and len(f.read_text().strip()) >= 16:
            return f.read_text().strip()
        f.parent.mkdir(parents=True, exist_ok=True)
        s = secrets.token_hex(24)
        f.write_text(s)
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
        return s
    except OSError:
        return secrets.token_hex(24)  # read-only disk: is process ke liye hi (node judne se pehle secret padhna zaroori)


def sign(secret: str, method: str, path: str, body: bytes, ts: int | None = None) -> dict:
    ts = int(time.time()) if ts is None else ts
    msg = f"{ts}.{method.upper()}.{path}.{hashlib.sha256(body).hexdigest()}".encode()
    return {"X-Mesh-Ts": str(ts), "X-Mesh-Sig": hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()}


class Job:
    def __init__(self, messages, max_tokens, temperature, target: str | None = None):
        self.target = target     # sirf isi node ko dena (None = koi bhi)
        self.id = uuid.uuid4().hex
        self.payload = {"id": self.id, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        self.q: asyncio.Queue = asyncio.Queue()
        self.taken = asyncio.Event()
        self.cancelled = False
        self.node: str | None = None
        self.t0 = time.time()


class RelayNode:
    def __init__(self, name: str):
        self.name, self.model, self.slots, self.tier = name, "", 1, 1
        self.first_seen = self.last_seen = time.time()
        self.done = self.failed = 0
        self.lat_ms: float | None = None
        self.busy = 0

    def alive(self) -> bool:
        return time.time() - self.last_seen <= PULL_TTL_S

    def info(self) -> dict:
        return {"name": self.name, "model": self.model, "tier": self.tier, "alive": self.alive(), "age_s": round(time.time() - self.first_seen),
                "busy": self.busy, "slots": self.slots, "done": self.done, "failed": self.failed,
                "first_token_ms": round(self.lat_ms) if self.lat_ms else None}


class Mesh:
    def __init__(self):
        self.secret = ""
        self.nodes: dict[str, RelayNode] = {}
        self.jobs: dict[str, Job] = {}
        self.pending: collections.deque[Job] = collections.deque()
        self._wake = asyncio.Event()
        self._seen: dict[str, float] = {}
        self._breaker: dict[str, list] = {}   # key -> [fails, until]
        self.stats = collections.Counter()
        self.last_pull = 0.0
        self.lag = collections.deque(maxlen=120)   # (waqt, event-loop kitni der atka ms)

    # ------------------------------------------------------------ auth
    def verify(self, method: str, path: str, body: bytes, ts: str | None, sig: str | None) -> bool:
        if not self.secret or not ts or not sig:
            return False
        try:
            t = int(ts)
        except ValueError:
            return False
        if abs(time.time() - t) > SKEW_S:
            return False
        want = sign(self.secret, method, path, body, t)["X-Mesh-Sig"]
        if not hmac.compare_digest(want, sig):
            return False
        now = time.time()
        for k in [k for k, v in self._seen.items() if now - v > 2 * SKEW_S]:
            self._seen.pop(k, None)
        if sig in self._seen:
            return False  # replay
        self._seen[sig] = now
        return True

    # ------------------------------------------------------------ node side (HTTP handlers call these)
    def touch(self, name: str, model: str = "", slots: int = 1, tier: int = 1) -> RelayNode:
        n = self.nodes.get(name)
        if not n or not n.alive():
            n = self.nodes[name] = RelayNode(name)  # purana mara hua node: nayi zindagi (age reset)
        n.last_seen = time.time()
        n.model = (model or n.model)[:60]
        n.slots = max(1, min(int(slots or 1), 8))
        n.tier = max(1, min(int(tier or 1), 9))
        return n

    def drop(self, name: str):
        """Node khud 'bye' kahe (graceful band): turant mara hua maano, 45 s intezar nahi."""
        n = self.nodes.pop(name, None)
        if n:
            self.stats["bye"] += 1

    async def pull(self, name: str, model: str, slots: int, wait_s: float = 20.0, tier: int = 1) -> dict | None:
        n = self.touch(name, model, slots, tier)
        self.stats["pulls"] += 1
        self.last_pull = time.time()
        end = time.time() + wait_s
        while time.time() < end:
            for job in list(self.pending):
                if job.cancelled or job.taken.is_set():
                    self.pending.remove(job)
                    continue
                if job.target and job.target != name:   # kisi aur (tez) node ke liye hai
                    continue
                self.pending.remove(job)
                job.node = name
                job.taken.set()
                n.busy += 1
                return job.payload
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=min(2.0, max(0.05, end - time.time())))
            except asyncio.TimeoutError:
                pass
            n.last_seen = time.time()
        return None

    def push(self, job_id: str, msg: dict) -> dict:
        job = self.jobs.get(job_id)
        if not job:
            return {"ok": False, "cancel": True}
        n = self.nodes.get(job.node or "")
        if n:
            n.last_seen = time.time()
        if msg.get("delta"):
            job.q.put_nowait(("tok", str(msg["delta"])))
        if msg.get("error"):
            job.q.put_nowait(("err", str(msg["error"])[:200]))
        if msg.get("done"):
            job.q.put_nowait(("done", None))
        return {"ok": True, "cancel": job.cancelled}

    # ------------------------------------------------------------ router
    def alive_relays(self, quality: bool = True) -> list[RelayNode]:
        """quality=True: sab se qabil node pehle (GPU 3 > 4B CPU 2 > 1.7B CPU 1). False (halka kaam): sab se tez/chhota lane pehle,
        taake bade node mushkil kaam ke liye khali rahein."""
        # pehle jinke slot khali hain; unmein se tez tier (GPU) pehle; phir naapi hui raftar; phir kam bhara hua
        def load(n):   # chal rahe + is node ke liye intezar mein (abhi uthaye nahi gaye)
            return n.busy + sum(1 for j in self.pending if j.target == n.name and not j.taken.is_set() and not j.cancelled)
        return sorted((n for n in self.nodes.values() if n.alive()),
                      key=lambda n: (load(n) >= n.slots, -n.tier if quality else n.tier, n.lat_ms or 1e9, load(n) / n.slots))

    def has_alive(self) -> bool:
        return bool(self.alive_relays())

    def status(self, detail: bool = False) -> dict:
        al = self.alive_relays()
        cpu = [n for n in al if n.tier <= 2]    # hamesha-chalne wala CPU node (GPU sirf kuch ghante)
        s = {"nodes_alive": len(al), "oldest_age_s": max((round(time.time() - n.first_seen) for n in al), default=0),
             "cpu_alive": len(cpu), "cpu_oldest_age_s": max((round(time.time() - n.first_seen) for n in cpu), default=0),
             "cloudflare": {"on": cf_cfg() is not None, "left_today": max(0, self.cf_left()) if cf_cfg() else 0},
             "pending": len(self.pending), "pulls": self.stats["pulls"],
             "last_pull_age_s": round(time.time() - self.last_pull) if self.last_pull else None,
             "loop_lag_ms_max_60s": max((ms for t, ms in self.lag if time.time() - t < 60), default=None)}
        if detail:
            s["nodes"] = [n.info() for n in self.nodes.values()]
            s["stats"] = dict(self.stats)
        return s

    def _broken(self, key: str) -> bool:
        b = self._breaker.get(key)
        if key == "static_alive":
            return False
        return bool(b and b[0] >= BREAK_AFTER and time.time() < b[1])

    def _fail(self, key: str):
        b = self._breaker.setdefault(key, [0, 0.0])
        b[0] += 1
        b[1] = time.time() + BREAK_FOR_S

    def _ok(self, key: str):
        self._breaker.pop(key, None)

    async def _relay_stream(self, node: RelayNode, messages, max_tokens, temperature) -> AsyncIterator[str]:
        if len(self.pending) >= MAX_PENDING:
            raise NodeError("queue full")
        job = Job(messages, max_tokens, temperature, target=node.name)
        self.jobs[job.id] = job
        self.pending.append(job)
        self._wake.set()
        t0 = time.time()
        got = False
        # lamba prompt (page edit) ka prefill CPU par ~50 tok/s: timeout prompt ke size se badhta hai (30..150 s)
        est_tok = sum(len(str(m.get("content", ""))) for m in messages) / 3.5
        first_s = min(150.0, max(FIRST_TOKEN_S, 10.0 + est_tok / 50.0))
        try:
            try:
                await asyncio.wait_for(job.taken.wait(), PICKUP_TIMEOUT_S)
            except asyncio.TimeoutError:
                node.last_seen = 0   # pull nahi kar raha: mara hua maano (zinda hua to agli pull par khud wapas aa jayega)
                raise NodeError("not picked up") from None
            while True:
                try:
                    kind, val = await asyncio.wait_for(job.q.get(), CHUNK_TIMEOUT_S if got else first_s)
                except asyncio.TimeoutError:
                    raise NodeError("node stalled") from None
                if kind == "tok":
                    if not got:
                        got = True
                        node.lat_ms = (time.time() - t0) * 1000
                    yield val
                elif kind == "err":
                    raise NodeError(val)
                else:
                    node.done += 1
                    return
        except GeneratorExit:   # user ne beech mein rok diya: node ki galti nahi
            raise
        except BaseException:
            node.failed += 1
            raise
        finally:
            job.cancelled = True
            if job.node and (n := self.nodes.get(job.node)):
                n.busy = max(0, n.busy - 1)
            self.jobs.pop(job.id, None)

    async def _static_alive(self, cfg: dict) -> bool:
        """Static node zinda? (15 s cache) - mara hua server har sawal par 10 s connect-timeout na khaye."""
        from .llm import llm_alive
        c = self._breaker.setdefault("static_alive", [0, 0.0])
        now = time.time()
        if now - c[1] < 15 and len(c) > 2:
            return bool(c[2])
        ok = await llm_alive(cfg["base_url"])
        c[:] = [0, now, ok]
        return ok

    async def _static_stream(self, cfg: dict, messages, max_tokens, temperature) -> AsyncIterator[str]:
        from .llm import _stream_chat_raw
        if not cfg.get("api_key") and not await self._static_alive(cfg):   # key wala = bahari provider: /health nahi hota
            raise NodeError("static node down")
        async for t in _stream_chat_raw(cfg, messages, max_tokens, temperature):
            yield t

    def cf_left(self) -> int:
        day = time.strftime("%Y-%m-%d", time.gmtime())   # Cloudflare ka din 00:00 UTC par reset hota hai
        if getattr(self, "_cf_day", None) != day:
            self._cf_day, self._cf_used = day, 0
        return int(os.environ.get("CF_MAX_PER_DAY", "120")) - self._cf_used

    def _free_slot(self) -> bool:
        return any(n.busy + sum(1 for j in self.pending if j.target == n.name and not j.taken.is_set() and not j.cancelled) < n.slots
                   for n in self.nodes.values() if n.alive())

    async def _wait_slot(self) -> None:
        """Sab nodes ke slot bhare hon to fallback par girne ki bajaye (jo node ko 'mara hua' bhi kar deta tha) khali slot ka intezar."""
        if not self.has_alive() or self._free_slot():
            return
        t0 = time.time()
        while time.time() - t0 < SLOT_WAIT_S and self.has_alive() and not self._free_slot():
            await asyncio.sleep(0.2 + (hash(id(asyncio.current_task())) % 100) / 1000)

    async def stream(self, cfg: dict, messages: list[dict], max_tokens: int = 1500, temperature: float = 0.5,
                     quality: bool = True) -> AsyncIterator[str]:
        """Sab nodes ko order se aazmata hai. Pehla token aane se pehle ki har galti par agla node;
        token aane ke baad galti ho to upar phenk deta hai (caller ya to 'replace' karta hai ya extractive par girta hai)."""
        cf = cf_cfg() if (cf_available() and cf_general()) else None
        overflow = bool(cf) and self.has_alive() and not self._free_slot()   # nodes bhare: slot ka intezar nahi, seedha Cloudflare
        if not overflow:
            await self._wait_slot()
        cands: list[tuple[str, object]] = []
        if cfg.get("enabled"):
            cands.append(("static", cfg))
        relays = [(f"relay:{n.name}", n) for n in self.alive_relays(quality)]
        if cf and overflow:
            cands.append(("cf", cf))
        cands += relays
        if cf and not overflow:
            cands.append(("cf", cf))   # aakhri sahara
        last: Exception | None = None
        for key, node in cands:
            if self._broken(key):
                continue
            started = False
            try:
                if key == "cf":
                    self._cf_used += 1
                it = (self._static_stream(node, messages, max_tokens, temperature) if key in ("static", "cf")
                      else self._relay_stream(node, messages, max_tokens, temperature))
                async for t in it:
                    started = True
                    yield t
                self._ok(key)
                self.stats["served_" + key.split(":")[0]] += 1
                return
            except GeneratorExit:
                raise
            except Exception as e:  # noqa: BLE001
                self._fail(key)
                self.stats["failed_" + key.split(":")[0]] += 1
                last = e
                if started:
                    raise
        self.stats["no_node"] += 1
        raise NodeError(f"no llm node available ({last})" if last else "no llm node available")


    async def watch_loop(self):
        """Har 0.5 s sone ki koshish; asal mein jitni der zyada soye utna loop atka tha (CPU-bhooka server pakadne ke liye)."""
        while True:
            t0 = time.perf_counter()
            await asyncio.sleep(0.5)
            self.lag.append((time.time(), max(0.0, (time.perf_counter() - t0 - 0.5) * 1000)))


MESH = Mesh()


def fast_llm_available() -> bool:
    """GPU-tier relay-node zinda hai? (CPU node par lambi cheezein 10 tok/s: poori website 60-90 s)."""
    return any(n.tier >= 3 for n in MESH.alive_relays())


def cf_cfg() -> dict | None:
    """Cloudflare Workers AI (free 10,000 neurons/din, open-source models): sirf tab jab owner ne CF_ACCOUNT_ID + CF_API_TOKEN set kiye.
    Ye overflow lane hai: Kaggle nodes bhare hon to lambe jawab yahan; roz ki hadd (CF_MAX_PER_DAY, default 120) guard hai."""
    acct, tok = os.environ.get("CF_ACCOUNT_ID", "").strip(), os.environ.get("CF_API_TOKEN", "").strip()
    if not (acct.isalnum() and tok):
        return None
    return {"enabled": True, "base_url": f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/v1", "path": "/chat/completions",
            "api_key": tok, "model": os.environ.get("CF_MODEL", "@cf/google/gemma-4-26b-a4b-it").strip()}


def cf_general() -> bool:
    """Cloudflare asal Talashly chat mein nahi (wo apni training/Qwen par rehti hai); sirf CF_GENERAL=1 par aam chat mein overflow."""
    return os.environ.get("CF_GENERAL", "") == "1"


def cf_available() -> bool:
    return cf_cfg() is not None and MESH.cf_left() > 0 and not MESH._broken("cf")


def llm_available(cfg: dict) -> bool:
    """Kam az kam ek LLM zinda hone ki umeed: static enabled, relay-node, ya Cloudflare overflow lane."""
    return bool(cfg.get("enabled")) or MESH.has_alive() or (cf_available() and cf_general())
