"""Validated public request and response shapes."""

from datetime import datetime
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, field_validator

RESERVED_CODES = frozenset({"links", "docs", "redoc", "openapi.json"})
Alias = Annotated[str, Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]


class LinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl = Field(description="An absolute HTTP or HTTPS destination URL.")
    alias: Alias | None = Field(default=None, description="Optional case-sensitive custom code.")
    expires_at: AwareDatetime | None = Field(
        default=None, description="Optional future ISO 8601 timestamp with a timezone."
    )

    @field_validator("url", mode="before")
    @classmethod
    def reject_control_characters(cls, value: object) -> object:
        # URL parsers can silently strip these; reject malformed input instead.
        if isinstance(value, str) and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("URL must not contain control characters.")
        return value

    @field_validator("url")
    @classmethod
    def reject_credentials(cls, value: HttpUrl) -> HttpUrl:
        if value.username is not None or value.password is not None:
            raise ValueError("URL must not contain embedded credentials.")
        return value

    @field_validator("alias")
    @classmethod
    def reject_reserved_alias(cls, value: str | None) -> str | None:
        if value is not None and value.lower() in RESERVED_CODES:
            raise ValueError(f"Alias '{value}' is reserved. Choose a different alias.")
        return value

    @field_validator("expires_at", mode="before")
    @classmethod
    def require_iso_timestamp(cls, value: object) -> object:
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("expires_at must be an ISO 8601 timestamp with a timezone.")
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value.replace("z", "+00:00"))
            except ValueError as exc:
                raise ValueError(
                    "expires_at must be an ISO 8601 timestamp with a timezone."
                ) from exc
        return value


class LinkResponse(BaseModel):
    code: str
    url: str
    short_url: str
    created_at: datetime
    expires_at: datetime | None


class LinkStats(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    url: str
    created_at: datetime
    expires_at: datetime | None
    total_clicks: int
    last_clicked_at: datetime | None
    is_expired: bool


class ErrorResponse(BaseModel):
    detail: str
