"""Relay worker: kisi bhi machine (Kaggle/Colab notebook, laptop) par chalta hai jahan ek local OpenAI-compatible LLM server (llama.cpp) hai.
Sirf *outbound* HTTP: Talashly se kaam kheenchta (pull) hai, token wapas bhejta (push) hai. Na tunnel, na khula port.
Sirf httpx + engine.mesh chahiye (Kaggle par kuch aur install nahi karna)."""
from __future__ import annotations

import asyncio
import json
import secrets
import time

import httpx

from .mesh import sign


class RelayWorker:
    def __init__(self, site: str, secret: str, llm_url: str, name: str = "node", model: str = "", slots: int = 2,
                 max_runtime_s: float | None = None, llm_key: str = "", flush_s: float = 0.12, log=print, tier: int = 1):
        self.site, self.secret, self.llm = site.rstrip("/"), secret, llm_url.rstrip("/")
        self.name, self.model, self.slots = name, model, slots
        self.deadline = time.time() + max_runtime_s if max_runtime_s else None
        self.llm_key, self.flush_s, self.log = llm_key, flush_s, log
        self.tier = tier     # 1 = CPU, 3 = GPU: mesh tez tier ko pehle kaam deta hai
        self.stop = False
        self.served = self.errors = 0
        self._tr = None   # tests ke liye transport

    def _client(self, timeout=40.0):
        return httpx.AsyncClient(timeout=timeout, transport=self._tr)

    async def _post(self, c: httpx.AsyncClient, path: str, obj: dict) -> dict:
        obj = {**obj, "nonce": secrets.token_hex(6)}   # har request ka sig alag (replay-guard se do slots na takraen)
        body = json.dumps(obj).encode()
        r = await c.post(self.site + path, content=body, headers={"Content-Type": "application/json", **sign(self.secret, "POST", path, body)})
        if r.status_code == 401:
            raise PermissionError("secret/signature galat (MESH_SECRET match nahi)")
        r.raise_for_status()
        return r.json()

    def should_run(self) -> bool:
        return not self.stop and not (self.deadline and time.time() > self.deadline)

    async def llm_ready(self) -> bool:
        try:
            async with self._client(5) as c:
                return (await c.get(self.llm + "/health")).status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def _serve(self, c: httpx.AsyncClient, job: dict):
        buf, last, cancelled = "", time.time(), False
        t0, first, nchar = time.time(), None, 0
        payload = {"messages": job["messages"], "stream": True, "temperature": job.get("temperature", 0.5),
                   "max_tokens": min(int(job.get("max_tokens", 1500)), 6000)}
        if self.model:
            payload["model"] = self.model
        headers = {"Authorization": "Bearer " + self.llm_key} if self.llm_key else {}
        path = f"/api/mesh/push/{job['id']}"

        async def flush(final=False, error=None):
            nonlocal buf, cancelled
            msg = {}
            if buf:
                msg["delta"] = buf
            if final:
                msg["done"] = True
            if error:
                msg["error"] = error
            if msg:
                buf = ""
                res = await self._post(c, path, msg)
                if res.get("cancel"):
                    cancelled = True

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0), transport=self._tr) as lc:
                async with lc.stream("POST", self.llm + "/v1/chat/completions", json=payload, headers=headers) as r:
                    if r.status_code != 200:
                        raise RuntimeError(f"local llm http {r.status_code}")
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            d = json.loads(data)["choices"][0]["delta"].get("content")
                        except (KeyError, ValueError, IndexError, TypeError):
                            continue
                        if d:
                            if first is None:
                                first = time.time() - t0
                            nchar += len(d)
                            buf += d
                        if buf and (time.time() - last >= self.flush_s or len(buf) > 200):
                            await flush()
                            last = time.time()
                        if cancelled:
                            return
            await flush(final=True)
            self.served += 1
            self.log(f"[relay] job ok: first token {first or 0:.1f}s, {nchar} chars, total {time.time() - t0:.1f}s")
        except PermissionError:
            raise
        except Exception as e:  # noqa: BLE001  local LLM/network ki galti: server ko batao (wo agla node/fallback lega)
            self.errors += 1
            self.log(f"[relay] job error: {type(e).__name__}: {str(e)[:120]}")
            try:
                await flush(error=f"{type(e).__name__}: {e}"[:150])
            except Exception:  # noqa: BLE001
                pass

    async def _puller(self, i: int):
        back, denied = 1.0, 0
        async with self._client(30) as c:
            while self.should_run():
                try:
                    if not await self.llm_ready():
                        await asyncio.sleep(3)
                        continue
                    res = await self._post(c, "/api/mesh/pull", {"node": self.name, "model": self.model, "slots": self.slots, "wait_s": 12, "tier": self.tier})
                    back, denied = 1.0, 0
                    if res.get("job"):
                        await self._serve(c, res["job"])
                except PermissionError as e:
                    denied += 1
                    if denied >= 3:   # ek-aadh 401 (clock/replay) maaf; lagataar 3 = secret galat
                        self.log(f"[relay] FATAL {e}")
                        self.stop = True
                        return
                    await asyncio.sleep(1.0)
                except Exception as e:  # noqa: BLE001  VM band/network: dheere dheere dobara
                    self.log(f"[relay] {type(e).__name__}: {str(e)[:100]} (retry {back:.0f}s)")
                    await asyncio.sleep(back)
                    back = min(30.0, back * 2)

    async def run(self):
        self.log(f"[relay] {self.name} -> {self.site} (slots={self.slots})")
        await asyncio.gather(*[self._puller(i) for i in range(self.slots)])
        try:   # graceful: server ko batao ke ab nahi aa raha (turant fallback)
            async with self._client(8) as c:
                await self._post(c, "/api/mesh/pull", {"node": self.name, "bye": True})
        except Exception:  # noqa: BLE001
            pass
        self.log(f"[relay] band: served={self.served} errors={self.errors}")
