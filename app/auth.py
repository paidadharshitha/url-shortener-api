"""Password authentication, revocable sessions, and private workspace access."""

import hashlib
import hmac
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from threading import BoundedSemaphore
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import Settings
from app.repository import LinkRepository, timestamp

COOKIE_NAME = "shortlink_session"
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 5
PASSWORD_WORKERS = BoundedSemaphore(4)
router = APIRouter(prefix="/auth", tags=["Authentication"])
bearer_scheme = HTTPBearer(
    auto_error=False, description="Session token returned by login/register."
)


def current_time() -> datetime:
    return datetime.now(UTC)


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else secrets.token_bytes(16)
    # The versioned format pins the OWASP-listed 16 MiB / five-pass work factor.
    # Limit simultaneous memory-hard hashes independently of the HTTP worker pool.
    with PASSWORD_WORKERS:
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            dklen=32,
            maxmem=64 * 1024 * 1024,
        )
    return f"scrypt-v1${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt, digest = stored.split("$")
        if (
            algorithm != "scrypt-v1"
            or (n, r, p) != ("16384", "8", "5")
            or len(salt) != 32
            or len(digest) != 64
        ):
            return False
        candidate = hash_password(password, bytes.fromhex(salt))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), stored.encode("utf-8"))


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    return hash_password(secrets.token_urlsafe(32))


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token(token: str) -> str:
    # Domain-separated derivation lets /me recover the CSRF token without storing
    # a usable session token in the database or exposing the cookie to JavaScript.
    return hashlib.sha256(f"shortlink-csrf:{token}".encode()).hexdigest()


@dataclass(frozen=True)
class User:
    id: int
    username: str


@dataclass(frozen=True)
class Session:
    user: User
    token: str
    expires_at: datetime


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    password: str = Field(min_length=10, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def require_valid_unicode(cls, value: object) -> object:
        # JSON permits escaped lone surrogates that cannot be hashed or written
        # as UTF-8. Reject them before any validation error can echo that input.
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                try:
                    item.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise HTTPException(
                        422,
                        "Credentials must contain valid Unicode text.",
                        headers={"Cache-Control": "no-store"},
                    ) from exc
            elif isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, (list, tuple)):
                pending.extend(item)
        return value

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def meaningful_password(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Password must not consist only of whitespace.")
        return value


class UserResponse(BaseModel):
    id: int
    username: str


class SessionResponse(BaseModel):
    user: UserResponse
    csrf_token: str
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class CurrentUserResponse(BaseModel):
    user: UserResponse
    csrf_token: str


class AuthService:
    def __init__(
        self,
        repository: LinkRepository,
        clock: Callable[[], datetime] = current_time,
        settings: Settings | None = None,
    ) -> None:
        self.repository = repository
        self.clock = clock
        self.settings = settings if settings is not None else Settings()
        # Unknown usernames still pay the same password-hashing cost as a failed
        # known-user login; the response does not disclose whether an account exists.
        self._dummy_password = dummy_password_hash()

    def initialize(self) -> None:
        with self.repository.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY NOT NULL,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at)"
            )

    def register(self, username: str, password: str) -> Session:
        payload = Credentials(username=username, password=password)
        password_hash = hash_password(payload.password)
        now = self.clock()
        token = secrets.token_urlsafe(32)
        expires = now + timedelta(days=self.settings.session_days)
        try:
            with self.repository.connection() as connection:
                row = connection.execute(
                    "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?) "
                    "RETURNING id, username",
                    (payload.username, password_hash, timestamp(now)),
                ).fetchone()
                user = User(row["id"], row["username"])
                self._store_session(connection, user.id, token, now, expires)
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
                raise HTTPException(
                    409,
                    "That username is already taken. Choose another.",
                    headers={"Cache-Control": "no-store"},
                ) from exc
            raise
        return Session(user, token, expires)

    def login(self, username: str, password: str) -> Session:
        payload = Credentials(username=username, password=password)
        with self.repository.connection() as connection:
            row = connection.execute(
                "SELECT id, username, password_hash FROM users WHERE username = ?",
                (payload.username,),
            ).fetchone()
        valid = verify_password(
            payload.password, row["password_hash"] if row else self._dummy_password
        )
        if row is None or not valid:
            raise unauthorized("Username or password is incorrect.")
        user = User(row["id"], row["username"])
        now = self.clock()
        token = secrets.token_urlsafe(32)
        expires = now + timedelta(days=self.settings.session_days)
        with self.repository.connection() as connection:
            self._store_session(connection, user.id, token, now, expires)
        return Session(user, token, expires)

    @staticmethod
    def _store_session(
        connection: sqlite3.Connection,
        user_id: int,
        token: str,
        now: datetime,
        expires: datetime,
    ) -> None:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (timestamp(now),))
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (token_digest(token), user_id, timestamp(now), timestamp(expires)),
        )

    def authenticate(self, token: str) -> User | None:
        if len(token) != 43 or not token.isascii():
            return None
        with self.repository.connection() as connection:
            row = connection.execute(
                "SELECT users.id, users.username FROM sessions JOIN users "
                "ON users.id = sessions.user_id WHERE token_hash = ? AND expires_at > ?",
                (token_digest(token), timestamp(self.clock())),
            ).fetchone()
        return User(row["id"], row["username"]) if row else None

    def logout(self, token: str) -> None:
        with self.repository.connection() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_digest(token),))


