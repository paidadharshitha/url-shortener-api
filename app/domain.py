"""Domain records and errors, independent of HTTP and storage."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

LinkStatus = Literal["active", "paused", "expired"]


@dataclass(frozen=True)
class Link:
    code: str
    url: str
    created_at: datetime
    expires_at: datetime | None
    total_clicks: int = 0
    last_clicked_at: datetime | None = None
    title: str | None = None
    is_active: bool = True

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at is not None and self.expires_at <= now

    def status(self, now: datetime) -> LinkStatus:
        if not self.is_active:
            return "paused"
        return "expired" if self.is_expired(now) else "active"


class AliasConflict(Exception):
    """The requested code already exists."""


class LinkNotFound(Exception):
    """No link exists for this code."""


class LinkExpired(Exception):
    """The link exists but can no longer redirect."""


class LinkPaused(Exception):
    """The link was paused by its owner and cannot redirect."""


class InvalidExpiry(Exception):
    """An expiry must be strictly in the future."""


class CodeGenerationFailed(Exception):
    """All bounded attempts to allocate a random code collided."""
