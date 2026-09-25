"""A job run never changes a decision, and a title is queued once (HANDOFF.md, omgång 5)."""

import asyncio
from types import SimpleNamespace

import request_providers
from recommendation.exclusion_engine import apply_exclusions, build_exclusion_context


class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def __aiter__(self):
        self._iter = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class FakeRequests:
    def __init__(self, rows):
        self.rows = [dict(row) for row in rows]

    def _match(self, query):
        return [row for row in self.rows if all(row.get(key) == value for key, value in query.items())]

    async def find_one(self, query, projection=None):
        found = self._match(query)
        return dict(found[0]) if found else None

    def find(self, query, projection=None):
        return _Cursor(dict(row) for row in self._match(query))

    async def update_one(self, query, update, upsert=False):
        found = self._match(query)
        if not found and upsert:
            row = {**query, **update.get("$setOnInsert", {})}
            self.rows.append(row)
            found = [row]
        for row in found[:1]:
            row.update(update.get("$set", {}))
            for key, step in update.get("$inc", {}).items():
                row[key] = row.get(key, 0) + step


def _submit(monkeypatch, rows, item, status="pending_approval"):
    fake = FakeRequests(rows)
    monkeypatch.setattr(request_providers, "db", SimpleNamespace(requests=fake))
    result = asyncio.run(request_providers.LocalRequestProvider().submit("u", item, status))
    return result, fake.rows


def test_a_rejected_title_suggested_again_stays_rejected(monkeypatch):
    rows = [{"user_id": "u", "id": "req_1", "title": "Monk", "year": 2002, "type": "show", "status": "rejected",
             "updated_at": "2026-09-01"}]
    result, stored = _submit(monkeypatch, rows, {"title": "Monk", "year": 2002, "type": "show", "match_score": 90})
    assert result["status"] == "rejected" and stored == rows


def test_a_title_waiting_in_the_queue_keeps_its_place_when_suggested_again(monkeypatch):
    rows = [{"user_id": "u", "id": "req_1", "title": "Ahsoka", "year": 2023, "type": "show",
             "status": "pending_approval", "updated_at": "2026-09-22T10:00:00", "source_job_id": "job_tv"}]
    result, stored = _submit(monkeypatch, rows, {"title": "Ahsoka", "year": 2023, "type": "show",
                                                 "match_score": 97, "source_job_id": "job_other"})
    assert result.get("already_queued") and len(stored) == 1
    assert stored[0]["updated_at"] == "2026-09-22T10:00:00" and stored[0]["source_job_id"] == "job_tv"
    assert stored[0]["match_score"] == 97 and stored[0]["suggested_count"] == 1


def test_a_renamed_title_is_the_same_row(monkeypatch):
    rows = [{"user_id": "u", "id": "req_1", "title": "Class Crush Crisis", "year": 2026, "type": "anime",
             "tmdb_id": 555, "status": "pending_approval", "updated_at": "2026-09-22"}]
    _result, stored = _submit(monkeypatch, rows, {"title": "Class Crush", "year": 2026, "type": "anime",
                                                  "tmdb_id": 555})
    assert len(stored) == 1


def test_a_film_and_a_series_with_the_same_tmdb_number_are_two_titles(monkeypatch):
    rows = [{"user_id": "u", "id": "req_1", "title": "A Series", "year": 2020, "type": "show", "tmdb_id": 42,
             "status": "pending_approval", "updated_at": "2026-09-22"}]
    _result, stored = _submit(monkeypatch, rows, {"title": "A Film", "year": 2021, "type": "movie", "tmdb_id": 42})
    assert len(stored) == 2 and stored[1]["status"] == "pending_approval" and stored[1].get("created_at")


def test_a_queued_anime_film_stored_without_its_format_is_recognised():
    queued = [{"title": "ALL YOU NEED IS KILL", "year": 2026, "type": "anime", "tmdb_id": 1000,
               "status": "pending_approval"}]
    context = build_exclusion_context([], [], [], queued, [])
    candidate = {"title": "ALL YOU NEED IS KILL", "year": 2026, "type": "anime", "media_type": "anime",
                 "format": "MOVIE", "tmdb_id": 2000}
    assert apply_exclusions(candidate, context) == (False, "rejected_already_requested")
