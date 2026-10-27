"""Ownership isolation and inclusive UTC analytics windows at the service boundary."""

import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.domain import AliasConflict, InvalidDateRange, LinkNotFound
from app.repository import LinkRepository
from app.service import LinkService, analytics_range
from tests.conftest import MutableClock


@pytest.fixture
def service(tmp_path: Path, clock: MutableClock) -> LinkService:
    repository = LinkRepository(tmp_path / "owned-links.sqlite3")
    repository.initialize()
    return LinkService(repository, clock)


def test_owner_is_persisted_and_foreign_or_unclaimed_links_are_hidden(
    service: LinkService, clock: MutableClock
) -> None:
    service.create("https://example.com/alice", "alice-link", None, owner_id=1)
    service.create("https://example.com/bob", "bob-link", None, owner_id=2)
    service.create("https://example.com/legacy", "legacy-link", None)

    assert service.get("alice-link", owner_id=1).owner_id == 1
    for code in ("bob-link", "legacy-link", "missing-link"):
        with pytest.raises(LinkNotFound):
            service.get(code, owner_id=1)
        with pytest.raises(LinkNotFound):
            service.update(code, {"title": "Not yours"}, owner_id=1)
        with pytest.raises(LinkNotFound):
            service.stats(code, clock.now, owner_id=1)

    assert service.get("bob-link", owner_id=2).title is None
    assert service.get("legacy-link").owner_id is None
    # Redirects are public, including pre-account links, while administration is private.
    assert service.resolve("bob-link").total_clicks == 1
    assert service.resolve("legacy-link").total_clicks == 1


def test_owner_scoping_applies_before_search_status_and_pagination(
    service: LinkService, clock: MutableClock
) -> None:
    for owner in (1, 2):
        for index in range(3):
            service.create(
                "https://example.com/campaign",
                f"campaign-{owner}-{index}",
                None,
                title="Launch",
                owner_id=owner,
            )
    service.update("campaign-1-2", {"is_active": False}, owner_id=1)
    links, total = service.list_links("launch", "active", 1, 1, clock.now, owner_id=1)

    assert total == 2
    assert [link.code for link in links] == ["campaign-1-1"]
    paused, total = service.list_links("", "paused", 20, 0, clock.now, owner_id=1)
    assert total == 1
    assert [link.code for link in paused] == ["campaign-1-2"]
    empty, total = service.list_links("", "all", 20, 0, clock.now, owner_id=3)
    assert empty == []
    assert total == 0


def test_aliases_remain_globally_unique_between_workspaces(service: LinkService) -> None:
    service.create("https://example.com/alice", "shared-alias", None, owner_id=1)

    with pytest.raises(AliasConflict):
        service.create("https://example.com/bob", "shared-alias", None, owner_id=2)


def test_overview_never_includes_other_owners_or_legacy_clicks(
    service: LinkService, clock: MutableClock
) -> None:
    for code, owner in (("alice-link", 1), ("bob-link", 2), ("legacy-link", None)):
        service.create("https://example.com/", code, None, owner_id=owner)
    service.resolve("alice-link")
    for code in ("bob-link", "legacy-link"):
        for _ in range(3):
            service.resolve(code)

    overview = service.overview(owner_id=1)

    assert overview["total_links"] == overview["active_links"] == 1
    assert overview["total_clicks"] == overview["clicks_today"] == 1
    assert overview["period_clicks"] == 1
    assert overview["daily_clicks"][-1] == {"date": clock.now.date().isoformat(), "clicks": 1}
    assert service.overview(owner_id=3)["period_clicks"] == 0


@pytest.mark.parametrize("days", [7, 30, 90])
def test_presets_zero_fill_every_day_and_include_boundary_clicks(
    service: LinkService, clock: MutableClock, days: int
) -> None:
    today = clock.now
    clock.now = today - timedelta(days=days)
    service.create("https://example.com/", "range-link", None, owner_id=1)
    service.resolve("range-link")  # Just before the requested window.
    clock.advance(days=1)
    service.resolve("range-link")  # Inclusive first day.
    clock.now = today
    service.resolve("range-link")  # Inclusive last day.

    link, series = service.stats("range-link", today, owner_id=1, days=days)
    overview = service.overview(owner_id=1, days=days)

    assert link.total_clicks == 3
    assert len(series) == days
    assert series[0] == {"date": (today.date() - timedelta(days=days - 1)).isoformat(), "clicks": 1}
    assert series[-1] == {"date": today.date().isoformat(), "clicks": 1}
    assert all(day["clicks"] == 0 for day in series[1:-1])
    assert overview["daily_clicks"] == series
    assert overview["period_clicks"] == 2
    assert overview["total_clicks"] == 3
    assert overview["start_date"] == today.date() - timedelta(days=days - 1)
    assert overview["end_date"] == today.date()


