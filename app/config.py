"""Startup configuration; settings are explicit and easy to override in tests."""

import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import HttpUrl


@dataclass(frozen=True)
class Settings:
    database_path: Path = Path("data/links.sqlite3")
    base_url: str = "http://localhost:8000"
    rate_limit_enabled: bool = True
    session_days: int = 7

    def __post_init__(self) -> None:
        message = "BASE_URL must be an HTTP(S) URL without credentials, query, or fragment."
        try:
            parsed = HttpUrl(self.base_url)
        except ValueError as exc:
            raise ValueError(message) from exc
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query is not None
            or parsed.fragment is not None
            or any(ord(char) < 32 or ord(char) == 127 for char in self.base_url)
        ):
            raise ValueError(message)
        object.__setattr__(self, "database_path", Path(self.database_path))
        object.__setattr__(self, "base_url", str(parsed).rstrip("/"))
        if not isinstance(self.rate_limit_enabled, bool):
            raise ValueError("RATE_LIMIT_ENABLED must be true or false.")
        if isinstance(self.session_days, bool) or not isinstance(self.session_days, int):
            raise ValueError("SESSION_DAYS must be an integer between 1 and 30.")
        if not 1 <= self.session_days <= 30:
            raise ValueError("SESSION_DAYS must be an integer between 1 and 30.")

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")

    @classmethod
    def from_env(cls) -> "Settings":
        enabled = os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower()
        if enabled not in {"true", "false"}:
            raise ValueError("RATE_LIMIT_ENABLED must be true or false.")
        try:
            session_days = int(os.getenv("SESSION_DAYS", "7"))
        except ValueError as exc:
            raise ValueError("SESSION_DAYS must be an integer between 1 and 30.") from exc
        return cls(
            database_path=Path(os.getenv("DATABASE_PATH", "data/links.sqlite3")),
            base_url=os.getenv("BASE_URL", "http://localhost:8000"),
            rate_limit_enabled=enabled == "true",
            session_days=session_days,
        )
