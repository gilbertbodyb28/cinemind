"""Sources shows what the providers say now, not that a token is stored.

Measured 2026-09-25 13:3x UTC with read-only calls: Trakt 401, Simkl 412 (the
manual app) / 401 (the server's app), Plex 401 on the server and on plex.tv,
AniList 200 - while Sources read "Connected" for Simkl and Plex, because no
call had met their tokens since the refusal was first recorded.
"""

import asyncio
import json

import httpx
import pytest

import providers.auth_state as auth_state
import providers.trakt as trakt
from fake_mongo import fake_db


_REAL_CLIENT = httpx.AsyncClient


def _route(handler):
    """Every httpx.AsyncClient in the code under test answers through `handler`."""

    def client(*args, **kwargs):
        return _REAL_CLIENT(*args, transport=httpx.MockTransport(handler), **kwargs)

    return client


@pytest.fixture
def db(monkeypatch):
    database = fake_db(connections=[{"user_id": "u"}])
    import database as database_module

    monkeypatch.setattr(database_module, "db", database)
    monkeypatch.setattr(trakt, "db", database)
    return database


def _answer(status, body=None):
    return httpx.Response(status, content=json.dumps(body if body is not None else {}).encode(),
                          headers={"content-type": "application/json"})


def _check(monkeypatch, provider, conn, handler):
    monkeypatch.setattr(httpx, "AsyncClient", _route(handler))
    return asyncio.run(auth_state.check_provider(provider, "u", conn))


TRAKT = {"trakt_access_token": "a", "trakt_refresh_token": "r", "trakt_expires_at": 4102444800}


def test_trakt_accepting_the_token_is_connected(monkeypatch, db):
    check = _check(monkeypatch, "trakt", TRAKT, lambda request: _answer(200, {"user": {"username": "gibbe21"}}))
    assert (check.state, check.account) == ("connected", "gibbe21")


def test_trakt_refusing_the_token_is_not_connected(monkeypatch, db):
    check = _check(monkeypatch, "trakt", TRAKT, lambda request: _answer(401))
    assert check.state == "rejected" and "401" in check.detail


def test_an_outage_proves_nothing_either_way(monkeypatch, db):
    check = _check(monkeypatch, "trakt", TRAKT, lambda request: _answer(503))
    assert check.state == "unverified"

    def down(request):
        raise httpx.ConnectError("down", request=request)

    assert _check(monkeypatch, "simkl", {"simkl_access_token": "s", "simkl_token_client_id": "app"}, down).state == "unverified"


def test_simkl_refusals_are_both_rejections(monkeypatch, db):
    conn = {"simkl_access_token": "s", "simkl_client_id": "dead-app"}
    for status, error in ((412, "client_id_failed"), (401, "user_token_failed")):
        check = _check(monkeypatch, "simkl", conn, lambda request, s=status, e=error: _answer(s, {"error": e}))
        assert check.state == "rejected" and error in check.detail


def test_the_simkl_token_is_asked_about_with_the_app_that_issued_it(monkeypatch, db):
    seen = []

    def handler(request):
        seen.append(request.headers.get("simkl-api-key"))
        return _answer(200, {"user": {"name": "Gilbert"}})

    conn = {"simkl_access_token": "s", "simkl_client_id": "dead-app", "simkl_token_client_id": "server-app"}
    assert _check(monkeypatch, "simkl", conn, handler).state == "connected"
    assert seen == ["server-app"]


def test_plex_server_refusing_the_token_is_not_connected(monkeypatch, db):
    conn = {"plex_token": "p", "plex_url": "http://192.168.50.223:32400"}
    check = _check(monkeypatch, "plex", conn, lambda request: _answer(401))
    assert check.state == "rejected"


def test_plex_tv_speaks_for_the_token_when_the_server_is_down(monkeypatch, db):
    conn = {"plex_token": "p", "plex_url": "http://192.168.50.223:32400"}

    def handler(request, answer=200):
        if request.url.host == "192.168.50.223":
            raise httpx.ConnectError("no route", request=request)
        return _answer(answer, {"username": "gibbe21"})

    assert _check(monkeypatch, "plex", conn, handler).state == "connected"
    assert _check(monkeypatch, "plex", conn, lambda request: handler(request, 401)).state == "rejected"


