from __future__ import annotations

import json
from typing import AsyncIterator

import httpx


def llm_ready_cfg(c: dict) -> bool:
    """LLM tayyar? Bahari (key wala, OpenAI-compatible) ho to sirf enabled+key; local ho to enabled."""
    return bool(c.get("enabled"))


async def _strip_think(it: AsyncIterator[str]) -> AsyncIterator[str]:
    """Qwen3 jaise models <think>...</think> likhte hain: wo hissa user ko kabhi nahi dikhta (tukron mein bante tags bhi)."""
    buf, inside = "", False
    async for tok in it:
        buf += tok
        while True:
            if inside:
                i = buf.find("</think>")
                if i < 0:
                    buf = buf[-8:] if len(buf) > 8 else buf   # aakhri hissa rakho: tag adha aaya ho sakta hai
                    break
                buf, inside = buf[i + 8:].lstrip("\n"), False
            else:
                i = buf.find("<think>")
                if i >= 0:
                    if buf[:i]:
                        yield buf[:i]
                    buf, inside = buf[i + 7:], True
                    continue
                keep = 6 if "<" in buf[-7:] else 0   # "<thi" jaisa adhura tag rok lo
                if len(buf) > keep:
                    yield buf[:len(buf) - keep]
                    buf = buf[len(buf) - keep:]
                break
    if buf and not inside:
        yield buf


async def stream_chat(c: dict, messages: list[dict], max_tokens: int = 1500, temperature: float = 0.5) -> AsyncIterator[str]:
    """Mesh ke zariye: static node + relay nodes (Kaggle...) order se, auto-failover. Koi nahi to NodeError (caller extractive par girta hai)."""
    from .mesh import MESH
    async for t in _strip_think(MESH.stream(c, messages, max_tokens, temperature)):
        yield t


async def _stream_chat_raw(c: dict, messages: list[dict], max_tokens: int = 1500, temperature: float = 0.5) -> AsyncIterator[str]:
    """OpenAI-compatible endpoint (apna llama.cpp ya koi bhi free provider: Groq, Gemini, OpenRouter...).
    Key/model/URL config ya env (SS_LLM_*) se aate hain; key kabhi log/response mein nahi jati."""
    headers = {"Content-Type": "application/json"}
    if c.get("api_key"):
        headers["Authorization"] = "Bearer " + c["api_key"]
    payload = {"messages": messages, "stream": True, "temperature": temperature, "max_tokens": max_tokens}
    if c.get("model"):
        payload["model"] = c["model"]
    url = c["base_url"].rstrip("/") + (c.get("path") or "/v1/chat/completions")
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as cl:
        async with cl.stream("POST", url, json=payload, headers=headers) as r:
            if r.status_code != 200:
                raise RuntimeError(f"llm http {r.status_code}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content")
                except (KeyError, ValueError, IndexError, TypeError):
                    continue
                if delta:
                    yield delta


async def stream_local_llm(base_url: str, prompt: str) -> AsyncIterator[str]:
    """Apne hi server par chalte llama.cpp (OpenAI-compatible) se token stream.
    Ye third-party API nahi: aap ka apna model, aap ki apni machine."""
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "stream": True, "temperature": 0.2, "max_tokens": 600,
    }
    async with httpx.AsyncClient(timeout=None) as c:
        async with c.stream("POST", f"{base_url.rstrip('/')}/v1/chat/completions", json=payload) as r:
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content")
                except (KeyError, ValueError, IndexError):
                    continue
                if delta:
                    yield delta


async def llm_alive(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            r = await c.get(f"{base_url.rstrip('/')}/health")
            return r.status_code == 200
    except Exception:
        return False
