"""MediaManager connection + approve helpers (unit tests with mocked HTTP)."""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mediamanager_client import (
    media_type_for_mediamanager,
    mediamanager_add_title,
    mediamanager_is_configured,
    mediamanager_login,
    mediamanager_search_external_id,
    mediamanager_token,
    normalize_mediamanager_url,
    send_item_to_library,
    clear_mediamanager_token_cache,
)


def test_media_type_for_mediamanager_routes_tv_and_movies():
    assert media_type_for_mediamanager({"type": "movie"}) == "movie"
    assert media_type_for_mediamanager({"type": "show"}) == "show"
    assert media_type_for_mediamanager({"type": "tv"}) == "show"
    assert media_type_for_mediamanager({"media_type": "anime"}) == "show"
    assert media_type_for_mediamanager({"type": "series"}) == "show"


def test_normalize_mediamanager_url_strips_slash():
    assert normalize_mediamanager_url("http://localhost:8000/") == "http://localhost:8000"


def test_normalize_mediamanager_url_rewrites_vite_port():
    assert normalize_mediamanager_url("http://127.0.0.1:5173") == "http://127.0.0.1:8000"
    assert normalize_mediamanager_url("http://localhost:5173/") == "http://localhost:8000"


def test_mediamanager_is_configured_requires_all_fields():
    assert not mediamanager_is_configured({})
    assert not mediamanager_is_configured(
        {"mediamanager_url": "http://x", "mediamanager_email": "a@b.c"}
    )
    assert mediamanager_is_configured(
        {
            "mediamanager_url": "http://x",
            "mediamanager_email": "a@b.c",
            "mediamanager_password": "secret",
        }
    )


def test_mediamanager_login_returns_token():
    async def run():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"access_token": "jwt-token", "token_type": "bearer"}
            )
        )
        async with httpx.AsyncClient(transport=transport) as hc:
            return await mediamanager_login(hc, "http://mm:8000", "admin@x.com", "pw")

    assert asyncio.run(run()) == "jwt-token"


def test_mediamanager_login_rejects_bad_credentials():
    from fastapi import HTTPException

    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"detail": "Bad"}))
        async with httpx.AsyncClient(transport=transport) as hc:
            await mediamanager_login(hc, "http://mm:8000", "admin@x.com", "bad")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(run())
    assert exc.value.status_code == 409


def test_mediamanager_add_movie_created():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/movies"
        assert request.url.params["movie_id"] == "550"
        assert request.url.params["metadata_provider"] == "tmdb"
        assert "monitored" not in request.url.params
        assert "search_now" not in request.url.params
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(201, json={"id": "1", "name": "Fight Club"})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as hc:
            return await mediamanager_add_title(hc, "http://mm:8000", "tok", "movie", 550)

    created, existed = asyncio.run(run())
    assert created is True
    assert existed is False


def test_mediamanager_add_show_already_exists():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/tv/shows"
        assert request.url.params["show_id"] == "1396"
        assert request.url.params["metadata_provider"] == "tmdb"
        assert request.url.params["monitoring"] == "unmonitored"
        assert "monitor_scope" not in request.url.params
        assert "discovery_provider" not in request.url.params
        assert "search_now" not in request.url.params
        return httpx.Response(200, json={"id": "2", "name": "Breaking Bad"})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as hc:
            return await mediamanager_add_title(hc, "http://mm:8000/", "tok", "show", 1396)

    created, existed = asyncio.run(run())
    assert created is False
    assert existed is True


def _install_emergent_stub() -> None:
    if "emergentintegrations.llm.chat" in sys.modules:
        return
    root = ModuleType("emergentintegrations")
    llm = ModuleType("emergentintegrations.llm")
    chat = ModuleType("emergentintegrations.llm.chat")
    chat.LlmChat = object
    chat.UserMessage = object
    chat.TextDelta = object
    chat.StreamDone = object
    sys.modules["emergentintegrations"] = root
    sys.modules["emergentintegrations.llm"] = llm
    sys.modules["emergentintegrations.llm.chat"] = chat


def _import_server():
    import os

    os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
    os.environ.setdefault("DB_NAME", "cinemind_test_mm")
    os.environ.setdefault("TMDB_API_KEY", "")
    _install_emergent_stub()
    import server  # noqa: WPS433

    return server


def test_approve_endpoint_requires_mediamanager_config():
    from fastapi import HTTPException

    server = _import_server()
    user = MagicMock(user_id="u1")
    fake_db = MagicMock()
    fake_db.recommendations.find_one = AsyncMock(
        return_value={"id": "r1", "title": "X", "type": "movie", "year": 2020}
    )
    fake_db.connections.find_one = AsyncMock(return_value={})

    async def run():
        with patch.object(server, "db", fake_db):
            await server.approve_to_mediamanager("r1", user)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(run())
    assert exc.value.status_code == 409
    assert "Connect MediaManager" in str(exc.value.detail)