def test_anilist_invalid_token_is_a_rejection_and_a_viewer_is_a_connection(monkeypatch, db):
    conn = {"anilist_access_token": "t"}
    invalid = {"data": None, "errors": [{"message": "Invalid token", "status": 400}]}
    assert _check(monkeypatch, "anilist", conn, lambda request: _answer(400, invalid)).state == "rejected"
    viewer = {"data": {"Viewer": {"id": 484485, "name": "gibbe21"}}}
    check = _check(monkeypatch, "anilist", conn, lambda request: _answer(200, viewer))
    assert (check.state, check.account) == ("connected", "gibbe21")
    # Any other 400 is a question AniList could not answer, not a verdict on the token.
    other = {"errors": [{"message": "Syntax Error", "status": 400}]}
    assert _check(monkeypatch, "anilist", conn, lambda request: _answer(400, other)).state == "unverified"


def test_no_stored_sign_in_is_not_connected_and_calls_nothing(monkeypatch, db):
    def handler(request):
        raise AssertionError("no call without a sign-in")

    for provider in ("trakt", "simkl", "plex", "anilist"):
        assert _check(monkeypatch, provider, {}, handler).state == "not_connected"


def test_verify_records_refusals_and_clears_what_works_again(monkeypatch, db):
    db.connections.rows = [{
        "user_id": "u", **TRAKT, "simkl_access_token": "s", "simkl_token_client_id": "app",
        "plex_token": "p", "plex_url": "http://plex.local:32400", "anilist_access_token": "t",
        "trakt_auth_error": "old refusal",
    }]

    def handler(request):
        if request.url.host == "api.trakt.tv":
            return _answer(200, {"user": {"username": "gibbe21"}})
        if request.url.host == "api.simkl.com":
            return _answer(401, {"error": "user_token_failed"})
        if request.url.host == "plex.local":
            return _answer(401)
        return _answer(503)  # AniList having a bad minute

    monkeypatch.setattr(httpx, "AsyncClient", _route(handler))
    checks = asyncio.run(auth_state.verify_connections("u"))
    stored = db.connections.rows[0]

    assert {name: check["state"] for name, check in checks.items()} == {
        "trakt": "connected", "simkl": "rejected", "plex": "rejected", "anilist": "unverified"}
    assert "trakt_auth_error" not in stored
    assert "user_token_failed" in stored["simkl_auth_error"] and stored["plex_auth_error"]
    assert "anilist_auth_error" not in stored  # an unanswered question changes nothing

    import server

    shown = server.connections_public(stored)
    assert shown.trakt_connected and not shown.simkl_connected and not shown.plex_connected
    assert shown.anilist_connected


def test_an_anilist_refusal_is_not_reported_as_connected():
    import server

    shown = server.connections_public({"anilist_access_token": "t", "anilist_auth_error": "AniList 400"})
    assert (shown.anilist_connected, shown.anilist_auth_error) == (False, "AniList 400")


EXPIRING = {"user_id": "u", "trakt_access_token": "old", "trakt_refresh_token": "r", "trakt_expires_at": 1}


@pytest.mark.parametrize("status", [503, 429, 500])
def test_a_trakt_outage_during_renewal_keeps_the_sign_in(monkeypatch, db, status):
    db.connections.rows = [dict(EXPIRING)]
    monkeypatch.setattr(trakt, "TRAKT_CLIENT_SECRET", "secret")
    monkeypatch.setattr(trakt, "TRAKT_CLIENT_ID", "app")
    monkeypatch.setattr(httpx, "AsyncClient", _route(lambda request: _answer(status)))

    token = asyncio.run(trakt.trakt_token("u", dict(EXPIRING)))

    assert token == "old"
    assert db.connections.rows[0]["trakt_refresh_token"] == "r" and "trakt_auth_error" not in db.connections.rows[0]


def test_a_refused_renewal_is_recorded_not_wiped(monkeypatch, db):
    db.connections.rows = [dict(EXPIRING)]
    monkeypatch.setattr(trakt, "TRAKT_CLIENT_SECRET", "secret")
    monkeypatch.setattr(trakt, "TRAKT_CLIENT_ID", "app")
    monkeypatch.setattr(httpx, "AsyncClient", _route(lambda request: _answer(400, {"error": "invalid_grant"})))

    assert asyncio.run(trakt.trakt_token("u", dict(EXPIRING))) is None
    stored = db.connections.rows[0]
    assert stored["trakt_refresh_token"] == "r"  # nothing destroyed; Connect replaces it
    assert "refused to renew" in stored["trakt_auth_error"]
