"""Tests for Claude Sonnet 5 fallback (emergentintegrations) in CineMind AI."""
import pytest

from conftest import BASE_URL

LLM_TIMEOUT = 180


# ---------- module: root / existing endpoints ----------
class TestExistingEndpoints:
    def test_root(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert data["app"] == "CineMind AI"
        assert data["status"] == "ok"

    def test_auth_me_requires_token(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/auth/me", timeout=30)
        assert r.status_code == 401

    def test_auth_me_with_seeded_session(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/auth/me", timeout=30)
        assert r.status_code == 200
        assert r.json()["email"] == "test.user.claude1@example.com"

    def test_history_stats(self, auth_client):
        # ensure history seeded
        h = auth_client.get(f"{BASE_URL}/api/history", timeout=60)
        assert h.status_code == 200
        assert len(h.json()) > 0

        r = auth_client.get(f"{BASE_URL}/api/history/stats", timeout=30)
        assert r.status_code == 200
        s = r.json()
        assert s["total"] == len(h.json())
        assert s["movies"] + s["shows"] == s["total"]
        assert isinstance(s["genres"], list) and len(s["genres"]) > 0
        assert isinstance(s["sources"], list)


# ---------- module: taste profile via Claude ----------
class TestTasteProfileClaude:
    def test_generate_uses_claude_when_no_ollama(self, auth_client, no_ollama):
        r = auth_client.post(f"{BASE_URL}/api/taste-profile/generate", timeout=LLM_TIMEOUT)
        assert r.status_code == 200, r.text
        p = r.json()
        assert p.get("provider") == "claude", f"expected claude, got {p.get('provider')}: {p}"
        assert p.get("used_demo") is False
        dna = p.get("cinematic_dna")
        assert isinstance(dna, str) and len(dna) > 40
        # not the hardcoded demo string
        assert not dna.startswith("You gravitate toward cerebral, atmospheric storytelling")
        assert isinstance(p.get("key_tropes"), list) and len(p["key_tropes"]) > 0
        assert p.get("mood")
        assert isinstance(p.get("narrative_complexity"), int)
        assert isinstance(p.get("top_genres"), list)

    def test_generated_profile_persisted(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/taste-profile", timeout=30)
        assert r.status_code == 200
        p = r.json()
        assert p is not None
        assert p["provider"] == "claude"
        assert len(p["cinematic_dna"]) > 40


# ---------- module: recommendations via Claude ----------
class TestRecommendationsClaude:
    def test_generate_uses_claude_when_no_ollama(self, auth_client, no_ollama):
        r = auth_client.post(f"{BASE_URL}/api/recommendations/generate", timeout=LLM_TIMEOUT)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["provider"] == "claude", data
        assert data["demo"] is False
        assert data["count"] == 8, data

    def test_generated_recs_have_content(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/recommendations", timeout=30)
        assert r.status_code == 200
        docs = r.json()
        assert len(docs) >= 8
        claude_docs = [d for d in docs if d.get("provider") == "claude"]
        assert len(claude_docs) >= 8, f"expected 8 claude recs, got {len(claude_docs)}"
        for d in claude_docs:
            assert d["title"] and d["title"] != "Untitled"
            assert d["why"] and len(d["why"]) > 20
            assert d["synopsis"] and len(d["synopsis"]) > 20
            assert isinstance(d["genres"], list)
            assert 1900 < d["year"] < 2035
            assert 0 <= d["match_score"] <= 100
            assert "_id" not in d


# ---------- module: unreachable Ollama -> Claude fallback ----------
class TestUnreachableOllamaFallback:
    @pytest.fixture(scope="class", autouse=True)
    def bad_ollama(self, auth_client):
        r = auth_client.put(
            f"{BASE_URL}/api/connections",
            json={"ollama_url": "http://127.0.0.1:9", "ollama_model": "llama3.2"},
            timeout=30,
        )
        assert r.status_code == 200
        yield
        auth_client.put(f"{BASE_URL}/api/connections", json={"ollama_url": None, "ollama_model": "llama3.2"}, timeout=30)

    def test_taste_falls_back_to_claude(self, auth_client):
        r = auth_client.post(f"{BASE_URL}/api/taste-profile/generate", timeout=LLM_TIMEOUT)
        assert r.status_code == 200, r.text
        p = r.json()
        assert p["provider"] == "claude", p
        assert p["used_demo"] is False

    def test_recs_fall_back_to_claude(self, auth_client):
        r = auth_client.post(f"{BASE_URL}/api/recommendations/generate", timeout=LLM_TIMEOUT)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["provider"] == "claude", d
        assert d["count"] == 8


# ---------- module: save / dismiss flows ----------
class TestSaveDismissFlows:
    def test_save_and_unsave(self, auth_client):
        docs = auth_client.get(f"{BASE_URL}/api/recommendations", timeout=30).json()
        rec_id = docs[0]["id"]

        assert auth_client.post(f"{BASE_URL}/api/recommendations/{rec_id}/save", timeout=30).status_code == 200
        saved = auth_client.get(f"{BASE_URL}/api/recommendations/saved", timeout=30).json()
        assert any(d["id"] == rec_id and d["saved"] is True for d in saved)

        assert auth_client.post(f"{BASE_URL}/api/recommendations/{rec_id}/unsave", timeout=30).status_code == 200
        saved = auth_client.get(f"{BASE_URL}/api/recommendations/saved", timeout=30).json()
        assert not any(d["id"] == rec_id for d in saved)

    def test_dismiss_removes_from_list(self, auth_client):
        docs = auth_client.get(f"{BASE_URL}/api/recommendations", timeout=30).json()
        before = len(docs)
        rec_id = docs[-1]["id"]
        assert auth_client.post(f"{BASE_URL}/api/recommendations/{rec_id}/dismiss", timeout=30).status_code == 200
        after = auth_client.get(f"{BASE_URL}/api/recommendations", timeout=30).json()
        assert len(after) == before - 1
        assert not any(d["id"] == rec_id for d in after)
