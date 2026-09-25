"""A new sign-in is "Connected" once the provider answers for it with a real call.

Trakt and Simkl stored whatever the device flow handed back, whether or not
the account then answered; Plex needed a server that takes the account token
itself, so a server shared with the account could never be connected.
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from fake_mongo import fake_db

_REAL_CLIENT = httpx.AsyncClient


def _answer(status, body=None):
    return httpx.Response(status, content=json.dumps(body if body is not None else {}).encode(),
                          headers={"content-type": "application/json"})


@pytest.fixture
def app(monkeypatch):
    import database
    import server

    db = fake_db(connections=[{"user_id": "u", "plex_url": "http://192.168.50.223:32400",
                               "trakt_auth_error": "Trakt no longer accepts the saved sign-in (401)"}])
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(database, "db", db)

    def route(handler):
        monkeypatch.setattr(httpx, "AsyncClient",
                            lambda *a, **k: _REAL_CLIENT(*a, transport=httpx.MockTransport(handler), **k))

    return SimpleNamespace(server=server, db=db, route=route, user=SimpleNamespace(user_id="u"))


TOKEN = {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 7776000}


def _trakt(app, settings_status):
    def handler(request):
        if request.url.path == "/oauth/device/token":
            return _answer(200, TOKEN)
        return _answer(settings_status, {"user": {"username": "gibbe21"}} if settings_status == 200 else {})

    app.route(handler)
    return asyncio.run(app.server.trakt_device_poll(app.server.DevicePoll(device_code="dc"), user=app.user))


def test_a_trakt_sign_in_that_answers_is_connected(app):
    result = _trakt(app, 200)
    stored = app.db.connections.rows[0]
    assert result == {"status": "authorized", "username": "gibbe21"}
    assert stored["trakt_access_token"] == "new-access" and "trakt_auth_error" not in stored
    assert app.server.connections_public(stored).trakt_connected


def test_a_trakt_sign_in_refused_at_once_is_not_stored(app):
    result = _trakt(app, 401)
    assert result["status"] == "invalid"
    assert "trakt_access_token" not in app.db.connections.rows[0]


def test_a_trakt_sign_in_nobody_confirmed_is_kept_but_not_connected(app):
    result = _trakt(app, 503)
    stored = app.db.connections.rows[0]
    assert result["status"] == "unverified"
    assert stored["trakt_refresh_token"] == "new-refresh"  # not lost to an outage
    assert not app.server.connections_public(stored).trakt_connected


def test_an_already_used_trakt_code_is_invalid_not_a_server_error(app):
    app.route(lambda request: _answer(409))
    result = asyncio.run(app.server.trakt_device_poll(app.server.DevicePoll(device_code="dc"), user=app.user))
    assert result == {"status": "invalid"}


def _simkl(app, settings_status):
    app.db.connections.rows[0]["simkl_pin_client_id"] = "server-app"

    def handler(request):
        if request.url.path == "/oauth2/token":
            return _answer(200, {"access_token": "simkl-new"})
        return _answer(settings_status, {"user": {"name": "Gilbert"}} if settings_status == 200 else
                       {"error": "user_token_failed"})

    app.route(handler)
    return asyncio.run(app.server.simkl_pin_poll(app.server.DevicePoll(device_code="dc"), user=app.user))


def test_a_simkl_sign_in_is_checked_with_the_app_that_issued_it(app):
    result = _simkl(app, 200)
    stored = app.db.connections.rows[0]
    assert result == {"status": "authorized", "username": "Gilbert"}
    assert (stored["simkl_access_token"], stored["simkl_token_client_id"]) == ("simkl-new", "server-app")
    assert app.server.connections_public(stored).simkl_connected


def test_a_simkl_sign_in_refused_at_once_is_not_stored(app):
    assert _simkl(app, 401)["status"] == "invalid"
    assert "simkl_access_token" not in app.db.connections.rows[0]


def _plex(app, *, owner=True, shared_token="server-token"):
    seen = []

    def handler(request):
        host, path, token = request.url.host, request.url.path, request.headers.get("X-Plex-Token")
        seen.append((host, path, token))
        if host == "plex.tv" and path.startswith("/api/v2/pins/"):
            return _answer(200, {"authToken": "account-token"})
        if host == "plex.tv" and path == "/api/v2/user":
            return _answer(200, {"username": "gibbe21"})
        if host == "plex.tv" and path == "/api/v2/resources":
            return _answer(200, [{"clientIdentifier": "machine-1", "provides": "server",
                                  "accessToken": shared_token}] if shared_token else [])
        if path == "/identity":
            return _answer(200, {"MediaContainer": {"machineIdentifier": "machine-1"}})
        if path == "/library/sections":
            ok = token == "account-token" if owner else token == shared_token
            return _answer(200 if ok else 401, {})
        return _answer(404)

    app.route(handler)
    result = asyncio.run(app.server.plex_pin_poll(app.server.DevicePoll(device_code="42"), user=app.user))
    return result, seen


def test_the_owners_plex_sign_in_is_stored_once_the_server_opens(app):
    result, seen = _plex(app, owner=True)
    assert result["status"] == "authorized"
    assert app.db.connections.rows[0]["plex_token"] == "account-token"
    assert ("192.168.50.223", "/library/sections", "account-token") in seen


def test_a_shared_plex_server_takes_its_own_access_token(app):
    result, _seen = _plex(app, owner=False)
    assert result["status"] == "authorized"
    assert app.db.connections.rows[0]["plex_token"] == "server-token"


def test_a_plex_account_the_server_refuses_is_not_stored(app):
    result, _seen = _plex(app, owner=False, shared_token=None)
    assert result["status"] == "invalid"
    assert "plex_token" not in app.db.connections.rows[0]
