import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import MutableClock, authenticate

TARGET_URL = "https://example.com/articles/hello?source=sharing&tag=python#examples"


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def create_link(client: TestClient, **fields: object) -> dict:
    response = client.post("/links", json={"url": TARGET_URL, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def assert_helpful_error(response, expected_status: int) -> None:
    assert response.status_code == expected_status, response.text
    assert isinstance(response.json()["detail"], str)
    assert response.json()["detail"].strip()


def test_create_generated_link_returns_public_representation(
    client: TestClient, clock: MutableClock
) -> None:
    link = create_link(client)

    assert re.fullmatch(r"[A-Za-z0-9]{8}", link["code"])
    assert link["url"] == TARGET_URL
    assert link["short_url"] == f"http://testserver/{link['code']}"
    assert parse_timestamp(link["created_at"]) == clock.now
    assert link["expires_at"] is None


def test_custom_alias_redirect_preserves_query_and_fragment(client: TestClient) -> None:
    link = create_link(client, alias="launch_2026-October")

    assert link["code"] == "launch_2026-October"
    response = client.get("/launch_2026-October")
    assert response.status_code == 302
    assert response.headers["location"] == TARGET_URL
    assert "no-store" in response.headers["cache-control"].lower()


def test_url_is_normalized_before_storage(client: TestClient) -> None:
    link = create_link(client, url="HTTPS://EXAMPLE.COM")

    assert link["url"] == "https://example.com/"
    assert client.get(f"/{link['code']}").headers["location"] == "https://example.com/"


def test_http_url_is_accepted(client: TestClient) -> None:
    link = create_link(client, url="http://example.com/plain-http")

    assert client.get(f"/{link['code']}").headers["location"] == link["url"]


def test_duplicate_alias_returns_conflict_without_replacing_original(client: TestClient) -> None:
    create_link(client, alias="shared-link")

    response = client.post(
        "/links", json={"url": "https://other.example.com/", "alias": "shared-link"}
    )

    assert_helpful_error(response, 409)
    assert "alias" in response.json()["detail"].lower()
    assert client.get("/shared-link").headers["location"] == TARGET_URL


def test_custom_aliases_are_case_sensitive(client: TestClient) -> None:
    create_link(client, alias="Release", url="https://example.com/upper")
    create_link(client, alias="release", url="https://example.com/lower")

    assert client.get("/Release").headers["location"] == "https://example.com/upper"
    assert client.get("/release").headers["location"] == "https://example.com/lower"


@pytest.mark.parametrize("alias", ["abc", "a" * 64, "0aB_-9"])
def test_alias_length_boundaries_and_allowed_characters(client: TestClient, alias: str) -> None:
    assert create_link(client, alias=alias)["code"] == alias


def test_repeated_url_can_receive_independent_short_codes(client: TestClient) -> None:
    first = create_link(client)
    second = create_link(client)

    assert first["code"] != second["code"]
    client.get(f"/{first['code']}")
    assert client.get(f"/links/{first['code']}/stats").json()["total_clicks"] == 1
    assert client.get(f"/links/{second['code']}/stats").json()["total_clicks"] == 0


def test_new_link_stats_start_empty_and_stats_reads_do_not_count(client: TestClient) -> None:
    link = create_link(client)

    response = client.get(f"/links/{link['code']}/stats")
    assert response.status_code == 200
    stats = response.json()
    assert stats["code"] == link["code"]
    assert stats["url"] == TARGET_URL
    assert stats["created_at"] == link["created_at"]
    assert stats["expires_at"] is None
    assert stats["total_clicks"] == 0
    assert stats["last_clicked_at"] is None
    assert stats["is_expired"] is False
    assert client.get(f"/links/{link['code']}/stats").json()["total_clicks"] == 0


def test_redirects_increment_clicks_and_update_last_clicked_time(
    client: TestClient, clock: MutableClock
) -> None:
    link = create_link(client)
    clock.advance(minutes=2)

    assert client.get(f"/{link['code']}").status_code == 302
    first_stats = client.get(f"/links/{link['code']}/stats").json()
    assert first_stats["total_clicks"] == 1
    assert parse_timestamp(first_stats["last_clicked_at"]) == clock.now

    clock.advance(seconds=30)
    assert client.get(f"/{link['code']}").status_code == 302
    second_stats = client.get(f"/links/{link['code']}/stats").json()
    assert second_stats["total_clicks"] == 2
    assert parse_timestamp(second_stats["last_clicked_at"]) == clock.now
    assert second_stats["last_clicked_at"] != first_stats["last_clicked_at"]


@pytest.mark.parametrize("path", ["/does-not-exist", "/links/does-not-exist/stats"])
def test_unknown_code_returns_clear_not_found(client: TestClient, path: str) -> None:
    assert_helpful_error(client.get(path), 404)


def test_link_created_after_missing_lookup_is_not_misreported_as_expired(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = client.app.state.link_service
    record_click = service.repository.record_click
    owner_id = client.get("/auth/me").json()["user"]["id"]

    def lookup_then_create(code: str, instant: datetime):
        result = record_click(code, instant)
        assert result is None
        # A concurrent creator can commit after the lookup transaction finishes,
        # before the redirect handler decides how to classify its result.
        service.create(TARGET_URL, code, None, owner_id=owner_id)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(service.repository, "record_click", lookup_then_create)
        assert_helpful_error(client.get("/created-during-lookup"), 404)

    stats = client.get("/links/created-during-lookup/stats").json()
    assert stats["total_clicks"] == 0
    assert stats["is_expired"] is False
    assert client.get("/created-during-lookup").status_code == 302


def test_link_without_expiry_remains_available(client: TestClient, clock: MutableClock) -> None:
    link = create_link(client, expires_at=None)
    clock.advance(days=3650)
    authenticate(client)

    assert client.get(f"/{link['code']}").status_code == 302
    assert client.get(f"/links/{link['code']}/stats").json()["is_expired"] is False


def test_link_expires_at_exact_boundary_and_failed_requests_do_not_count(
    client: TestClient, clock: MutableClock
) -> None:
    expiry = clock.now + timedelta(minutes=5)
    link = create_link(client, expires_at=expiry.isoformat())
    assert parse_timestamp(link["expires_at"]) == expiry

    clock.now = expiry - timedelta(microseconds=1)
    assert client.get(f"/{link['code']}").status_code == 302
    before_expiry = client.get(f"/links/{link['code']}/stats").json()
    assert before_expiry["is_expired"] is False
    assert before_expiry["total_clicks"] == 1

    clock.now = expiry
    response = client.get(f"/{link['code']}")
    assert_helpful_error(response, 410)
    assert "expired" in response.json()["detail"].lower()

    clock.advance(days=1)
    assert client.get(f"/{link['code']}").status_code == 410
    stats_response = client.get(f"/links/{link['code']}/stats")
    assert stats_response.status_code == 200
    stats = stats_response.json()
    assert stats["is_expired"] is True
    assert stats["total_clicks"] == 1
    assert stats["last_clicked_at"] == before_expiry["last_clicked_at"]


def test_expiry_accepts_timezone_offsets_and_compares_the_instant(
    client: TestClient, clock: MutableClock
) -> None:
    expiry = clock.now + timedelta(hours=1)
    offset_expiry = expiry.astimezone(timezone(timedelta(hours=5, minutes=30)))
    link = create_link(client, expires_at=offset_expiry.isoformat())

    assert parse_timestamp(link["expires_at"]) == expiry
    assert client.get(f"/{link['code']}").status_code == 302
    clock.now = expiry
    assert client.get(f"/{link['code']}").status_code == 410


def test_expired_alias_remains_reserved(client: TestClient, clock: MutableClock) -> None:
    create_link(
        client,
        alias="expired-campaign",
        expires_at=(clock.now + timedelta(seconds=1)).isoformat(),
    )
    clock.advance(seconds=1)

    response = client.post(
        "/links", json={"url": "https://example.com/new", "alias": "expired-campaign"}
    )
    assert_helpful_error(response, 409)


def test_links_and_click_stats_persist_across_app_restart(
    settings: Settings, clock: MutableClock
) -> None:
    expiry = clock.now + timedelta(days=1)
    with TestClient(create_app(settings, clock=clock), follow_redirects=False) as first_client:
        authenticate(first_client, register=True)
        original = create_link(first_client, alias="persistent-link", expires_at=expiry.isoformat())
        clock.advance(minutes=1)
        first_client.get("/persistent-link")
        clicked_at = clock.now

    with TestClient(create_app(settings, clock=clock), follow_redirects=False) as restarted_client:
        authenticate(restarted_client)
        response = restarted_client.get("/links/persistent-link/stats")
        assert response.status_code == 200
        stats = response.json()
        assert stats["url"] == original["url"]
        assert stats["created_at"] == original["created_at"]
        assert parse_timestamp(stats["expires_at"]) == expiry
        assert stats["total_clicks"] == 1
        assert parse_timestamp(stats["last_clicked_at"]) == clicked_at
        assert restarted_client.get("/persistent-link").status_code == 302
        assert restarted_client.get("/links/persistent-link/stats").json()["total_clicks"] == 2


def test_concurrent_redirects_do_not_lose_clicks(client: TestClient) -> None:
    link = create_link(client)
    click_count = 32

    def visit(_: int) -> int:
        return client.get(f"/{link['code']}").status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(visit, range(click_count)))

    assert statuses == [302] * click_count
    stats = client.get(f"/links/{link['code']}/stats").json()
    assert stats["total_clicks"] == click_count
    assert stats["last_clicked_at"] is not None


def test_concurrent_alias_claims_create_exactly_one_link(client: TestClient) -> None:
    def claim(_: int) -> int:
        return client.post("/links", json={"url": TARGET_URL, "alias": "one-winner"}).status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(claim, range(8)))

    assert statuses.count(201) == 1
    assert statuses.count(409) == 7
    assert client.get("/one-winner").headers["location"] == TARGET_URL


def test_generated_code_collision_is_retried_without_replacing_existing_link(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_link(client, alias="occupied", url="https://example.com/original")
    candidates = iter(["occupied", "newCode1"])
    monkeypatch.setattr(client.app.state.link_service, "code_generator", lambda: next(candidates))

    new_link = create_link(client)

    assert new_link["code"] == "newCode1"
    assert client.get("/occupied").headers["location"] == "https://example.com/original"
    assert client.get("/newCode1").headers["location"] == TARGET_URL


def test_repeated_generated_code_collisions_return_retryable_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_link(client, alias="occupied")
    monkeypatch.setattr(client.app.state.link_service, "code_generator", lambda: "occupied")

    response = client.post("/links", json={"url": "https://example.com/new"})

    assert_helpful_error(response, 503)
    assert "retry" in response.json()["detail"].lower()
    assert client.get("/occupied").headers["location"] == TARGET_URL


def test_generated_reserved_route_names_are_skipped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = iter(["links", "DoCs", "newCode1"])
    monkeypatch.setattr(client.app.state.link_service, "code_generator", lambda: next(candidates))

    assert create_link(client)["code"] == "newCode1"
    assert client.get("/docs").status_code == 200


def test_out_of_order_click_timestamps_do_not_move_last_clicked_backwards(
    client: TestClient, clock: MutableClock
) -> None:
    link = create_link(client)
    clock.advance(minutes=5)
    latest_click = clock.now
    assert client.get(f"/{link['code']}").status_code == 302

    clock.advance(seconds=-1)
    assert client.get(f"/{link['code']}").status_code == 302
    stats = client.get(f"/links/{link['code']}/stats").json()
    assert stats["total_clicks"] == 2
    assert parse_timestamp(stats["last_clicked_at"]) == latest_click
