"""Ollama is the only LLM path; Claude keys must not be selected.

These assert against the configured default (`OLLAMA_MODEL`) rather than a
hard-coded model name, so changing the deployment's model in `.env` does not
break the behavioural guarantees this file exists to protect.
"""
from config import (
    CLAUDE_MODEL_KEYS,
    LEGACY_OLLAMA_MODELS,
    OLLAMA_MODEL,
    effective_ollama_model,
    is_claude_model,
    is_legacy_ollama_model,
    resolve_model,
)


def test_default_model_is_a_real_ollama_model():
    assert OLLAMA_MODEL
    assert not is_claude_model(OLLAMA_MODEL)
    assert OLLAMA_MODEL.lower() not in LEGACY_OLLAMA_MODELS


def test_empty_connections_use_configured_default():
    url, model = resolve_model({})
    assert url.endswith("11434")
    assert model == OLLAMA_MODEL


def test_claude_override_is_ignored():
    url, model = resolve_model(
        {"ollama_url": "http://localhost:11434", "ollama_model": "llama3.2"},
        "sonnet-5",
    )
    assert model == OLLAMA_MODEL
    assert "claude" not in model.lower()


def test_every_claude_key_falls_back_to_default():
    for key in CLAUDE_MODEL_KEYS:
        assert effective_ollama_model({"ollama_model": key}) == OLLAMA_MODEL


def test_legacy_llama_default_becomes_configured_default():
    assert effective_ollama_model({"ollama_model": "llama3.2"}) == OLLAMA_MODEL


def test_explicit_custom_ollama_model_is_kept():
    assert effective_ollama_model({"ollama_model": "mistral"}) == "mistral"


def test_retired_default_model_falls_back_to_configured_default():
    """A connection pinned to a model the picker no longer lists is unreachable.

    qwen3:14b was the previous shipped default and is not in MODELS, so the user
    could neither see nor change it while it kept overriding the configured one.
    """
    assert effective_ollama_model({"ollama_model": "qwen3:14b"}) == OLLAMA_MODEL


def test_retired_model_with_latest_tag_also_falls_back():
    """`ollama pull qwen-suggestarr` is reported by the API as `...:latest`.

    The retired set is written without the tag, so an exact-string check let the
    tagged spelling through: picking it in the live model picker stored it and
    the account kept running a model that had been deliberately retired.
    """
    for name in ("qwen-suggestarr:latest", "llama3.2:latest", "QWEN-SUGGESTARR:LATEST"):
        assert effective_ollama_model({"ollama_model": name}) == OLLAMA_MODEL


def test_legacy_check_is_tag_insensitive_both_ways():
    assert is_legacy_ollama_model("qwen-suggestarr")
    assert is_legacy_ollama_model("qwen-suggestarr:latest")
    assert is_legacy_ollama_model("qwen2.5:7b-instruct-q6_K")
    assert not is_legacy_ollama_model("gemma4:12b-it-qat")
    assert not is_legacy_ollama_model("qwen3.5:9b")
    assert not is_legacy_ollama_model("")
    assert not is_legacy_ollama_model(None)
