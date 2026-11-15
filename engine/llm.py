from __future__ import annotations

import json
from typing import AsyncIterator

import httpx


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