def unauthorized(message: str = "Sign in to access your workspace.") -> HTTPException:
    return HTTPException(
        401, message, headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"}
    )


def origin(value: str) -> tuple[str, str | None, int | None] | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        return (
            parsed.scheme,
            parsed.hostname.lower(),
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
    except ValueError:
        return None


def check_origin(request: Request) -> None:
    supplied = request.headers.get("origin")
    if supplied is None:
        return  # Non-browser API clients have no Origin header.
    if origin(supplied) != origin(request.app.state.auth_service.settings.base_url):
        raise HTTPException(
            403,
            "This request must come from the Shortlink application.",
            headers={"Cache-Control": "no-store"},
        )


def request_token(request: Request) -> tuple[str, bool]:
    header = request.headers.get("authorization")
    if header is not None:
        scheme, separator, token = header.partition(" ")
        if separator and scheme.lower() == "bearer" and token.strip():
            return token.strip(), False
        raise unauthorized()
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise unauthorized()
    return token, True


def require_user(
    request: Request,
    _credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User:
    token, from_cookie = request_token(request)
    user = request.app.state.auth_service.authenticate(token)
    if user is None:
        raise unauthorized("Your session has expired. Please sign in again.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)
        if from_cookie and not hmac.compare_digest(
            request.headers.get("x-csrf-token", "").encode("utf-8"),
            csrf_token(token).encode("utf-8"),
        ):
            raise HTTPException(
                403,
                "Your security token is missing or invalid. Refresh and retry.",
                headers={"Cache-Control": "no-store"},
            )
    request.state.user = user
    return user


UserDependency = Annotated[User, Depends(require_user)]


def client_key(request: Request) -> str:
    # Never read X-Forwarded-For directly. A trusted reverse proxy must be
    # configured in Uvicorn before forwarded client addresses are accepted.
    return request.client.host if request.client is not None else "unknown"


def auth_quota(request: Request, bucket: str, limit: int) -> None:
    check_origin(request)
    request.app.state.rate_limiter.check(bucket, client_key(request), limit)


def session_response(request: Request, response: Response, session: Session) -> SessionResponse:
    settings = request.app.state.auth_service.settings
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        COOKIE_NAME,
        session.token,
        max_age=settings.session_days * 86400,
        expires=session.expires_at,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return SessionResponse(
        user=UserResponse(id=session.user.id, username=session.user.username),
        csrf_token=csrf_token(session.token),
        access_token=session.token,
        expires_at=session.expires_at,
    )


@router.post("/register", response_model=SessionResponse, status_code=201)
def register(payload: Credentials, request: Request, response: Response) -> SessionResponse:
    """Create a private workspace and sign in. Existing unowned links are not claimed."""
    auth_quota(request, "auth-register", 5)
    session = request.app.state.auth_service.register(payload.username, payload.password)
    return session_response(request, response, session)


@router.post("/login", response_model=SessionResponse)
def login(payload: Credentials, request: Request, response: Response) -> SessionResponse:
    """Sign in with a username and password; returns a revocable bearer token too."""
    auth_quota(request, "auth-login", 10)
    session = request.app.state.auth_service.login(payload.username, payload.password)
    return session_response(request, response, session)


@router.get("/me", response_model=CurrentUserResponse)
def current_user(request: Request, response: Response, user: UserDependency) -> CurrentUserResponse:
    token, _ = request_token(request)
    response.headers["Cache-Control"] = "no-store"
    return CurrentUserResponse(
        user=UserResponse(id=user.id, username=user.username), csrf_token=csrf_token(token)
    )


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, user: UserDependency) -> None:
    token, _ = request_token(request)
    request.app.state.auth_service.logout(token)
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        secure=request.app.state.auth_service.settings.secure_cookies,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
