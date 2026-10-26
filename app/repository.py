"""SQLite persistence with a unique code and an atomic click increment."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.domain import AliasConflict, Link


def timestamp(value: datetime) -> str:
    """Canonical UTC strings have the same chronological and lexical ordering."""
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def link_from_row(row: sqlite3.Row) -> Link:
    return Link(
        code=row["code"],
        url=row["url"],
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        total_clicks=row["total_clicks"],
        last_clicked_at=(
            datetime.fromisoformat(row["last_clicked_at"]) if row["last_clicked_at"] else None
        ),
    )


class LinkRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        # Each operation owns its connection; no connection crosses worker threads.
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS links (
                    code TEXT PRIMARY KEY NOT NULL,
                    url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    total_clicks INTEGER NOT NULL DEFAULT 0 CHECK (total_clicks >= 0),
                    last_clicked_at TEXT
                )
                """
            )

    def create(self, link: Link) -> Link:
        try:
            with self.connection() as connection:
                connection.execute(
                    "INSERT INTO links (code, url, created_at, expires_at) VALUES (?, ?, ?, ?)",
                    (
                        link.code,
                        link.url,
                        timestamp(link.created_at),
                        timestamp(link.expires_at) if link.expires_at else None,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY:
                raise AliasConflict(link.code) from exc
            raise
        return link

    def get(self, code: str) -> Link | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM links WHERE code = ?", (code,)).fetchone()
        return link_from_row(row) if row else None

    def record_click(self, code: str, now: datetime) -> Link | None:
        """Return the link, counting a click only if active, in one transaction."""
        instant = timestamp(now)
        with self.connection() as connection:
            row = connection.execute(
                """
                UPDATE links
                SET total_clicks = total_clicks + 1,
                    last_clicked_at = CASE
                        WHEN last_clicked_at IS NULL OR last_clicked_at < ? THEN ?
                        ELSE last_clicked_at
                    END
                WHERE code = ? AND (expires_at IS NULL OR expires_at > ?)
                RETURNING *
                """,
                (instant, instant, code, instant),
            ).fetchone()
            if row is None:
                # The UPDATE holds the write transaction while we distinguish an
                # expired link from a missing code. Concurrent creation cannot
                # turn a missing code into a false "expired" result.
                row = connection.execute("SELECT * FROM links WHERE code = ?", (code,)).fetchone()
        return link_from_row(row) if row else None
