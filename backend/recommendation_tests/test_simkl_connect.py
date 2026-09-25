"""A dead manual Simkl Client ID no longer blocks reconnecting (HANDOFF.md, omgång 5)."""

import asyncio
from types import SimpleNamespace

import server
from providers.simkl import simkl_failure_hint, simkl_token_client_id


class _Connections:
    def __init__(self, doc):
        self.doc = dict(doc)

    async def find_one(self, _query, _projection=None):
        return dict(self.doc)

    async def update_one(self, _query, update, upsert=False):
        self.doc.update(update.get("$set", {}))


class _Response:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class _Client:
    """Simkl answers like it did on 2026-09-25: the manual id is unknown."""

    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        _Client.calls.append(params["client_id"])
        if params["client_id"] == "dead-manual-id":
            return _Response(401, {"error": "invalid_client", "error_description": "Unknown or missing client_id"})
        return _Response(200, {"device_code": "dev", "user_code": "ABCD", "verification_uri": "https://simkl.com/pin"})


def test_pin_start_falls_back_to_the_servers_app_and_remembers_it(monkeypatch):
    connections = _Connections({"user_id": "u", "simkl_client_id": "dead-manual-id"})
    monkeypatch.setattr(server, "db", SimpleNamespace(connections=connections))
    monkeypatch.setattr(server, "SIMKL_CLIENT_ID", "server-app-id")
    monkeypatch.setattr(server, "SIMKL_CLIENT_SECRET", "secret")
    monkeypatch.setattr(server.httpx, "AsyncClient", _Client)
    _Client.calls = []
    result = asyncio.run(server.simkl_pin_start(SimpleNamespace(user_id="u")))
    assert result["user_code"] == "ABCD"
    assert _Client.calls == ["dead-manual-id", "server-app-id"]
    assert connections.doc["simkl_pin_client_id"] == "server-app-id"


def test_calls_use_the_app_that_issued_the_token():
    conn = {"simkl_client_id": "dead-manual-id", "simkl_token_client_id": "server-app-id"}
    assert simkl_token_client_id(conn) == "server-app-id"
    assert simkl_token_client_id({"simkl_client_id": "manual"}) == "manual"


def test_the_reason_names_what_to_do():
    assert "Client ID" in simkl_failure_hint(_Response(412, {"error": "client_id_failed"}))
    # What Sources actually offers once a sign-in is refused (there is no Disconnect then).
    for status, error in ((412, "client_id_failed"), (401, "user_token_failed")):
        assert "Connect with Simkl" in simkl_failure_hint(_Response(status, {"error": error}))
