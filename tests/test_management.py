from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MutableClock
from tests.test_links import TARGET_URL, assert_helpful_error, create_link, parse_timestamp


def test_create_accepts_trimmed_optional_title(client: TestClient) -> None:
    titled = create_link(client, title="  Product launch  ")
    untitled = create_link(client)

    assert titled["title"] == "Product launch"
    assert untitled["title"] is None
    assert client.get(f"/links/{titled['code']}/stats").json()["title"] == "Product launch"


def test_title_length_is_validated_after_trimming(client: TestClient) -> None:
    assert create_link(client, title="  " + "x" * 120 + "  ")["title"] == "x" * 120
    response = client.post("/links", json={"url": TARGET_URL, "title": "x" * 121})
    assert response.status_code == 422


def test_whitespace_only_titles_are_stored_as_null(client: TestClient) -> None:
    link = create_link(client, title="  \t ")
    assert link["title"] is None
    create_link(client, alias="blank-title", title="Original")
    response = client.patch("/links/blank-title", json={"title": "   "})
    assert response.status_code == 200
    assert response.json()["title"] is None


def test_list_returns_empty_page_and_pagination_metadata(client: TestClient) -> None:
    response = client.get("/links")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_list_returns_newest_first_with_complete_metadata(
    client: TestClient, clock: MutableClock
) -> None:
    oldest = create_link(client, alias="oldest", title="Original link")
    clock.advance(seconds=1)
    newest = create_link(client, alias="newest", title="Latest link")
    assert client.get("/newest").status_code == 302

    response = client.get("/links", params={"limit": 1, "offset": 0})
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 2
    assert page["limit"] == 1
    assert page["offset"] == 0
    item = page["items"][0]
    assert item["code"] == newest["code"]
    assert item["title"] == "Latest link"
    assert item["url"] == TARGET_URL
    assert item["short_url"] == "http://testserver/newest"
    assert item["created_at"] == newest["created_at"]
    assert item["expires_at"] is None
    assert item["total_clicks"] == 1
    assert parse_timestamp(item["last_clicked_at"]) == clock.now
    assert item["is_active"] is True
    assert item["is_expired"] is False
    assert item["status"] == "active"

    second = client.get("/links", params={"limit": 1, "offset": 1}).json()
    assert second["total"] == 2
    assert second["items"][0]["code"] == oldest["code"]
    assert client.get("/links", params={"offset": 20}).json()["items"] == []


def test_pagination_is_stable_when_creation_timestamps_match(client: TestClient) -> None:
    codes = {create_link(client, alias=f"same-time-{index}")["code"] for index in range(6)}
    first_order = [item["code"] for item in client.get("/links").json()["items"]]
    second_order = [item["code"] for item in client.get("/links").json()["items"]]
    paged_order = [
        client.get("/links", params={"limit": 1, "offset": index}).json()["items"][0]["code"]
        for index in range(6)
    ]

    assert first_order == second_order == paged_order
    assert set(paged_order) == codes


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"status": "unknown"}, {"limit": "abc"}],
)
def test_list_rejects_invalid_pagination_and_status(client: TestClient, params: dict) -> None:
    assert client.get("/links", params=params).status_code == 422


@pytest.mark.parametrize("query", ["LAUNCH", "example.ORG/campaign", "CAMPAIGN-CODE"])
def test_list_search_matches_title_url_and_code_without_case(
    client: TestClient, query: str
) -> None:
    create_link(
        client,
        alias="campaign-code",
        title="Product launch",
        url="https://example.org/campaign",
    )
    create_link(client, alias="unrelated", title="Something else")

    response = client.get("/links", params={"q": query})

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["code"] == "campaign-code"


def test_title_search_case_folds_unicode_text(client: TestClient) -> None:
    create_link(client, alias="international", title="Café Straße")
    create_link(client, alias="unrelated-title", title="Unrelated")

    page = client.get("/links", params={"q": "CAFÉ STRASSE"}).json()

    assert page["total"] == 1
    assert page["items"][0]["code"] == "international"


