"""Ollama qwen3:14b is the only LLM path; Claude keys must not be selected."""
from config import OLLAMA_MODEL, effective_ollama_model, resolve_model


def test_default_model_is_qwen3():
    assert OLLAMA_MODEL == "qwen3:14b"


def test_empty_connections_use_localhost_qwen():
    url, model = resolve_model({})
    assert url.endswith("11434")
    assert model == "qwen3:14b"


def test_claude_override_is_ignored():
    url, model = resolve_model(
        {"ollama_url": "http://localhost:11434", "ollama_model": "llama3.2"},
        "sonnet-5",
    )
    assert model == "qwen3:14b"
    assert "claude" not in model.lower()


def test_legacy_llama_default_becomes_qwen():
    assert effective_ollama_model({"ollama_model": "llama3.2"}) == "qwen3:14b"


def test_explicit_custom_ollama_model_is_kept():
    assert effective_ollama_model({"ollama_model": "mistral"}) == "mistral"
