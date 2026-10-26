from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


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
    return Settings(database_path=tmp_path / "links.sqlite3", base_url="http://testserver")


@pytest.fixture
def client(settings: Settings, clock: MutableClock) -> Iterator[TestClient]:
    with TestClient(create_app(settings, clock=clock), follow_redirects=False) as client:
        yield client