def test_approve_endpoint_movie_success():
    clear_mediamanager_token_cache()
    server = _import_server()
    user = MagicMock(user_id="u1")
    fake_db = MagicMock()
    fake_db.recommendations.find_one = AsyncMock(
        return_value={
            "id": "r1",
            "title": "Fight Club",
            "type": "movie",
            "year": 1999,
            "tmdb_id": 550,
        }
    )
    fake_db.connections.find_one = AsyncMock(
        return_value={
            "mediamanager_url": "http://mm:8000",
            "mediamanager_email": "admin@x.com",
            "mediamanager_password": "secret",
        }
    )
    fake_db.recommendations.update_one = AsyncMock()
    fake_db.requests.update_one = AsyncMock()

    login_calls = {"n": 0}
    add_calls = {"n": 0}

    async def fake_login(hc, url, email, password):
        login_calls["n"] += 1
        return "jwt"

    async def fake_add(hc, url, token, media_type, tmdb_id):
        add_calls["n"] += 1
        assert media_type == "movie"
        assert tmdb_id == 550
        return True, False

    async def run():
        with (
            patch.object(server, "db", fake_db),
            patch("mediamanager_client.mediamanager_login", fake_login),
            patch("mediamanager_client.mediamanager_add_title", fake_add),
        ):
            return await server.approve_to_mediamanager("r1", user)

    result = asyncio.run(run())
    assert result == {
        "ok": True,
        "created": True,
        "already_existed": False,
        "tmdb_id": 550,
        "media_type": "movie",
    }
    assert login_calls["n"] == 1
    assert add_calls["n"] == 1
    fake_db.recommendations.update_one.assert_awaited()


def test_approve_endpoint_show_success():
    clear_mediamanager_token_cache()
    server = _import_server()
    user = MagicMock(user_id="u1")
    fake_db = MagicMock()
    fake_db.recommendations.find_one = AsyncMock(
        return_value={
            "id": "r2",
            "title": "Breaking Bad",
            "type": "show",
            "year": 2008,
            "tmdb_id": 1396,
        }
    )
    fake_db.connections.find_one = AsyncMock(
        return_value={
            "mediamanager_url": "http://mm:8000",
            "mediamanager_email": "admin@x.com",
            "mediamanager_password": "secret",
        }
    )
    fake_db.recommendations.update_one = AsyncMock()
    fake_db.requests.update_one = AsyncMock()

    async def fake_add(hc, url, token, media_type, tmdb_id):
        assert media_type == "show"
        assert tmdb_id == 1396
        return False, True

    async def run():
        with (
            patch.object(server, "db", fake_db),
            patch("mediamanager_client.mediamanager_login", AsyncMock(return_value="jwt")),
            patch("mediamanager_client.mediamanager_add_title", fake_add),
        ):
            return await server.approve_to_mediamanager("r2", user)

    result = asyncio.run(run())
    assert result["media_type"] == "show"
    assert result["already_existed"] is True
    assert result["created"] is False


def test_mediamanager_search_picks_matching_year():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/movies/search"
        assert "Oppenheimer" in request.url.params["query"]
        return httpx.Response(
            200,
            json=[
                {"name": "Oppenheimer", "external_id": 1, "year": 1980},
                {"name": "Oppenheimer", "external_id": 872585, "year": 2023},
            ],
        )

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as hc:
            return await mediamanager_search_external_id(
                hc, "http://mm:8000", "tok", "movie", "Oppenheimer", 2023
            )

    assert asyncio.run(run()) == 872585


def test_send_item_to_library_searches_mediamanager_not_tmdb():
    from mediamanager_client import clear_mediamanager_token_cache

    clear_mediamanager_token_cache()
    conn = {
        "mediamanager_url": "http://127.0.0.1:5173",
        "mediamanager_email": "admin@x.com",
        "mediamanager_password": "secret",
    }

    async def fake_login(hc, url, email, password):
        assert url == "http://127.0.0.1:8000"
        return "jwt"

    async def fake_search(hc, url, token, media_type, title, year=None):
        assert media_type == "movie"
        assert title == "Oppenheimer"
        return 872585

    async def fake_add(hc, url, token, media_type, tmdb_id):
        assert tmdb_id == 872585
        return True, False

    async def run():
        with (
            patch("mediamanager_client.mediamanager_login", fake_login),
            patch("mediamanager_client.mediamanager_search_external_id", fake_search),
            patch("mediamanager_client.mediamanager_add_title", fake_add),
            patch("providers.tmdb.tmdb_lookup", AsyncMock()) as lookup,
        ):
            result = await send_item_to_library(
                {"title": "Oppenheimer", "year": 2023, "type": "movie"},
                conn,
            )
            lookup.assert_not_called()
            return result

    result = asyncio.run(run())
    assert result["tmdb_id"] == 872585
    assert result["created"] is True


def test_send_item_to_library_timeout_is_502():
    from fastapi import HTTPException

    clear_mediamanager_token_cache()
    conn = {
        "mediamanager_url": "http://127.0.0.1:8000",
        "mediamanager_email": "admin@x.com",
        "mediamanager_password": "secret",
    }

    async def boom(*_args, **_kwargs):
        raise httpx.ReadTimeout("timed out")

    async def run():
        with patch("mediamanager_client.mediamanager_login", boom):
            await send_item_to_library({"title": "X", "type": "movie", "tmdb_id": 1}, conn)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(run())
    assert exc.value.status_code == 502
    assert "8000" in str(exc.value.detail)


def test_mediamanager_token_is_cached():
    clear_mediamanager_token_cache()
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            posts["n"] += 1
            return httpx.Response(200, json={"access_token": "jwt-cached"})
        return httpx.Response(404)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as hc:
            first = await mediamanager_token(hc, "http://mm:8000", "a@b.c", "pw")
            second = await mediamanager_token(hc, "http://mm:8000", "a@b.c", "pw")
            return first, second

    assert asyncio.run(run()) == ("jwt-cached", "jwt-cached")
    assert posts["n"] == 1

