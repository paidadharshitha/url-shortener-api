"""Full-application checks of private management and public redirect boundaries."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

PASSWORD = "integration-only password"


def register(client: TestClient, username: str) -> dict:
    response = client.post("/auth/register", json={"username": username, "password": PASSWORD})
    assert response.status_code == 201, response.text
    session = response.json()
    client.headers["Authorization"] = f"Bearer {session['access_token']}"
    return session


def create_link(client: TestClient, alias: str) -> dict:
    response = client.post("/links", json={"url": f"https://example.com/{alias}", "alias": alias})
    assert response.status_code == 201, response.text
    return response.json()


def test_http_management_is_owner_scoped_while_redirects_are_public(tmp_path, clock):
    settings = Settings(
        database_path=tmp_path / "privacy.sqlite3",
        base_url="http://testserver",
        rate_limit_enabled=False,
    )
    with TestClient(create_app(settings, clock=clock), follow_redirects=False) as client:
        alice = register(client, "alice")
        create_link(client, "alice-link")
        bob = register(client, "bob")
        create_link(client, "bob-link")

        client.headers["Authorization"] = f"Bearer {alice['access_token']}"
        assert client.get("/links/bob-link/stats").status_code == 404
        assert client.patch("/links/bob-link", json={"is_active": False}).status_code == 404
        assert client.get("/links/missing-link/stats").status_code == 404
        own_page = client.get("/links", params={"owner_id": bob["user"]["id"]}).json()
        assert [item["code"] for item in own_page["items"]] == ["alice-link"]
        assert client.get("/links/overview").json()["total_links"] == 1
        assert (
            client.post(
                "/links",
                json={"url": "https://example.com/", "owner_id": bob["user"]["id"]},
            ).status_code
            == 422
        )

        client.headers.pop("Authorization")
        client.cookies.clear()
        public = client.get("/bob-link")
        assert public.status_code == 302
        assert public.headers["location"] == "https://example.com/bob-link"
        for path in ("/links", "/links/overview", "/links/bob-link/stats"):
            assert client.get(path).status_code == 401
        assert client.post("/links", json={"url": "https://example.com/"}).status_code == 401

        client.headers["Authorization"] = f"Bearer {bob['access_token']}"
        assert client.get("/links/bob-link/stats").json()["total_clicks"] == 1
        assert client.get("/links/bob-link/stats").json()["status"] == "active"


def test_http_date_ranges_keep_lifetime_today_and_other_owner_counts_separate(tmp_path, clock):
    settings = Settings(
        database_path=tmp_path / "analytics.sqlite3",
        base_url="http://testserver",
        rate_limit_enabled=False,
    )
    with TestClient(create_app(settings, clock=clock), follow_redirects=False) as client:
        alice = register(client, "alice")
        create_link(client, "alice-range")
        register(client, "bob")
        create_link(client, "bob-range")
        for code in ("alice-range", "bob-range", "bob-range"):
            assert client.get(f"/{code}").status_code == 302
        previous_day = clock.now.date().isoformat()
        clock.advance(days=1)
        assert client.get("/alice-range").status_code == 302
        client.headers["Authorization"] = f"Bearer {alice['access_token']}"

        params = {"start_date": previous_day, "end_date": previous_day}
        overview = client.get("/links/overview", params=params)
        assert overview.status_code == 200
        assert overview.json()["total_clicks"] == 2
        assert overview.json()["clicks_today"] == 1
        assert overview.json()["period_clicks"] == 1
        assert overview.json()["daily_clicks"] == [{"date": previous_day, "clicks": 1}]
        stats = client.get("/links/alice-range/stats", params=params).json()
        assert stats["daily_clicks"] == overview.json()["daily_clicks"]
        assert stats["total_clicks"] == 2
        assert stats["period_clicks"] == 1
        assert len(client.get("/links/overview", params={"days": 90}).json()["daily_clicks"]) == 90
        invalid = {"start_date": previous_day, "end_date": (clock.now.date() + timedelta(days=1))}
        assert client.get("/links/overview", params=invalid).status_code == 422


def test_forwarded_header_does_not_bypass_public_redirect_quota(tmp_path, clock):
    settings = Settings(database_path=tmp_path / "quota.sqlite3", base_url="http://testserver")
    with TestClient(create_app(settings, clock=clock), follow_redirects=False) as client:
        register(client, "quota-owner")
        create_link(client, "quota-link")
        client.app.state.rate_limiter.check("redirect", "testclient", 120, cost=120)
        response = client.get("/quota-link", headers={"X-Forwarded-For": "198.51.100.5"})
        assert response.status_code == 429
        assert int(response.headers["retry-after"]) > 0
        assert client.get("/links/quota-link/stats").json()["total_clicks"] == 0
        clock.advance(seconds=60)
        assert client.get("/quota-link").status_code == 302
        assert client.get("/links/quota-link/stats").json()["total_clicks"] == 1


def test_complete_application_can_initialize_concurrently(tmp_path, clock):
    settings = Settings(database_path=tmp_path / "startup.sqlite3", base_url="http://testserver")

    def initialize(_: int) -> int:
        with TestClient(create_app(settings, clock=clock)) as client:
            return client.get("/links").status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(initialize, range(6))) == [401] * 6

    with TestClient(create_app(settings, clock=clock)) as client:
        register(client, "first-owner")
        assert create_link(client, "after-startup")["code"] == "after-startup"


@pytest.mark.parametrize("path", ["/auth/register", "/auth/login"])
def test_auth_rejects_unpaired_unicode_surrogates_without_server_error(tmp_path, clock, path):
    settings = Settings(database_path=tmp_path / "unicode.sqlite3", base_url="http://testserver")
    with TestClient(create_app(settings, clock=clock), raise_server_exceptions=False) as client:
        response = client.post(
            path,
            content=b'{"username":"review-owner","password":"test-password\\ud800"}',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]