@pytest.mark.parametrize(
    ("query", "expected"),
    [("%", ["percent"]), ("_", ["underscore"]), ("\\", ["backslash"]), ("' OR 1=1 --", [])],
)
def test_search_treats_sql_wildcards_and_quotes_as_literal_text(
    client: TestClient, query: str, expected: list[str]
) -> None:
    create_link(client, alias="percent", title="Save 50% today")
    create_link(client, alias="underscore", title="alpha_beta")
    create_link(client, alias="backslash", title="folder\\document")
    create_link(client, alias="ordinary", title="Ordinary title")

    page = client.get("/links", params={"q": query}).json()

    assert page["total"] == len(expected)
    assert [item["code"] for item in page["items"]] == expected


def test_status_filters_use_paused_then_expired_then_active_precedence(
    client: TestClient, clock: MutableClock
) -> None:
    expiry = (clock.now + timedelta(seconds=1)).isoformat()
    create_link(client, alias="active-link", title="Campaign active")
    create_link(client, alias="expired-link", title="Campaign expired", expires_at=expiry)
    create_link(client, alias="paused-link", title="Campaign paused")
    create_link(client, alias="paused-expired", title="Campaign both", expires_at=expiry)
    assert client.patch("/links/paused-link", json={"is_active": False}).status_code == 200
    assert client.patch("/links/paused-expired", json={"is_active": False}).status_code == 200
    clock.advance(seconds=1)

    expected = {
        "all": {"active-link", "expired-link", "paused-link", "paused-expired"},
        "active": {"active-link"},
        "expired": {"expired-link"},
        "paused": {"paused-link", "paused-expired"},
    }
    for status, codes in expected.items():
        response = client.get("/links", params={"status": status, "q": "campaign"})
        assert response.status_code == 200
        page = response.json()
        assert page["total"] == len(codes)
        assert {item["code"] for item in page["items"]} == codes
        if status != "all":
            assert all(item["status"] == status for item in page["items"])

    filtered = client.get("/links", params={"status": "paused", "limit": 1}).json()
    assert filtered["total"] == 2
    assert len(filtered["items"]) == 1


def test_patch_changes_only_supplied_fields_and_preserves_stats(
    client: TestClient, clock: MutableClock
) -> None:
    expiry = clock.now + timedelta(days=1)
    original = create_link(
        client, alias="editable", title="Old title", expires_at=expiry.isoformat()
    )
    assert client.get("/editable").status_code == 302

    response = client.patch("/links/editable", json={"title": "  New title  "})
    assert response.status_code == 200
    edited = response.json()
    assert edited["code"] == original["code"]
    assert edited["url"] == TARGET_URL
    assert edited["title"] == "New title"
    assert edited["created_at"] == original["created_at"]
    assert parse_timestamp(edited["expires_at"]) == expiry
    assert edited["total_clicks"] == 1
    assert edited["is_active"] is True
    assert edited["status"] == "active"

    response = client.patch("/links/editable", json={"url": "HTTPS://EXAMPLE.ORG"})
    assert response.status_code == 200
    assert response.json()["url"] == "https://example.org/"
    assert response.json()["title"] == "New title"
    assert client.get("/editable").headers["location"] == "https://example.org/"


def test_patch_can_clear_title_and_expiry(client: TestClient, clock: MutableClock) -> None:
    create_link(
        client,
        alias="clearable",
        title="Temporary link",
        expires_at=(clock.now + timedelta(seconds=1)).isoformat(),
    )

    response = client.patch("/links/clearable", json={"title": None, "expires_at": None})

    assert response.status_code == 200
    assert response.json()["title"] is None
    assert response.json()["expires_at"] is None
    clock.advance(days=1)
    assert client.get("/clearable").status_code == 302


def test_pause_blocks_clicks_and_resume_preserves_expiry(
    client: TestClient, clock: MutableClock
) -> None:
    expiry = clock.now + timedelta(seconds=2)
    create_link(client, alias="toggle-link", expires_at=expiry.isoformat())
    assert client.get("/toggle-link").status_code == 302

    paused = client.patch("/links/toggle-link", json={"is_active": False})
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    blocked = client.get("/toggle-link")
    assert_helpful_error(blocked, 410)
    assert "paused" in blocked.json()["detail"].lower()
    assert client.get("/links/toggle-link/stats").json()["total_clicks"] == 1

    resumed = client.patch("/links/toggle-link", json={"is_active": True}).json()
    assert resumed["status"] == "active"
    assert parse_timestamp(resumed["expires_at"]) == expiry
    assert client.get("/toggle-link").status_code == 302
    clock.now = expiry
    assert client.get("/toggle-link").status_code == 410
    assert client.get("/links/toggle-link/stats").json()["total_clicks"] == 2


