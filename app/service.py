"""Link creation, expiry policy, and collision handling."""

import secrets
import string
from collections.abc import Callable
from datetime import UTC, datetime

from app.domain import (
    AliasConflict,
    CodeGenerationFailed,
    InvalidExpiry,
    Link,
    LinkExpired,
    LinkNotFound,
)
from app.repository import LinkRepository
from app.schemas import RESERVED_CODES

Clock = Callable[[], datetime]
CodeGenerator = Callable[[], str]


def utc_now() -> datetime:
    return datetime.now(UTC)


def generate_code() -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))


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

    def create(self, url: str, alias: str | None, expires_at: datetime | None) -> Link:
        now = self.clock().astimezone(UTC)
        expiry = expires_at.astimezone(UTC) if expires_at else None
        if expiry is not None and expiry <= now:
            raise InvalidExpiry("expires_at must be in the future.")

        for _ in range(10):
            code = alias if alias is not None else self.code_generator()
            if code.lower() in RESERVED_CODES:
                continue
            try:
                return self.repository.create(Link(code, url, now, expiry))
            except AliasConflict:
                if alias is not None:
                    raise
        raise CodeGenerationFailed("Unable to allocate a short code. Please retry.")

    def resolve(self, code: str) -> Link:
        now = self.clock()
        link = self.repository.record_click(code, now)
        if link is None:
            raise LinkNotFound(code)
        if link.is_expired(now):
            raise LinkExpired(code)
        return link

    def get(self, code: str) -> Link:
        link = self.repository.get(code)
        if link is None:
            raise LinkNotFound(code)
        return link
