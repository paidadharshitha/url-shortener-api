"""SQLite-backed fixed-window quotas shared by all application workers."""

import hashlib
import math
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import HTTPException

from app.repository import LinkRepository


def current_time() -> datetime:
    return datetime.now(UTC)


class RateLimiter:
    def __init__(
        self,
        repository: LinkRepository,
        clock: Callable[[], datetime] = current_time,
        *,
        enabled: bool = True,
    ) -> None:
        self.repository = repository
        self.clock = clock
        self.enabled = enabled

    def initialize(self) -> None:
        with self.repository.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_limits (
                    bucket TEXT NOT NULL,
                    identity TEXT NOT NULL,
                    window_start INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    used INTEGER NOT NULL CHECK (used > 0),
                    PRIMARY KEY (bucket, identity, window_start)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS rate_limits_expiry_idx ON rate_limits(expires_at)"
            )

    def check(self, bucket: str, key: str, limit: int, window: int = 60, cost: int = 1) -> None:
        """Consume a quota atomically, or reject without consuming more capacity."""
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in (limit, window, cost)
        ):
            raise ValueError("Rate limit, window, and cost must be positive integers.")
        if not self.enabled:
            return
        now = self.clock().timestamp()
        window_start = math.floor(now / window) * window
        expires_at = window_start + window
        retry_after = max(1, math.ceil(expires_at - now))
        identity = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
        with self.repository.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM rate_limits WHERE expires_at <= ?", (now,))
            if cost > limit:
                raise self.exceeded(retry_after)
            row = connection.execute(
                """
                INSERT INTO rate_limits (bucket, identity, window_start, expires_at, used)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (bucket, identity, window_start) DO UPDATE
                SET used = used + excluded.used
                WHERE used + excluded.used <= ?
                RETURNING used
                """,
                (bucket, identity, window_start, expires_at, cost, limit),
            ).fetchone()
            if row is None:
                raise self.exceeded(retry_after)

    @staticmethod
    def exceeded(retry_after: int) -> HTTPException:
        return HTTPException(
            status_code=429,
            detail=f"Too many requests. Try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
        )
