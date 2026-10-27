from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

TEST_CREDENTIALS = {"username": "test-user", "password": "integration-test-password-123"}


def authenticate(client: TestClient, *, register: bool = False) -> dict:
    """Exercise real authentication, using bearer sessions for API-focused tests."""
    action = "register" if register else "login"
    response = client.post(f"/auth/{action}", json=TEST_CREDENTIALS)
    assert response.status_code == (201 if register else 200), response.text
    session = response.json()
    client.headers["Authorization"] = f"Bearer {session['access_token']}"
    return session["user"]


@dataclass
class MutableClock:
    """Deterministic application time; expiry tests never need to sleep."""

    now: datetime

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(datetime(2030, 1, 1, 12, 0, tzinfo=UTC))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "links.sqlite3",
        base_url="http://testserver",
        rate_limit_enabled=False,
    )


@pytest.fixture
def client(settings: Settings, clock: MutableClock) -> Iterator[TestClient]:
    with TestClient(create_app(settings, clock=clock), follow_redirects=False) as client:
        authenticate(client, register=True)
        yield client
