from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MutableClock


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "not-a-url",
        "example.com/path",
        "ftp://example.com/file",
        "javascript:alert(1)",
        "data:text/html,hello",
        "https://",
        "https://user:secret@example.com/path",
        "https://user@example.com/path",
        "https://example.com/a\nb",
        "https://example.com/a\tb",
        "https://example.com/\x7f",
    ],
)
def test_invalid_or_unsupported_url_is_rejected(client: TestClient, url: str) -> None:
    response = client.post("/links", json={"url": url})

    assert response.status_code == 422
    assert response.json()["detail"]


@pytest.mark.parametrize(
    "alias",
    ["", "ab", "a" * 65, "_abc", "-abc", "abc/def", "two words", "abc.def", "café"],
)
def test_invalid_alias_is_rejected(client: TestClient, alias: str) -> None:
    response = client.post("/links", json={"url": "https://example.com/", "alias": alias})

    assert response.status_code == 422
    assert response.json()["detail"]


@pytest.mark.parametrize(
    "alias",
    [
        "links",
        "LINKS",
        "docs",
        "Docs",
        "redoc",
        "ReDoc",
        "openapi.json",
        "assets",
        "ASSETS",
        "auth",
        "AUTH",
    ],
)
def test_reserved_route_alias_is_rejected_case_insensitively(
    client: TestClient, alias: str
) -> None:
    response = client.post("/links", json={"url": "https://example.com/", "alias": alias})

    assert response.status_code == 422
    assert response.json()["detail"]


@pytest.mark.parametrize("delta", [timedelta(seconds=-1), timedelta(0)])
def test_expiry_must_be_strictly_in_the_future(
    client: TestClient, clock: MutableClock, delta: timedelta
) -> None:
    response = client.post(
        "/links",
        json={"url": "https://example.com/", "expires_at": (clock.now + delta).isoformat()},
    )

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert isinstance(errors, list)
    assert errors[0]["loc"] == ["body", "expires_at"]
    assert errors[0]["type"] == "value_error"
    assert "future" in errors[0]["msg"].lower()


@pytest.mark.parametrize(
    "expires_at",
    ["2030-01-02T12:00:00", "2030-01-02", "tomorrow", 1893585600, "1893585600", True],
)
def test_expiry_requires_a_valid_timezone_aware_datetime(
    client: TestClient, expires_at: object
) -> None:
    response = client.post("/links", json={"url": "https://example.com/", "expires_at": expires_at})

    assert response.status_code == 422
    assert response.json()["detail"]


@pytest.mark.parametrize(
    "payload", [{}, {"url": None}, {"url": "https://example.com/", "unexpected": True}]
)
def test_missing_url_or_unrecognized_request_fields_are_rejected(
    client: TestClient, payload: dict
) -> None:
    response = client.post("/links", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]
