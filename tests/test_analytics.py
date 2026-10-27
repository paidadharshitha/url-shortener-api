import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import MutableClock, authenticate
from tests.test_links import create_link


def expected_days(clock: MutableClock) -> list[str]:
    today = clock.now.astimezone(UTC).date()
    return [(today - timedelta(days=ago)).isoformat() for ago in range(6, -1, -1)]


def test_empty_overview_zero_fills_seven_utc_days(client: TestClient, clock: MutableClock) -> None:
    response = client.get("/links/overview")

    assert response.status_code == 200
    overview = response.json()
    for key in (
        "total_links",
        "active_links",
        "paused_links",
        "expired_links",
        "total_clicks",
        "clicks_today",
    ):
        assert overview[key] == 0
    assert overview["daily_clicks"] == [{"date": day, "clicks": 0} for day in expected_days(clock)]


def test_overview_status_totals_are_mutually_exclusive(
    client: TestClient, clock: MutableClock
) -> None:
    expiry = (clock.now + timedelta(seconds=1)).isoformat()
    create_link(client, alias="active")
    create_link(client, alias="expired", expires_at=expiry)
    create_link(client, alias="paused")
    create_link(client, alias="paused-expired", expires_at=expiry)
    client.patch("/links/paused", json={"is_active": False})
    client.patch("/links/paused-expired", json={"is_active": False})
    clock.advance(seconds=1)

    overview = client.get("/links/overview").json()

    assert overview["total_links"] == 4
    assert overview["active_links"] == 1
    assert overview["paused_links"] == 2
    assert overview["expired_links"] == 1
    assert overview["total_links"] == sum(
        overview[key] for key in ("active_links", "paused_links", "expired_links")
    )


def test_daily_clicks_split_at_utc_midnight_and_stats_are_per_link(
    client: TestClient, clock: MutableClock
) -> None:
    clock.now = datetime(2030, 1, 1, 23, 59, 59, tzinfo=UTC)
    create_link(client, alias="first-link")
    create_link(client, alias="second-link")
    assert client.get("/first-link").status_code == 302

    clock.advance(seconds=1)
    assert client.get("/first-link").status_code == 302
    assert client.get("/second-link").status_code == 302
    assert client.get("/second-link").status_code == 302

    overview = client.get("/links/overview").json()
    assert overview["total_clicks"] == 4
    assert overview["clicks_today"] == 3
    assert [day["date"] for day in overview["daily_clicks"]] == expected_days(clock)
    assert [day["clicks"] for day in overview["daily_clicks"]] == [0, 0, 0, 0, 0, 1, 3]

    first = client.get("/links/first-link/stats").json()
    second = client.get("/links/second-link/stats").json()
    assert first["total_clicks"] == 2
    assert second["total_clicks"] == 2
    assert [day["clicks"] for day in first["daily_clicks"]] == [0, 0, 0, 0, 0, 1, 1]
    assert [day["clicks"] for day in second["daily_clicks"]] == [0, 0, 0, 0, 0, 0, 2]


def test_daily_window_excludes_older_clicks_but_all_time_total_keeps_them(
    client: TestClient, clock: MutableClock
) -> None:
    clock.advance(days=-8)
    create_link(client, alias="long-running")
    client.get("/long-running")
    client.get("/long-running")
    clock.advance(days=2)
    client.get("/long-running")
    clock.advance(days=6)
    for _ in range(3):
        client.get("/long-running")

    overview = client.get("/links/overview").json()
    stats = client.get("/links/long-running/stats").json()

    assert overview["total_clicks"] == stats["total_clicks"] == 6
    assert overview["clicks_today"] == 3
    assert [day["date"] for day in stats["daily_clicks"]] == expected_days(clock)
    assert [day["clicks"] for day in stats["daily_clicks"]] == [1, 0, 0, 0, 0, 0, 3]
    assert overview["daily_clicks"] == stats["daily_clicks"]


def test_analytics_uses_utc_date_even_when_clock_has_a_timezone_offset(
    client: TestClient, clock: MutableClock
) -> None:
    clock.now = datetime(2030, 1, 2, 1, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    create_link(client, alias="utc-calendar")
    assert client.get("/utc-calendar").status_code == 302

    overview = client.get("/links/overview").json()

    assert overview["clicks_today"] == 1
    assert overview["daily_clicks"][-1] == {"date": "2030-01-01", "clicks": 1}
    assert [day["date"] for day in overview["daily_clicks"]] == expected_days(clock)


def test_failed_redirects_and_management_reads_do_not_create_analytics(
    client: TestClient, clock: MutableClock
) -> None:
    create_link(client, alias="paused-no-clicks")
    create_link(
        client,
        alias="expired-no-clicks",
        expires_at=(clock.now + timedelta(seconds=1)).isoformat(),
    )
    client.patch("/links/paused-no-clicks", json={"is_active": False})
    clock.advance(seconds=1)

    assert client.get("/unknown-code").status_code == 404
    assert client.get("/paused-no-clicks").status_code == 410
    assert client.get("/expired-no-clicks").status_code == 410
    assert client.get("/links").status_code == 200
    for code in ("paused-no-clicks", "expired-no-clicks"):
        stats = client.get(f"/links/{code}/stats").json()
        assert stats["total_clicks"] == 0
        assert all(day["clicks"] == 0 for day in stats["daily_clicks"])

    for _ in range(2):
        overview = client.get("/links/overview").json()
        assert overview["total_clicks"] == overview["clicks_today"] == 0
        assert all(day["clicks"] == 0 for day in overview["daily_clicks"])


def test_concurrent_clicks_keep_daily_and_all_time_totals_consistent(client: TestClient) -> None:
    create_link(client, alias="parallel-analytics")

    def visit(_: int) -> int:
        return client.get("/parallel-analytics").status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(visit, range(40)))

    assert statuses == [302] * 40
    stats = client.get("/links/parallel-analytics/stats").json()
    overview = client.get("/links/overview").json()
    assert stats["total_clicks"] == overview["total_clicks"] == overview["clicks_today"] == 40
    assert sum(day["clicks"] for day in stats["daily_clicks"]) == 40
    assert stats["daily_clicks"] == overview["daily_clicks"]


def test_failed_daily_counter_write_rolls_back_the_entire_click(
    settings: Settings, clock: MutableClock
) -> None:
    with TestClient(
        create_app(settings, clock=clock),
        follow_redirects=False,
        raise_server_exceptions=False,
    ) as client:
        authenticate(client, register=True)
        create_link(client, alias="atomic-click")
        with closing(sqlite3.connect(settings.database_path)) as connection, connection:
            connection.execute(
                """
                CREATE TRIGGER fail_daily_insert BEFORE INSERT ON daily_clicks
                BEGIN
                    SELECT RAISE(ABORT, 'Injected analytics write failure');
                END
                """
            )

        assert client.get("/atomic-click").status_code == 500
        stats = client.get("/links/atomic-click/stats").json()
        assert stats["total_clicks"] == 0
        assert stats["last_clicked_at"] is None
        assert all(day["clicks"] == 0 for day in stats["daily_clicks"])
        assert client.get("/links/overview").json()["total_clicks"] == 0

        with closing(sqlite3.connect(settings.database_path)) as connection, connection:
            connection.execute("DROP TRIGGER fail_daily_insert")
        assert client.get("/atomic-click").status_code == 302
        stats = client.get("/links/atomic-click/stats").json()
        assert stats["total_clicks"] == 1
        assert sum(day["clicks"] for day in stats["daily_clicks"]) == 1
