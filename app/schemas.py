"""Validated public request and response shapes."""

from datetime import date, datetime
from typing import Annotated, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StrictBool,
    field_validator,
    model_validator,
)

from app.domain import LinkStatus

RESERVED_CODES = frozenset({"links", "docs", "redoc", "openapi.json", "assets", "auth"})
Alias = Annotated[str, Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]


class LinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("url", "title", "alias", "expires_at", mode="before", check_fields=False)
    @classmethod
    def require_valid_unicode(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("Text must contain valid Unicode characters.") from exc
        return value

    @field_validator("url", mode="before", check_fields=False)
    @classmethod
    def reject_control_characters(cls, value: object) -> object:
        # URL parsers can silently strip these; reject malformed input instead.
        if isinstance(value, str) and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("URL must not contain control characters.")
        return value

    @field_validator("url", check_fields=False)
    @classmethod
    def reject_credentials(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is not None and (value.username is not None or value.password is not None):
            raise ValueError("URL must not contain embedded credentials.")
        return value

    @field_validator("title", mode="before", check_fields=False)
    @classmethod
    def normalize_title(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("expires_at", mode="before", check_fields=False)
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


class LinkCreate(LinkInput):
    url: HttpUrl = Field(description="An absolute HTTP or HTTPS destination URL.")
    alias: Alias | None = Field(default=None, description="Optional case-sensitive custom code.")
    title: str | None = Field(default=None, max_length=120, description="Optional display title.")
    expires_at: AwareDatetime | None = Field(
        default=None, description="Optional future ISO 8601 timestamp with a timezone."
    )

    @field_validator("alias")
    @classmethod
    def reject_reserved_alias(cls, value: str | None) -> str | None:
        if value is not None and value.lower() in RESERVED_CODES:
            raise ValueError(f"Alias '{value}' is reserved. Choose a different alias.")
        return value


class LinkPatch(LinkInput):
    url: HttpUrl | None = Field(default=None, description="A replacement HTTP(S) destination URL.")
    title: str | None = Field(
        default=None, max_length=120, description="Set null to clear the title."
    )
    expires_at: AwareDatetime | None = Field(
        default=None, description="A future timestamp with a timezone, or null to remove expiry."
    )
    is_active: StrictBool | None = Field(default=None, description="False pauses redirects.")

    @field_validator("url", "is_active", mode="before")
    @classmethod
    def reject_null_for_required_values(cls, value: object) -> object:
        if value is None:
            raise ValueError("This field cannot be null; omit it to leave it unchanged.")
        return value

    @model_validator(mode="after")
    def require_changes(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        return self


class LinkResponse(BaseModel):
    code: str
    url: str
    short_url: str
    title: str | None
    created_at: datetime
    expires_at: datetime | None
    total_clicks: int
    last_clicked_at: datetime | None
    is_active: bool
    is_expired: bool
    status: LinkStatus


class DailyClicks(BaseModel):
    date: date
    clicks: int


class LinkStats(LinkResponse):
    daily_clicks: list[DailyClicks]
    start_date: date
    end_date: date
    period_clicks: int


class LinkList(BaseModel):
    items: list[LinkResponse]
    total: int
    limit: int
    offset: int


class LinkOverview(BaseModel):
    total_links: int
    active_links: int
    paused_links: int
    expired_links: int
    total_clicks: int
    clicks_today: int
    daily_clicks: list[DailyClicks]
    start_date: date
    end_date: date
    period_clicks: int


class ErrorResponse(BaseModel):
    detail: str
