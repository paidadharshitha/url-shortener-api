"""Link creation, expiry policy, and collision handling."""

import secrets
import string
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta

from app.domain import (
    AliasConflict,
    CodeGenerationFailed,
    InvalidDateRange,
    InvalidExpiry,
    Link,
    LinkExpired,
    LinkNotFound,
    LinkPaused,
)
from app.repository import LinkRepository
from app.schemas import RESERVED_CODES

Clock = Callable[[], datetime]
CodeGenerator = Callable[[], str]


def utc_now() -> datetime:
    return datetime.now(UTC)


def generate_code() -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))


def validate_expiry(expires_at: datetime | None, now: datetime) -> datetime | None:
    expiry = expires_at.astimezone(UTC) if expires_at else None
    if expiry is not None and expiry <= now:
        raise InvalidExpiry("expires_at must be in the future.")
    return expiry


def analytics_range(
    now: datetime,
    *,
    days: int = 7,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[date, date]:
    """Resolve a preset or explicit inclusive window using the UTC calendar."""
    if days not in (7, 30, 90):
        raise InvalidDateRange("days must be 7, 30, or 90.")
    today = now.astimezone(UTC).date()
    if start_date is None and end_date is None:
        return today - timedelta(days=days - 1), today
    if start_date is None or end_date is None:
        raise InvalidDateRange("Provide both start_date and end_date for a custom range.")
    if start_date > end_date:
        raise InvalidDateRange("start_date must be on or before end_date.")
    if end_date > today:
        raise InvalidDateRange("Analytics dates cannot be in the future (UTC).")
    if (end_date - start_date).days >= 366:
        raise InvalidDateRange("Choose a date range of at most 366 days, including both endpoints.")
    return start_date, end_date


def daily_series(
    first_day: date, counts: Mapping[str, int], last_day: date | None = None
) -> list[dict[str, str | int]]:
    end = last_day if last_day is not None else first_day + timedelta(days=6)
    return [
        {"date": day.isoformat(), "clicks": counts.get(day.isoformat(), 0)}
        for day in (
            first_day + timedelta(days=offset) for offset in range((end - first_day).days + 1)
        )
    ]


class LinkService:
    def __init__(
        self,
        repository: LinkRepository,
        clock: Clock = utc_now,
        code_generator: CodeGenerator = generate_code,
    ) -> None:
        self.repository = repository
        self.clock = clock
        self.code_generator = code_generator

    def create(
        self,
        url: str,
        alias: str | None,
        expires_at: datetime | None,
        title: str | None = None,
        *,
        owner_id: int | None = None,
    ) -> Link:
        now = self.clock().astimezone(UTC)
        expiry = validate_expiry(expires_at, now)

        for _ in range(10):
            code = alias if alias is not None else self.code_generator()
            if code.lower() in RESERVED_CODES:
                continue
            try:
                return self.repository.create(
                    Link(code, url, now, expiry, title=title, owner_id=owner_id)
                )
            except AliasConflict:
                if alias is not None:
                    raise
        raise CodeGenerationFailed("Unable to allocate a short code. Please retry.")

    def resolve(self, code: str) -> Link:
        now = self.clock()
        link = self.repository.record_click(code, now)
        if link is None:
            raise LinkNotFound(code)
        if not link.is_active:
            raise LinkPaused(code)
        if link.is_expired(now):
            raise LinkExpired(code)
        return link

    def get(self, code: str, *, owner_id: int | None = None) -> Link:
        link = self.repository.get(code, owner_id=owner_id)
        if link is None:
            raise LinkNotFound(code)
        return link

    def update(self, code: str, changes: dict[str, object], *, owner_id: int | None = None) -> Link:
        updates = dict(changes)
        if "expires_at" in updates:
            expiry = updates["expires_at"]
            if expiry is not None and not isinstance(expiry, datetime):
                raise InvalidExpiry("expires_at must be an ISO 8601 timestamp with a timezone.")
            updates["expires_at"] = validate_expiry(expiry, self.clock())
        link = self.repository.update(code, updates, owner_id=owner_id)
        if link is None:
            raise LinkNotFound(code)
        return link

    def list_links(
        self,
        query: str,
        status: str,
        limit: int,
        offset: int,
        now: datetime,
        *,
        owner_id: int | None = None,
    ) -> tuple[list[Link], int]:
        return self.repository.list_links(query, status, limit, offset, now, owner_id=owner_id)

    def stats(
        self,
        code: str,
        now: datetime,
        *,
        owner_id: int | None = None,
        days: int = 7,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> tuple[Link, list[dict[str, str | int]]]:
        first_day, last_day = analytics_range(
            now, days=days, start_date=start_date, end_date=end_date
        )
        link, counts = self.repository.get_stats(code, first_day, last_day, owner_id=owner_id)
        if link is None:
            raise LinkNotFound(code)
        return link, daily_series(first_day, counts, last_day)

    def overview(
        self,
        *,
        owner_id: int | None = None,
        days: int = 7,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, object]:
        now = self.clock().astimezone(UTC)
        first_day, last_day = analytics_range(
            now, days=days, start_date=start_date, end_date=end_date
        )
        totals, counts = self.repository.overview(now, first_day, last_day, owner_id=owner_id)
        return {
            **totals,
            "daily_clicks": daily_series(first_day, counts, last_day),
            "start_date": first_day,
            "end_date": last_day,
            "period_clicks": sum(counts.values()),
        }
