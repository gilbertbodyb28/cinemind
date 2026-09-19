"""AniList OAuth helpers and public connection flags (no live AniList calls)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from providers.anilist import parse_media_list_collection
from tests.test_mediamanager_approve import _import_server


def test_parse_media_list_collection_reads_entries():
    payload = {
        "data": {
            "MediaListCollection": {
                "lists": [
                    {
                        "entries": [
                            {
                                "status": "COMPLETED",
                                "score": 9,
                                "media": {
                                    "id": 1,
                                    "seasonYear": 2024,
                                    "genres": ["Drama"],
                                    "title": {"english": "Frieren", "romaji": "Sousou no Frieren"},
                                },
                            }
                        ]
                    }
                ]
            }
        }
    }
    items = parse_media_list_collection(payload)
    assert len(items) == 1
    assert items[0]["title"] == "Frieren"
    assert items[0]["source"] == "anilist"
    assert items[0]["anilist_id"] == 1


def test_connections_public_marks_anilist_without_leaking_token():
    from tests.test_mediamanager_approve import _import_server

    server = _import_server()
    public = server.connections_public(
        {
            "anilist_access_token": "secret-token",
            "anilist_username": "gilbert",
            "trakt_refresh_token": "trakt-secret",
            "trakt_access_token": "trakt-access",
        }
    )
    assert public.anilist_connected is True
    assert public.anilist_username == "gilbert"
    assert public.trakt_connected is True
    assert public.trakt_access_token is None
    dumped = public.model_dump()
    assert "secret-token" not in str(dumped)
    assert "anilist_access_token" not in dumped


def test_anilist_oauth_start_requires_client_id():
    from fastapi import HTTPException
    from tests.test_mediamanager_approve import _import_server

    server = _import_server()
    user = MagicMock(user_id="u1")

    async def run():
        with patch.object(server, "anilist_client_id", return_value=""):
            await server.anilist_oauth_start(user)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(run())
    assert exc.value.status_code == 503


def test_approve_request_sends_to_mediamanager():
    from tests.test_mediamanager_approve import _import_server

    server = _import_server()
    user = MagicMock(user_id="u1")
    fake_db = MagicMock()
    fake_db.requests.find_one = AsyncMock(
        side_effect=[
            {
                "id": "req1",
                "title": "Fight Club",
                "type": "movie",
                "year": 1999,
                "tmdb_id": 550,
                "status": "pending_approval",
            },
            {
                "id": "req1",
                "status": "approved",
                "title": "Fight Club",
            },
        ]
    )
    fake_db.connections.find_one = AsyncMock(
        return_value={
            "mediamanager_url": "http://mm:8000",
            "mediamanager_email": "admin@x.com",
            "mediamanager_password": "secret",
        }
    )
    fake_db.requests.update_one = AsyncMock()

    async def fake_add(hc, url, token, media_type, tmdb_id):
        assert media_type == "movie"
        assert tmdb_id == 550
        return True, False

    async def run():
        with (
            patch.object(server, "db", fake_db),
            patch("mediamanager_client.mediamanager_login", AsyncMock(return_value="jwt")),
            patch("mediamanager_client.mediamanager_add_title", fake_add),
        ):
            return await server.approve_request("req1", user)

    result = asyncio.run(run())
    assert result["ok"] is True
    assert result["status"] == "approved"
    assert result["created"] is True
    fake_db.requests.update_one.assert_awaited()


def test_approve_request_resends_when_already_approved():
    server = _import_server()
    user = MagicMock(user_id="u1")
    fake_db = MagicMock()
    fake_db.requests.find_one = AsyncMock(
        side_effect=[
            {
                "id": "req1",
                "title": "Zip Wire",
                "type": "movie",
                "year": 2026,
                "tmdb_id": 123,
                "status": "approved",
            },
            {
                "id": "req1",
                "status": "approved",
                "title": "Zip Wire",
            },
        ]
    )
    fake_db.connections.find_one = AsyncMock(
        return_value={
            "mediamanager_url": "http://mm:8000",
            "mediamanager_email": "admin@x.com",
            "mediamanager_password": "secret",
        }
    )
    fake_db.requests.update_one = AsyncMock()
    added = {"n": 0}

    async def fake_add(hc, url, token, media_type, tmdb_id):
        added["n"] += 1
        assert media_type == "movie"
        assert tmdb_id == 123
        return False, True

    async def run():
        with (
            patch.object(server, "db", fake_db),
            patch("mediamanager_client.mediamanager_login", AsyncMock(return_value="jwt")),
            patch("mediamanager_client.mediamanager_add_title", fake_add),
        ):
            return await server.approve_request("req1", user)

    result = asyncio.run(run())
    assert result["ok"] is True
    assert result["already_existed"] is True
    assert added["n"] == 1
    fake_db.requests.update_one.assert_awaited()


def test_approve_request_rejects_rejected_status():
    from fastapi import HTTPException

    server = _import_server()
    user = MagicMock(user_id="u1")
    fake_db = MagicMock()
    fake_db.requests.find_one = AsyncMock(
        return_value={"id": "req1", "title": "Nope", "status": "rejected"}
    )

    async def run():
        with patch.object(server, "db", fake_db):
            await server.approve_request("req1", user)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(run())
    assert exc.value.status_code == 422


def test_approve_request_falls_back_to_recommendation():
    server = _import_server()
    user = MagicMock(user_id="u1")
    fake_db = MagicMock()
    fake_db.requests.find_one = AsyncMock(return_value=None)
    fake_db.recommendations.find_one = AsyncMock(
        return_value={"id": "rec1", "title": "Zip Wire", "request_id": "req_stale"}
    )

    async def fake_approve(rec_id, current_user):
        assert rec_id == "rec1"
        assert current_user is user
        return {"ok": True, "created": True, "already_existed": False, "tmdb_id": 9, "media_type": "movie"}

    async def run():
        with (
            patch.object(server, "db", fake_db),
            patch.object(server, "approve_to_mediamanager", fake_approve),
        ):
            return await server.approve_request("req_stale", user)

    result = asyncio.run(run())
    assert result["ok"] is True
    assert result["tmdb_id"] == 9


def test_library_item_from_request_fills_tmdb_id():
    server = _import_server()
    fake_db = MagicMock()
    fake_db.recommendations.find_one = AsyncMock(
        return_value={"tmdb_id": 1396, "type": "show", "year": 2008, "title": "Breaking Bad"}
    )

    async def run():
        with patch.object(server, "db", fake_db):
            return await server.library_item_from_request(
                "u1",
                {"id": "req1", "title": "Breaking Bad", "recommendation_id": "r2", "status": "pending_approval"},
            )

    item = asyncio.run(run())
    assert item["tmdb_id"] == 1396
    assert item["type"] == "show"


def test_list_requests_hides_rejected():
    server = _import_server()
    user = MagicMock(user_id="u1")
    fake_db = MagicMock()
    fake_db.requests.find.return_value.to_list = AsyncMock(
        return_value=[
            {"id": "a", "status": "pending_approval", "title": "Keep", "updated_at": "2"},
            {"id": "b", "status": "rejected", "title": "Gone", "updated_at": "3"},
            {"id": "c", "status": "approved", "title": "Done", "updated_at": "1"},
        ]
    )
    fake_db.recommendations.find.return_value.to_list = AsyncMock(return_value=[])
    fake_db.taste_profiles.find_one = AsyncMock(return_value={})

    async def run():
        with patch.object(server, "db", fake_db):
            return await server.list_requests(user)

    rows = asyncio.run(run())
    assert [row["id"] for row in rows] == ["a", "c"]


def test_list_requests_fills_match_score_from_recommendation():
    server = _import_server()
    user = MagicMock(user_id="u1")
    fake_db = MagicMock()
    fake_db.requests.find.return_value.to_list = AsyncMock(
        return_value=[
            {"id": "a", "status": "pending_approval", "recommendation_id": "rec1", "updated_at": "2"},
            {"id": "b", "status": "pending_approval", "match_score": 81, "updated_at": "1"},
        ]
    )
    fake_db.recommendations.find.return_value.to_list = AsyncMock(
        return_value=[{"id": "rec1", "match_score": 94, "title": "Keep", "year": 2024}]
    )
    fake_db.taste_profiles.find_one = AsyncMock(return_value={})

    async def run():
        with patch.object(server, "db", fake_db):
            return await server.list_requests(user)

    rows = asyncio.run(run())
    by_id = {row["id"]: row["match_score"] for row in rows}
    assert by_id["a"] == 94
    assert by_id["b"] == 81


def test_list_requests_fills_match_score_from_title():
    server = _import_server()
    user = MagicMock(user_id="u1")
    fake_db = MagicMock()
    fake_db.requests.find.return_value.to_list = AsyncMock(
        return_value=[{"id": "a", "status": "pending_approval", "title": "Zip Wire", "year": 2026, "updated_at": "1"}]
    )
    fake_db.recommendations.find.return_value.to_list = AsyncMock(
        return_value=[{"id": "rec9", "title": "Zip Wire", "year": 2026, "match_score": 88}]
    )
    fake_db.taste_profiles.find_one = AsyncMock(return_value={})

    async def run():
        with patch.object(server, "db", fake_db):
            return await server.list_requests(user)

    rows = asyncio.run(run())
    assert rows[0]["match_score"] == 88


def test_clamp_match_score():
    server = _import_server()
    assert server.clamp_match_score(87.4) == 87
    assert server.clamp_match_score("140") == 100
    assert server.clamp_match_score(None) is None


def test_bulk_reject_marks_selected_ids():
    server = _import_server()
    user = MagicMock(user_id="u1")
    fake_db = MagicMock()
    fake_db.requests.find.return_value.to_list = AsyncMock(
        return_value=[
            {"id": "r1", "status": "pending_approval", "recommendation_id": "rec1"},
            {"id": "r2", "status": "pending_approval"},
        ]
    )
    fake_db.requests.update_many = AsyncMock()
    fake_db.recommendations.update_many = AsyncMock()

    async def run():
        with patch.object(server, "db", fake_db):
            return await server.bulk_requests(
                server.BulkRequestsBody(ids=["r1", "r2"], action="reject"),
                user,
            )

    result = asyncio.run(run())
    assert result["ok"] is True
    assert result["action"] == "reject"
    assert set(result["ids"]) == {"r1", "r2"}
    fake_db.requests.update_many.assert_awaited()
    fake_db.recommendations.update_many.assert_awaited()
