from pathlib import Path

import pytest

from app.config import Settings


def test_settings_read_environment_and_normalize_trailing_slash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "custom" / "links.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(database))
    monkeypatch.setenv("BASE_URL", "https://short.example.com/")

    settings = Settings.from_env()

    assert settings.database_path == database
    assert settings.base_url == "https://short.example.com"


def test_settings_use_documented_defaults_without_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("BASE_URL", raising=False)

    settings = Settings.from_env()

    assert settings.database_path == Path("data/links.sqlite3")
    assert settings.base_url == "http://localhost:8000"


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://short.example.com",
        "short.example.com",
        "https://",
        "https://user:password@short.example.com",
        "https://short.example.com?campaign=launch",
        "https://short.example.com#fragment",
        "https://short.example.com?",
        "https://short.example.com#",
        "https://short.example.com:99999",
        "https://short.example.com:not-a-port",
        "https://short.example.com:-1",
        "https://short.example.com/\npath",
        "https://short.example.com/\tpath",
        "https://short.example.com/\x7f",
    ],
)
def test_invalid_public_base_url_fails_at_startup(base_url: str) -> None:
    with pytest.raises(ValueError, match="BASE_URL"):
        Settings(base_url=base_url)
