"""SQLite persistence, additive migrations, and atomic click accounting."""

import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from time import monotonic, sleep

from app.domain import AliasConflict, Link


def timestamp(value: datetime) -> str:
    """Canonical UTC strings have the same chronological and lexical ordering."""
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def enable_wal(connection: sqlite3.Connection) -> None:
    # Changing journal mode can return SQLITE_BUSY without invoking the busy
    # timeout when several workers open a legacy database simultaneously.
    deadline = monotonic() + 10
    while True:
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode != sqlite3.SQLITE_BUSY or monotonic() >= deadline:
                raise
            sleep(0.05)


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
        title=row["title"],
        is_active=bool(row["is_active"]),
    )


def daily_counts(
    connection: sqlite3.Connection, first_day: date, last_day: date, code: str | None = None
) -> dict[str, int]:
    conditions = "day >= ? AND day <= ?"
    parameters = [first_day.isoformat(), last_day.isoformat()]
    if code is not None:
        conditions += " AND code = ?"
        parameters.append(code)
    rows = connection.execute(
        f"SELECT day, SUM(clicks) AS clicks FROM daily_clicks WHERE {conditions} GROUP BY day",
        parameters,
    ).fetchall()
    return {row["day"]: row["clicks"] for row in rows}


class LinkRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        # Each operation owns its connection; no connection crosses worker threads.
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function("casefold", 1, str.casefold, deterministic=True)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            enable_wal(connection)
            # Serialize startup migrations before inspecting the existing schema.
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS links (
                    code TEXT PRIMARY KEY NOT NULL,
                    url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    total_clicks INTEGER NOT NULL DEFAULT 0 CHECK (total_clicks >= 0),
                    last_clicked_at TEXT,
                    title TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
                )
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(links)")}
            if "title" not in columns:
                connection.execute("ALTER TABLE links ADD COLUMN title TEXT")
            if "is_active" not in columns:
                connection.execute(
                    "ALTER TABLE links ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1 "
                    "CHECK (is_active IN (0, 1))"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_clicks (
                    code TEXT NOT NULL REFERENCES links(code),
                    day TEXT NOT NULL,
                    clicks INTEGER NOT NULL DEFAULT 0 CHECK (clicks >= 0),
                    PRIMARY KEY (code, day)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS links_created_idx ON links(created_at DESC, code)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS daily_clicks_day_idx ON daily_clicks(day)"
            )

    def create(self, link: Link) -> Link:
        try:
            with self.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO links (code, url, created_at, expires_at, title, is_active)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        link.code,
                        link.url,
                        timestamp(link.created_at),
                        timestamp(link.expires_at) if link.expires_at else None,
                        link.title,
                        int(link.is_active),
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

    def update(self, code: str, changes: Mapping[str, object]) -> Link | None:
        # Only fixed column names enter SQL; request values remain bound parameters.
        allowed = {"url", "title", "expires_at", "is_active"}
        if not changes or not changes.keys() <= allowed:
            raise ValueError("Updates must contain only editable link fields.")
        assignments = ", ".join(f"{column} = ?" for column in changes)
        values = [
            timestamp(value) if isinstance(value, datetime) else value for value in changes.values()
        ]
        with self.connection() as connection:
            row = connection.execute(
                f"UPDATE links SET {assignments} WHERE code = ? RETURNING *", [*values, code]
            ).fetchone()
        return link_from_row(row) if row else None

    def list_links(
        self, query: str, status: str, limit: int, offset: int, now: datetime
    ) -> tuple[list[Link], int]:
        conditions = []
        parameters: list[object] = []
        if query:
            escaped = query.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            conditions.append(
                "(casefold(code) LIKE ? ESCAPE '\\' OR casefold(url) LIKE ? ESCAPE '\\' "
                "OR casefold(COALESCE(title, '')) LIKE ? ESCAPE '\\')"
            )
            parameters.extend([pattern, pattern, pattern])
        if status == "paused":
            conditions.append("is_active = 0")
        elif status == "expired":
            conditions.append("is_active = 1 AND expires_at IS NOT NULL AND expires_at <= ?")
            parameters.append(timestamp(now))
        elif status == "active":
            conditions.append("is_active = 1 AND (expires_at IS NULL OR expires_at > ?)")
            parameters.append(timestamp(now))
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connection() as connection:
            # Count and page come from the same snapshot during concurrent writes.
            connection.execute("BEGIN")
            total = connection.execute(f"SELECT COUNT(*) FROM links{where}", parameters).fetchone()[
                0
            ]
            rows = connection.execute(
                f"SELECT * FROM links{where} ORDER BY created_at DESC, code ASC LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            ).fetchall()
        return [link_from_row(row) for row in rows], total

    def get_stats(
        self, code: str, first_day: date, last_day: date
    ) -> tuple[Link | None, dict[str, int]]:
        with self.connection() as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT * FROM links WHERE code = ?", (code,)).fetchone()
            counts = daily_counts(connection, first_day, last_day, code) if row else {}
        return (link_from_row(row) if row else None), counts

    def overview(
        self, now: datetime, first_day: date, last_day: date
    ) -> tuple[dict[str, int], dict[str, int]]:
        instant = timestamp(now)
        with self.connection() as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                """
                SELECT COUNT(*) AS total_links,
                    COALESCE(SUM(CASE WHEN is_active = 1 AND
                        (expires_at IS NULL OR expires_at > ?)
                        THEN 1 ELSE 0 END), 0) AS active_links,
                    COALESCE(SUM(CASE WHEN is_active = 0 THEN 1 ELSE 0 END), 0) AS paused_links,
                    COALESCE(SUM(CASE WHEN is_active = 1 AND expires_at <= ?
                        THEN 1 ELSE 0 END), 0) AS expired_links,
                    COALESCE(SUM(total_clicks), 0) AS total_clicks
                FROM links
                """,
                (instant, instant),
            ).fetchone()
            counts = daily_counts(connection, first_day, last_day)
        return dict(row), counts

    def record_click(self, code: str, now: datetime) -> Link | None:
        """Return the link and count a successful redirect in one transaction."""
        instant = timestamp(now)
        day = now.astimezone(UTC).date().isoformat()
        with self.connection() as connection:
            row = connection.execute(
                """
                UPDATE links
                SET total_clicks = total_clicks + 1,
                    last_clicked_at = CASE
                        WHEN last_clicked_at IS NULL OR last_clicked_at < ? THEN ?
                        ELSE last_clicked_at
                    END
                WHERE code = ? AND is_active = 1 AND (expires_at IS NULL OR expires_at > ?)
                RETURNING *
                """,
                (instant, instant, code, instant),
            ).fetchone()
            if row is not None:
                connection.execute(
                    """
                    INSERT INTO daily_clicks (code, day, clicks) VALUES (?, ?, 1)
                    ON CONFLICT (code, day) DO UPDATE SET clicks = clicks + 1
                    """,
                    (code, day),
                )
            else:
                # Keep the same write transaction while classifying an unsuccessful
                # redirect, so concurrent creation or edits cannot change its result.
                row = connection.execute("SELECT * FROM links WHERE code = ?", (code,)).fetchone()
        return link_from_row(row) if row else None
