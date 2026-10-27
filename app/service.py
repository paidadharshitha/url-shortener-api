"""Link creation, expiry policy, and collision handling."""

import secrets
import string
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta

from app.domain import (
    AliasConflict,
    CodeGenerationFailed,
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


def daily_series(first_day: date, counts: Mapping[str, int]) -> list[dict[str, str | int]]:
    return [
        {"date": day.isoformat(), "clicks": counts.get(day.isoformat(), 0)}
        for day in (first_day + timedelta(days=offset) for offset in range(7))
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
        self, url: str, alias: str | None, expires_at: datetime | None, title: str | None = None
    ) -> Link:
        now = self.clock().astimezone(UTC)
        expiry = validate_expiry(expires_at, now)

        for _ in range(10):
            code = alias if alias is not None else self.code_generator()
            if code.lower() in RESERVED_CODES:
                continue
            try:
                return self.repository.create(Link(code, url, now, expiry, title=title))
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

    def get(self, code: str) -> Link:
        link = self.repository.get(code)
        if link is None:
            raise LinkNotFound(code)
        return link

    def update(self, code: str, changes: dict[str, object]) -> Link:
        updates = dict(changes)
        if "expires_at" in updates:
            expiry = updates["expires_at"]
            if expiry is not None and not isinstance(expiry, datetime):
                raise InvalidExpiry("expires_at must be an ISO 8601 timestamp with a timezone.")
            updates["expires_at"] = validate_expiry(expiry, self.clock())
        link = self.repository.update(code, updates)
        if link is None:
            raise LinkNotFound(code)
        return link

    def list_links(
        self, query: str, status: str, limit: int, offset: int, now: datetime
    ) -> tuple[list[Link], int]:
        return self.repository.list_links(query, status, limit, offset, now)

    def stats(self, code: str, now: datetime) -> tuple[Link, list[dict[str, str | int]]]:
        today = now.astimezone(UTC).date()
        first_day = today - timedelta(days=6)
        link, counts = self.repository.get_stats(code, first_day, today)
        if link is None:
            raise LinkNotFound(code)
        return link, daily_series(first_day, counts)

    def overview(self) -> dict[str, object]:
        now = self.clock().astimezone(UTC)
        today = now.date()
        first_day = today - timedelta(days=6)
        totals, counts = self.repository.overview(now, first_day, today)
        return {
            **totals,
            "clicks_today": counts.get(today.isoformat(), 0),
            "daily_clicks": daily_series(first_day, counts),
        }
