"""RCA helper: is /api/usage stale immediately after a Claude call, or is it a frontend caching issue?"""
from conftest import BASE_URL


def test_usage_updates_immediately_after_call(auth_client):
    before = auth_client.get(f"{BASE_URL}/api/usage", timeout=60).json()
    r = auth_client.post(f"{BASE_URL}/api/taste-profile/generate", json={"model": "haiku-4.5"}, timeout=240)
    assert r.status_code == 200, r.text
    after = auth_client.get(f"{BASE_URL}/api/usage", timeout=60)
    print("cache-control:", after.headers.get("cache-control"), "| etag:", after.headers.get("etag"))
    after = after.json()
    print("before:", before["calls"], "after:", after["calls"])
    assert after["calls"] == before["calls"] + 1, (before, after)
