"""Startup configuration; settings are explicit and easy to override in tests."""

import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import HttpUrl


@dataclass(frozen=True)
class Settings:
    database_path: Path = Path("data/links.sqlite3")
    base_url: str = "http://localhost:8000"

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

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_path=Path(os.getenv("DATABASE_PATH", "data/links.sqlite3")),
            base_url=os.getenv("BASE_URL", "http://localhost:8000"),
        )
