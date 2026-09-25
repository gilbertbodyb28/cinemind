"""POST /history/sync reads one page per provider; that page must not replace a whole history.

The sync asked Trakt for its newest 50 entries and then deleted every stored
Trakt row before inserting them, so one press of Sync cut 10,210 rows to 50.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

import server
import providers.sync_lock as sync_lock

_REAL_CLIENT = httpx.AsyncClient


class _History:
    def __init__(self, stored):
        self.stored = dict(stored)
        self.deleted = []
        self.inserted = []

    async def count_documents(self, query):
        source = query.get("source")
        return self.stored.get(source, 0) if source else sum(self.stored.values())

    async def delete_many(self, query):
        self.deleted.append(query.get("source") or "all")

    async def insert_many(self, rows):
        self.inserted.extend(rows)


def _trakt_entries(count):
    return [
        {"watched_at": f"2026-09-{(index % 28) + 1:02d}T20:00:00Z",
         "movie": {"title": f"Film {index}", "year": 2020, "ids": {"tmdb": index}}}
        for index in range(count)
    ]


def _plex_payload(count):
    return {"MediaContainer": {"Metadata": [
        {"title": f"Plex {index}", "year": 2019, "type": "movie"} for index in range(count)
    ]}}


def _run(monkeypatch, conn, stored, responses):
    history = _History(stored)
    monkeypatch.setattr(server, "db", SimpleNamespace(
        connections=SimpleNamespace(find_one=AsyncMock(return_value=conn)),
        history=history,
    ))
    monkeypatch.setattr(server, "trakt_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(server, "enrich_history_posters", AsyncMock())
    monkeypatch.setattr(sync_lock, "record_sync_result", AsyncMock())

    def handler(request):
        for marker, payload in responses.items():
            if marker in str(request.url):
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={})

    monkeypatch.setattr(server.httpx, "AsyncClient", lambda *args, **kwargs: _REAL_CLIENT(
        transport=httpx.MockTransport(handler),
    ))
    result = asyncio.run(server.sync_history(SimpleNamespace(user_id="test-user")))
    return result, history


def test_a_full_trakt_page_keeps_the_larger_stored_history(monkeypatch):
    result, history = _run(
        monkeypatch, {"trakt_client_id": "cid"}, {"trakt": 10210},
        {"/sync/history": _trakt_entries(50)},
    )

    assert history.deleted == []
    assert history.inserted == []
    assert result["kept"] == {"trakt": 10210}
    assert result["sources"] == {"trakt": 50}


def test_a_short_trakt_answer_is_the_whole_history_and_replaces_it(monkeypatch):
    result, history = _run(
        monkeypatch, {"trakt_client_id": "cid"}, {"trakt": 10210},
        {"/sync/history": _trakt_entries(12)},
    )

    assert history.deleted == ["trakt"]
    assert len(history.inserted) == 12
    assert result["kept"] == {}


def test_a_full_page_still_fills_an_account_that_had_less(monkeypatch):
    _result, history = _run(
        monkeypatch, {"trakt_client_id": "cid"}, {"trakt": 20},
        {"/sync/history": _trakt_entries(50)},
    )

    assert history.deleted == ["trakt"]
    assert len(history.inserted) == 50


def test_a_plex_library_cut_at_the_page_keeps_the_stored_rows(monkeypatch):
    result, history = _run(
        monkeypatch, {"plex_url": "http://plex.local:32400", "plex_token": "tok"}, {"plex": 400},
        {"/library/all": _plex_payload(900)},
    )

    assert history.deleted == []
    assert result["kept"] == {"plex": 400}
    assert result["sources"] == {"plex": 50}


def test_nothing_connected_never_swaps_stored_history_for_demo_rows(monkeypatch):
    result, history = _run(monkeypatch, {}, {"trakt": 10210}, {})

    assert history.deleted == []
    assert history.inserted == []
    assert result["demo"] is False
    assert result["kept"] == {"all": 10210}


def test_nothing_connected_and_nothing_stored_still_seeds_the_demo_shelf(monkeypatch):
    result, history = _run(monkeypatch, {}, {}, {})

    assert result["demo"] is True
    assert history.deleted == ["all"]
    assert len(history.inserted) == len(server.DEMO_HISTORY)