def test_past_custom_window_keeps_clicks_today_current(
    service: LinkService, clock: MutableClock
) -> None:
    today = clock.now
    clock.advance(days=-5)
    service.create("https://example.com/", "past-window", None, owner_id=1)
    service.resolve("past-window")
    clock.advance(days=1)
    service.resolve("past-window")
    clock.now = today
    for _ in range(3):
        service.resolve("past-window")
    start = today.date() - timedelta(days=5)
    end = today.date() - timedelta(days=3)

    overview = service.overview(owner_id=1, start_date=start, end_date=end)

    assert overview["period_clicks"] == 2
    assert overview["clicks_today"] == 3
    assert overview["total_clicks"] == 5
    assert [day["clicks"] for day in overview["daily_clicks"]] == [1, 1, 0]
    assert overview["start_date"] == start
    assert overview["end_date"] == end


def test_custom_same_day_and_leap_day_are_inclusive(
    service: LinkService, clock: MutableClock
) -> None:
    clock.now = datetime(2032, 3, 1, 12, tzinfo=UTC)
    leap_day = date(2032, 2, 29)

    result = service.overview(owner_id=1, start_date=leap_day, end_date=leap_day)

    assert result["daily_clicks"] == [{"date": "2032-02-29", "clicks": 0}]
    assert result["period_clicks"] == 0


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"days": 1}, "days must be"),
        ({"days": 365}, "days must be"),
        ({"start_date": date(2030, 1, 1)}, "both start_date and end_date"),
        ({"end_date": date(2030, 1, 1)}, "both start_date and end_date"),
        ({"start_date": date(2030, 1, 2), "end_date": date(2030, 1, 1)}, "on or before"),
        ({"start_date": date(2030, 1, 1), "end_date": date(2030, 1, 2)}, "future"),
        ({"start_date": date(2028, 12, 31), "end_date": date(2030, 1, 1)}, "366 days"),
    ],
)
def test_invalid_ranges_are_rejected_consistently(
    service: LinkService, clock: MutableClock, options: dict, message: str
) -> None:
    service.create("https://example.com/", "range-link", None, owner_id=1)

    with pytest.raises(InvalidDateRange, match=message):
        service.overview(owner_id=1, **options)
    with pytest.raises(InvalidDateRange, match=message):
        service.stats("range-link", clock.now, owner_id=1, **options)


def test_custom_range_accepts_exactly_366_days(service: LinkService, clock: MutableClock) -> None:
    end = clock.now.date()
    result = service.overview(owner_id=1, start_date=end - timedelta(days=365), end_date=end)

    assert len(result["daily_clicks"]) == 366


def test_range_uses_utc_today_instead_of_clock_local_date() -> None:
    now = datetime(2030, 1, 2, 1, tzinfo=timezone(timedelta(hours=5, minutes=30)))

    assert analytics_range(now) == (date(2029, 12, 26), date(2030, 1, 1))
    with pytest.raises(InvalidDateRange, match="future"):
        analytics_range(now, start_date=date(2030, 1, 2), end_date=date(2030, 1, 2))


def test_owner_migration_preserves_existing_links_and_never_claims_them(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "CREATE TABLE links (code TEXT PRIMARY KEY, url TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "expires_at TEXT, total_clicks INTEGER NOT NULL, last_clicked_at TEXT, "
            "title TEXT, is_active INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO links VALUES (?, ?, ?, NULL, 12, ?, ?, 0)",
            (
                "legacy-link",
                "https://example.com/",
                "2029-01-01T00:00:00+00:00",
                "2029-01-02T00:00:00+00:00",
                "Legacy campaign",
            ),
        )
    repository = LinkRepository(path)
    repository.initialize()
    repository.initialize()

    link = repository.get("legacy-link")

    assert link is not None
    assert link.owner_id is None
    assert link.title == "Legacy campaign"
    assert link.total_clicks == 12
    assert link.last_clicked_at == datetime(2029, 1, 2, tzinfo=UTC)
    assert not link.is_active
    assert repository.get("legacy-link", owner_id=1) is None


def test_internal_export_window_supports_more_than_public_page_limit(
    service: LinkService, clock: MutableClock
) -> None:
    for index in range(101):
        service.create("https://example.com/", f"export-{index:03}", None, owner_id=1)
    service.create("https://example.com/private", "other-export", None, owner_id=2)

    links, total = service.list_links("", "all", 10001, 0, clock.now, owner_id=1)

    assert total == len(links) == 101
    assert all(link.owner_id == 1 for link in links)
