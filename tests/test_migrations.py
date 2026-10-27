import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
from threading import Barrier

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.repository import LinkRepository
from tests.conftest import MutableClock
from tests.test_links import TARGET_URL, create_link, parse_timestamp


def seed_legacy_database(settings: Settings, clock: MutableClock) -> tuple[datetime, datetime]:
    created_at = clock.now - timedelta(days=10)
    last_clicked_at = clock.now - timedelta(days=1)
    with closing(sqlite3.connect(settings.database_path)) as connection, connection:
        connection.execute(
            """
            CREATE TABLE links (
                code TEXT PRIMARY KEY NOT NULL,
                url TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                total_clicks INTEGER NOT NULL DEFAULT 0 CHECK (total_clicks >= 0),
                last_clicked_at TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO links VALUES (?, ?, ?, ?, ?, ?)",
            (
                "legacy-link",
                TARGET_URL,
                created_at.isoformat(),
                None,
                12,
                last_clicked_at.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO links VALUES (?, ?, ?, ?, ?, ?)",
            (
                "legacy-expired",
                TARGET_URL,
                created_at.isoformat(),
                (clock.now - timedelta(days=1)).isoformat(),
                7,
                last_clicked_at.isoformat(),
            ),
        )
    return created_at, last_clicked_at


def test_legacy_database_migrates_without_losing_links_or_inventing_daily_history(
    settings: Settings, clock: MutableClock
) -> None:
    created_at, last_clicked_at = seed_legacy_database(settings, clock)

    with TestClient(create_app(settings, clock=clock), follow_redirects=False) as client:
        migrated = client.get("/links/legacy-link/stats")
        assert migrated.status_code == 200
        stats = migrated.json()
        assert stats["title"] is None
        assert stats["is_active"] is True
        assert stats["status"] == "active"
        assert stats["total_clicks"] == 12
        assert parse_timestamp(stats["created_at"]) == created_at
        assert parse_timestamp(stats["last_clicked_at"]) == last_clicked_at
        assert all(day["clicks"] == 0 for day in stats["daily_clicks"])
        overview = client.get("/links/overview").json()
        assert overview["total_links"] == 2
        assert overview["total_clicks"] == 19
        assert overview["clicks_today"] == 0
        assert all(day["clicks"] == 0 for day in overview["daily_clicks"])
        assert client.get("/legacy-expired").status_code == 410

        assert (
            client.patch("/links/legacy-link", json={"title": "Migrated campaign"}).status_code
            == 200
        )
        assert client.get("/legacy-link").status_code == 302
        new = create_link(client, alias="post-migration", title="New campaign")
        assert new["title"] == "New campaign"

    # Repeated initialization is safe and preserves both migration and new data.
    for _ in range(2):
        with TestClient(create_app(settings, clock=clock), follow_redirects=False) as client:
            stats = client.get("/links/legacy-link/stats").json()
            assert stats["title"] == "Migrated campaign"
            assert stats["total_clicks"] == 13
            assert sum(day["clicks"] for day in stats["daily_clicks"]) == 1
            assert client.get("/links/legacy-expired/stats").json()["total_clicks"] == 7
            overview = client.get("/links/overview").json()
            assert overview["total_links"] == 3
            assert overview["total_clicks"] == 20
            assert overview["clicks_today"] == 1


def test_concurrent_workers_safely_initialize_the_same_legacy_database(
    settings: Settings, clock: MutableClock
) -> None:
    seed_legacy_database(settings, clock)
    ready = Barrier(6)

    def initialize(_: int):
        repository = LinkRepository(settings.database_path)
        ready.wait(timeout=10)
        repository.initialize()
        return repository.get("legacy-link")

    with ThreadPoolExecutor(max_workers=6) as executor:
        links = list(executor.map(initialize, range(6)))

    assert all(link is not None and link.total_clicks == 12 for link in links)
    assert all(link.title is None and link.is_active for link in links)
    with closing(sqlite3.connect(settings.database_path)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(links)")}
        assert {"title", "is_active"} <= columns
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 2


def test_patch_state_and_daily_clicks_persist_after_restart(
    settings: Settings, clock: MutableClock
) -> None:
    with TestClient(create_app(settings, clock=clock), follow_redirects=False) as client:
        create_link(client, alias="persistent-dashboard", title="Initial")
        client.get("/persistent-dashboard")
        client.patch(
            "/links/persistent-dashboard",
            json={"title": "Updated", "url": "https://example.org/updated", "is_active": False},
        )

    with TestClient(create_app(settings, clock=clock), follow_redirects=False) as client:
        stats = client.get("/links/persistent-dashboard/stats").json()
        assert stats["title"] == "Updated"
        assert stats["url"] == "https://example.org/updated"
        assert stats["status"] == "paused"
        assert stats["is_active"] is False
        assert stats["total_clicks"] == 1
        assert sum(day["clicks"] for day in stats["daily_clicks"]) == 1
        assert client.get("/persistent-dashboard").status_code == 410
