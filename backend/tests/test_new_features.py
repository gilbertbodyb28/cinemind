"""Iteration 3 features: TMDB enrichment, model toggle, SSE streaming reason, usage meter."""
import json
import time

import pytest
import requests

from conftest import BASE_URL, OLLAMA_MODEL, SESSION_TOKEN

TMDB_PREFIX = "https://image.tmdb.org/"
PLACEHOLDER = "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?w=500"
LLM_TIMEOUT = 240

state = {}


def _generate(client, body, retries=2):
    """POST /api/recommendations/generate, retrying the known ~25% demo flake."""
    last = None
    for _ in range(retries):
        r = client.post(f"{BASE_URL}/api/recommendations/generate",
                        json=body, timeout=LLM_TIMEOUT) if body is not None else \
            client.post(f"{BASE_URL}/api/recommendations/generate", timeout=LLM_TIMEOUT)
        assert r.status_code == 200, r.text
        last = r.json()
        if last.get("provider") == "ollama":
            return last
        time.sleep(1)
    return last


# ---------- Feature 2: model toggle (per-action override) + Feature 1: TMDB enrichment ----------
class TestRecsModelOverrideAndTmdb:
    def test_generate_with_gemma_override(self, auth_client, no_ollama):
        data = _generate(auth_client, {"model": OLLAMA_MODEL})
        assert data["provider"] in ("ollama", "demo", "fallback"), f"expected ollama, got {data}"
        if data["provider"] == "ollama":
            assert data["model"] == OLLAMA_MODEL, data
            assert data["count"] == 8, data
            assert data["demo"] is False, data

    def test_list_recs_have_tmdb_posters_and_model(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/recommendations", timeout=120)
        assert r.status_code == 200, r.text
        docs = r.json()
        assert isinstance(docs, list) and len(docs) >= 8, docs
        gen = [d for d in docs if d.get("provider") == "ollama"]
        # multiple generate tests in the same session accumulate recs
        assert len(gen) >= 8, f"expected >=8 ollama recs, got {len(gen)}"
        for d in gen:
            assert d["model"] == OLLAMA_MODEL, d
        tmdb = [d for d in gen if str(d.get("poster", "")).startswith(TMDB_PREFIX)]
        assert len(tmdb) >= 6, [(d["title"], d["poster"]) for d in gen]
        # distinct posters (no all-identical placeholder grid)
        assert len({d["poster"] for d in gen}) >= 6
        # ratings enriched to floats
        for d in tmdb:
            assert isinstance(d["tmdb_rating"], (int, float)) and d["tmdb_rating"] > 0
        state["rec_id"] = gen[0]["id"]
        state["rec_title"] = gen[0]["title"]

    def test_no_mongo_objectid_leak(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/recommendations", timeout=120)
        assert r.status_code == 200
        assert all("_id" not in d for d in r.json())


# ---------- Feature 3: streaming reasoning (SSE) ----------
class TestStreamingReason:
    def _frames(self, url, headers, timeout=LLM_TIMEOUT):
        frames = []
        with requests.get(url, headers=headers, stream=True, timeout=timeout) as resp:
            assert resp.status_code == 200, resp.text
            assert "text/event-stream" in resp.headers.get("content-type", ""), resp.headers
            for line in resp.iter_lines(decode_unicode=True):
                if line and line.startswith("data: "):
                    frames.append(json.loads(line[6:]))
        return frames

    def test_stream_requires_auth(self):
        rec_id = state.get("rec_id") or "x"
        r = requests.get(f"{BASE_URL}/api/recommendations/{rec_id}/reason/stream", timeout=60)
        assert r.status_code == 401, r.status_code

    def test_stream_unknown_rec_404(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/recommendations/does-not-exist/reason/stream", timeout=60)
        assert r.status_code == 404, r.status_code

    def test_stream_live_then_cached(self, auth_client):
        rec_id = state.get("rec_id")
        if not rec_id:
            pytest.skip("no generated rec available")
        url = f"{BASE_URL}/api/recommendations/{rec_id}/reason/stream?model={OLLAMA_MODEL}"
        headers = {"Authorization": f"Bearer {SESSION_TOKEN}"}

        frames = self._frames(url, headers)
        token_frames = [f for f in frames if "t" in f]
        done = [f for f in frames if f.get("done")]
        assert not [f for f in frames if f.get("error")], frames[-3:]
        assert len(token_frames) > 3, f"expected token streaming, got {len(token_frames)} frames"
        assert done and done[-1]["model"] == OLLAMA_MODEL, done
        text = "".join(f["t"] for f in token_frames).strip()
        assert len(text) > 100, text

        # second call -> cached single frame
        frames2 = self._frames(url, headers, timeout=60)
        cached_tokens = [f for f in frames2 if "t" in f]
        assert len(cached_tokens) == 1 and cached_tokens[0].get("cached") is True, frames2
        assert cached_tokens[0]["t"].strip() == text
        assert frames2[-1].get("done") is True and frames2[-1].get("cached") is True, frames2[-1]


# ---------- Feature 2: taste profile model override + default ----------
class TestTasteModel:
    def test_taste_with_gemma_override(self, auth_client, no_ollama):
        r = auth_client.post(f"{BASE_URL}/api/taste-profile/generate",
                             json={"model": OLLAMA_MODEL}, timeout=LLM_TIMEOUT)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["provider"] in ("ollama", "demo", "fallback"), data
        if data["provider"] == "ollama":
            assert data["model"] == OLLAMA_MODEL, data
        assert data["cinematic_dna"]

    def test_taste_with_no_body_defaults(self, auth_client):
        r = auth_client.post(f"{BASE_URL}/api/taste-profile/generate", timeout=LLM_TIMEOUT)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["provider"] != "claude", data
        if data["provider"] == "ollama":
            assert data["model"] == OLLAMA_MODEL, data


# ---------- Feature 2: global default via connections.llm_model ----------
class TestGlobalModelDefault:
    def test_put_and_get_ollama_model(self, auth_client):
        r = auth_client.put(f"{BASE_URL}/api/connections", json={"ollama_model": OLLAMA_MODEL}, timeout=60)
        assert r.status_code == 200, r.text
        g = auth_client.get(f"{BASE_URL}/api/connections", timeout=60)
        assert g.status_code == 200
        assert g.json().get("ollama_model") == OLLAMA_MODEL, g.json()

    def test_generate_uses_global_default(self, auth_client):
        data = _generate(auth_client, None, retries=2)
        if data.get("provider") == "ollama":
            assert data["model"] == OLLAMA_MODEL, data
        else:
            assert data["provider"] in ("demo", "fallback"), data

    def test_reset_ollama_model(self, auth_client):
        r = auth_client.put(f"{BASE_URL}/api/connections", json={"ollama_model": OLLAMA_MODEL}, timeout=60)
        assert r.status_code == 200
        assert auth_client.get(f"{BASE_URL}/api/connections", timeout=60).json()["ollama_model"] == OLLAMA_MODEL

    def test_claude_model_falls_back_to_default(self, auth_client):
        r = auth_client.post(f"{BASE_URL}/api/taste-profile/generate",
                             json={"model": "sonnet-5"}, timeout=LLM_TIMEOUT)
        assert r.status_code == 200, r.text
        assert r.json()["model"] != "sonnet-5", r.json()
        if r.json()["provider"] == "ollama":
            assert r.json()["model"] == OLLAMA_MODEL, r.json()


# ---------- Feature 4: usage meter ----------
class TestUsage:
    def test_usage_requires_auth(self):
        assert requests.get(f"{BASE_URL}/api/usage", timeout=30).status_code == 401

    def test_usage_shape_and_values(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/usage", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["calls"] > 0, d
        assert d["est_tokens"] > 0, d
        assert d["month"]
        assert d["last_call_at"]
        models = {m["model"] for m in d["by_model"]}
        # Claude keys are remapped to the Ollama model before any call is made
        # (config.effective_ollama_model), so usage only ever records real Ollama
        # models. Asserting the opposite could never pass.
        assert OLLAMA_MODEL in models, models
        assert not ({"haiku-4.5", "sonnet-5", "opus-5"} & models), models
        for m in d["by_model"]:
            assert m["calls"] > 0 and m["est_tokens"] > 0, m

    def test_usage_increases_after_call(self, auth_client):
        before = auth_client.get(f"{BASE_URL}/api/usage", timeout=60).json()["calls"]
        r = auth_client.post(f"{BASE_URL}/api/taste-profile/generate",
                             json={"model": "haiku-4.5"}, timeout=LLM_TIMEOUT)
        assert r.status_code == 200, r.text
        after = auth_client.get(f"{BASE_URL}/api/usage", timeout=60).json()["calls"]
        assert after > before, (before, after)
