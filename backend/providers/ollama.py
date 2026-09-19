"""Ollama generate and stream clients."""

from typing import Any, AsyncGenerator, Dict, Optional
import json
import logging

import httpx


async def call_ollama(
    url: str,
    model: str,
    prompt: str,
    system: str = "",
    json_mode: bool = True,
) -> Optional[str]:
    # Do not set Ollama `format: json` here. Thinking models (e.g. qwen3) often
    # return an empty `{}` under that constraint while plain generation returns
    # valid JSON in `response` (with optional separate `thinking`).
    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "system": system or ("Respond with valid JSON only." if json_mode else ""),
        "stream": False,
        "options": {"temperature": 0},
    }
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{url.rstrip('/')}/api/generate", json=payload)
            if response.status_code == 200:
                body = response.json()
                text = (body.get("response") or "").strip()
                if not text and body.get("thinking"):
                    # Rare: answer landed in thinking; still try to recover JSON.
                    text = str(body.get("thinking") or "")
                return text
            logging.warning("Ollama responded %s for model %s", response.status_code, model)
    except Exception as exc:
        logging.warning("Ollama call failed: %s: %s", exc.__class__.__name__, exc)
    return None


async def stream_ollama(url: str, model: str, prompt: str, system: str = "") -> AsyncGenerator[str, None]:
    payload = {"model": model, "prompt": prompt, "system": system, "stream": True}
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream("POST", f"{url.rstrip('/')}/api/generate", json=payload) as response:
            if response.status_code != 200:
                raise RuntimeError(f"Ollama responded {response.status_code}")
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    continue
                chunk = frame.get("response")
                if chunk:
                    yield chunk
                if frame.get("done"):
                    return
