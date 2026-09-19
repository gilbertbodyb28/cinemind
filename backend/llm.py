"""Ollama-then-deterministic generation helpers."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import json
import logging
import re

from config import resolve_model
from database import db
from providers.ollama import call_ollama


def extract_json(raw: str) -> Optional[Any]:
    if not raw:
        return None
    # Strip Qwen/DeepSeek-style think blocks before parsing.
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.I).strip()
    for candidate in (cleaned, raw):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except Exception:
            pass
        match = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", candidate)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                continue
    return None


async def record_usage(user_id: str, model_key: str, action: str, prompt_chars: int, output_chars: int) -> None:
    await db.llm_usage.insert_one({
        "user_id": user_id,
        "model": model_key,
        "action": action,
        "est_tokens": (prompt_chars + output_chars) // 4,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


async def generate_with_llm(
    conn: Dict[str, Any],
    session_id: str,
    system: str,
    prompt: str,
    user_id: str = "",
    action: str = "generate",
    model_override: Optional[str] = None,
) -> tuple[Optional[Any], str, str]:
    """Ask Ollama for structured JSON, retrying once on unparseable output.

    Returns (parsed_json_or_none, provider, model). An Ollama outage is not fatal:
    callers fall back to deterministic output when provider == "fallback".
    """
    del session_id  # reserved for later request tracing
    url, model = resolve_model(conn, model_override)
    for attempt in (1, 2):
        raw = await call_ollama(url, model, prompt, system)
        if raw is None:
            break
        parsed = extract_json(raw)
        # Empty object is a common failed structured-output artifact on thinking models.
        if parsed is not None and parsed != {}:
            await record_usage(user_id, model, action, len(prompt) + len(system), len(raw))
            return parsed, "ollama", model
        logging.warning("Ollama unparseable (attempt %s): %s", attempt, (raw or "")[:400])
    return None, "fallback", model