def test_resuming_an_expired_link_does_not_silently_extend_it(
    client: TestClient, clock: MutableClock
) -> None:
    create_link(
        client, alias="expired-resume", expires_at=(clock.now + timedelta(seconds=1)).isoformat()
    )
    client.patch("/links/expired-resume", json={"is_active": False})
    clock.advance(seconds=1)

    response = client.patch("/links/expired-resume", json={"is_active": True, "title": "Archive"})

    assert response.status_code == 200
    assert response.json()["status"] == "expired"
    assert response.json()["is_expired"] is True
    assert client.get("/expired-resume").status_code == 410


def test_patch_expiry_can_restore_an_expired_link(client: TestClient, clock: MutableClock) -> None:
    create_link(
        client, alias="renew-link", expires_at=(clock.now + timedelta(seconds=1)).isoformat()
    )
    clock.advance(seconds=1)
    assert client.get("/renew-link").status_code == 410

    response = client.patch(
        "/links/renew-link", json={"expires_at": (clock.now + timedelta(hours=1)).isoformat()}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "active"
    assert client.get("/renew-link").status_code == 302


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"url": None},
        {"is_active": None},
        {"is_active": "false"},
        {"is_active": 1},
        {"alias": "rename-attempt"},
        {"total_clicks": 99},
        {"title": "x" * 121},
        {"url": "javascript:alert(1)"},
        {"url": "https://user:secret@example.com/"},
        {"url": "https://example.com/\npath"},
        {"expires_at": "tomorrow"},
        {"expires_at": "2030-01-02T12:00:00"},
        {"expires_at": 1893585600},
        {"expires_at": "1893585600"},
    ],
)
def test_invalid_patch_does_not_modify_link(client: TestClient, payload: dict) -> None:
    create_link(client, alias="unchanged", title="Original")

    response = client.patch("/links/unchanged", json=payload)

    assert response.status_code == 422
    stats = client.get("/links/unchanged/stats").json()
    assert stats["url"] == TARGET_URL
    assert stats["title"] == "Original"
    assert stats["expires_at"] is None
    assert stats["is_active"] is True
    assert stats["total_clicks"] == 0


@pytest.mark.parametrize("seconds", [-1, 0])
def test_patch_rejects_past_expiry_atomically(
    client: TestClient, clock: MutableClock, seconds: int
) -> None:
    create_link(client, alias="future-only", title="Original")

    response = client.patch(
        "/links/future-only",
        json={
            "title": "Should not save",
            "expires_at": (clock.now + timedelta(seconds=seconds)).isoformat(),
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "expires_at"]
    assert client.get("/links/future-only/stats").json()["title"] == "Original"


def test_patch_unknown_link_returns_not_found(client: TestClient) -> None:
    assert_helpful_error(client.patch("/links/missing-link", json={"title": "Unknown"}), 404)


def test_concurrent_partial_edits_preserve_independent_fields(client: TestClient) -> None:
    create_link(client, alias="parallel-edit", title="Original")
    ready = Barrier(2)

    def edit(payload: dict) -> int:
        ready.wait(timeout=10)
        return client.patch("/links/parallel-edit", json=payload).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(
            executor.map(edit, [{"title": "Edited"}, {"url": "https://example.org/new"}])
        )

    assert statuses == [200, 200]
    stats = client.get("/links/parallel-edit/stats").json()
    assert stats["title"] == "Edited"
    assert stats["url"] == "https://example.org/new"


def test_concurrent_metadata_edits_and_redirects_do_not_lose_clicks(client: TestClient) -> None:
    create_link(client, alias="live-edit", title="Original")

    def interact(index: int) -> int:
        if index % 3 == 0:
            return client.patch("/links/live-edit", json={"title": f"Revision {index}"}).status_code
        return client.get("/live-edit").status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(interact, range(36)))

    assert statuses.count(200) == 12
    assert statuses.count(302) == 24
    stats = client.get("/links/live-edit/stats").json()
    assert stats["total_clicks"] == 24
    assert stats["title"].startswith("Revision ")
    assert sum(day["clicks"] for day in stats["daily_clicks"]) == 24
