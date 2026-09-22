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
    options: Optional[Dict[str, Any]] = None,
    response_schema: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    # Do not set Ollama `format: json` here. Thinking models (e.g. qwen3) often
    # return an empty `{}` under that constraint while plain generation returns
    # valid JSON in `response` (with optional separate `thinking`).
    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "system": system or ("Respond with valid JSON only." if json_mode else ""),
        "stream": False,
        # Ranking wants the same answer twice, not creativity: greedy decoding,
        # a fixed seed, and a context window big enough for the whole candidate
        # list. Leaving num_ctx unset let the server truncate the candidates.
        "options": {
            "temperature": 0,
            "top_p": 1,
            "top_k": 1,
            "seed": 11,
            "repeat_penalty": 1.0,
            "num_ctx": 8192,
            "num_predict": 1024,
            **(options or {}),
        },
    }
    if response_schema:
        # A JSON *schema* constrains decoding to a valid answer. The blanket
        # `format: "json"` below is what made thinking models emit an empty {};
        # a schema does not have that failure mode and stops the model from
        # narrating its reasoning instead of answering.
        payload["format"] = response_schema
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
