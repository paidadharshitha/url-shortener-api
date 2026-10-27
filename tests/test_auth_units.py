"""Authentication boundaries tested independently of the application routes."""

from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin import claim_legacy
from app.auth import (
    COOKIE_NAME,
    AuthService,
    UserDependency,
    hash_password,
    router,
    verify_password,
)
from app.config import Settings
from app.rate_limit import RateLimiter
from app.repository import LinkRepository
from app.service import LinkService
from tests.conftest import MutableClock

PASSWORD = "test-only password with spaces"


@pytest.fixture
def auth_client(tmp_path: Path, clock: MutableClock) -> Iterator[TestClient]:
    settings = Settings(database_path=tmp_path / "auth.sqlite3", base_url="http://testserver")
    repository = LinkRepository(settings.database_path)
    service = AuthService(repository, clock=clock, settings=settings)
    limiter = RateLimiter(repository, clock=clock)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        repository.initialize()
        service.initialize()
        limiter.initialize()
        yield

    application = FastAPI(lifespan=lifespan)
    application.state.auth_service = service
    application.state.rate_limiter = limiter
    application.include_router(router)

    @application.get("/private")
    def read_private(user: UserDependency) -> dict[str, str]:
        return {"username": user.username}

    @application.post("/private")
    def write_private(user: UserDependency) -> dict[str, str]:
        return {"username": user.username}

    with TestClient(application) as client:
        yield client


def register(client: TestClient, username: str = "alice") -> dict:
    response = client.post("/auth/register", json={"username": username, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return response.json()


def test_password_hashes_are_salted_and_never_plaintext() -> None:
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)
    assert first != second
    assert PASSWORD not in first
    assert first.startswith("scrypt-v1$16384$8$5$")
    assert verify_password(PASSWORD, first)
    assert not verify_password("a different password", first)


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "plain$secret",
        "scrypt-v1$1073741824$8$5$" + "00" * 16 + "$" + "00" * 32,
        "scrypt-v1$16384$8$999999999$" + "00" * 16 + "$" + "00" * 32,
        "scrypt-v1$16384$8$5$bad-salt$bad-digest",
        "scrypt-v1$16384$8$5$" + "zz" * 16 + "$" + "00" * 32,
    ],
)
def test_malformed_hashes_fail_safely_without_expensive_parameters(stored: str) -> None:
    assert not verify_password(PASSWORD, stored)


def test_registration_normalizes_username_and_stores_only_token_hash(
    auth_client: TestClient,
) -> None:
    result = register(auth_client, " Alice ")
    assert result["user"]["username"] == "alice"
    assert result["token_type"] == "bearer"
    assert len(result["access_token"]) == 43
    assert len(result["csrf_token"]) == 64
    with auth_client.app.state.auth_service.repository.connection() as connection:
        user = connection.execute("SELECT * FROM users").fetchone()
        session = connection.execute("SELECT * FROM sessions").fetchone()
    assert PASSWORD not in user["password_hash"]
    assert result["access_token"] != session["token_hash"]
    assert auth_client.app.state.auth_service.authenticate(session["token_hash"]) is None
    assert auth_client.get("/auth/me").json()["user"] == result["user"]


def test_duplicate_username_is_case_insensitive_and_original_login_still_works(
    auth_client: TestClient,
) -> None:
    original = register(auth_client)
    response = auth_client.post(
        "/auth/register", json={"username": "ALICE", "password": "different long password"}
    )
    assert response.status_code == 409
    login = auth_client.post("/auth/login", json={"username": "Alice", "password": PASSWORD})
    assert login.status_code == 200
    assert login.json()["user"] == original["user"]
    assert login.json()["access_token"] != original["access_token"]


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "ab", "password": PASSWORD},
        {"username": "alice@example.com", "password": PASSWORD},
        {"username": "用户", "password": PASSWORD},
        {"username": "alice", "password": "short"},
        {"username": "alice", "password": " " * 12},
        {"username": "alice", "password": "x" * 129},
        {"username": "alice", "password": PASSWORD, "owner_id": 1},
    ],
)
def test_invalid_credentials_are_rejected(auth_client: TestClient, payload: dict) -> None:
    assert auth_client.post("/auth/register", json=payload).status_code == 422


def test_unknown_username_and_wrong_password_have_same_error(auth_client: TestClient) -> None:
    register(auth_client)
    wrong = auth_client.post(
        "/auth/login", json={"username": "alice", "password": "incorrect password"}
    )
    missing = auth_client.post(
        "/auth/login", json={"username": "missing", "password": "incorrect password"}
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json()
    assert wrong.headers["cache-control"] == "no-store"


def test_cookie_mutations_require_csrf_but_bearer_clients_do_not(auth_client: TestClient) -> None:
    result = register(auth_client)
    assert auth_client.get("/private").status_code == 200
    assert auth_client.post("/private").status_code == 403
    assert auth_client.post("/private", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert (
        auth_client.post("/private", headers={"X-CSRF-Token": result["csrf_token"]}).status_code
        == 200
    )
    auth_client.cookies.clear()
    assert (
        auth_client.post(
            "/private", headers={"Authorization": f"Bearer {result['access_token']}"}
        ).status_code
        == 200
    )


def test_foreign_origin_cannot_register_login_or_mutate(auth_client: TestClient) -> None:
    credentials = {"username": "alice", "password": PASSWORD}
    foreign = {"Origin": "https://untrusted.example"}
    assert auth_client.post("/auth/register", json=credentials, headers=foreign).status_code == 403
    result = register(auth_client)
    assert auth_client.post("/auth/login", json=credentials, headers=foreign).status_code == 403
    assert (
        auth_client.post(
            "/private", headers={**foreign, "X-CSRF-Token": result["csrf_token"]}
        ).status_code
        == 403
    )
    assert (
        auth_client.post(
            "/private",
            headers={"Origin": "http://testserver", "X-CSRF-Token": result["csrf_token"]},
        ).status_code
        == 200
    )


def test_logout_revokes_token_and_clears_cookie(auth_client: TestClient) -> None:
    result = register(auth_client)
    response = auth_client.post("/auth/logout", headers={"X-CSRF-Token": result["csrf_token"]})
    assert response.status_code == 204
    assert response.content == b""
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert COOKIE_NAME not in auth_client.cookies
    assert (
        auth_client.get(
            "/auth/me", headers={"Authorization": f"Bearer {result['access_token']}"}
        ).status_code
        == 401
    )


def test_sessions_expire_at_the_exact_boundary(
    auth_client: TestClient, clock: MutableClock
) -> None:
    result = register(auth_client)
    clock.advance(days=7, seconds=-1)
    assert auth_client.get("/auth/me").status_code == 200
    clock.advance(seconds=1)
    assert auth_client.get("/auth/me").status_code == 401
    assert auth_client.app.state.auth_service.authenticate(result["access_token"]) is None


def test_session_cookie_and_auth_responses_are_protected(auth_client: TestClient) -> None:
    response = auth_client.post("/auth/register", json={"username": "alice", "password": PASSWORD})
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie  # HTTP localhost development.
    assert response.headers["cache-control"] == "no-store"
    assert auth_client.get("/auth/me").headers["cache-control"] == "no-store"


def test_https_configuration_sets_secure_cookie(auth_client: TestClient) -> None:
    service = auth_client.app.state.auth_service
    service.settings = Settings(
        database_path=service.repository.database_path, base_url="https://testserver"
    )
    response = auth_client.post("/auth/register", json={"username": "alice", "password": PASSWORD})
    assert "Secure" in response.headers["set-cookie"]


def test_swagger_exposes_bearer_authorization(auth_client: TestClient) -> None:
    schema = auth_client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert schema["paths"]["/private"]["post"]["security"] == [{"HTTPBearer": []}]


def test_login_limit_returns_retry_after_and_resets(
    auth_client: TestClient, clock: MutableClock
) -> None:
    payload = {"username": "missing", "password": PASSWORD}
    for attempt in range(10):
        response = auth_client.post(
            "/auth/login", json=payload, headers={"X-Forwarded-For": f"192.0.2.{attempt}"}
        )
        assert response.status_code == 401
    limited = auth_client.post("/auth/login", json=payload)
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) == 60
    clock.advance(seconds=60)
    assert auth_client.post("/auth/login", json=payload).status_code == 401


def test_registering_an_account_does_not_automatically_claim_legacy_links(
    auth_client: TestClient, clock: MutableClock
) -> None:
    repository = auth_client.app.state.auth_service.repository
    links = LinkService(repository, clock=clock)
    links.create("https://example.com/legacy", "legacy-link", None)
    links.resolve("legacy-link")
    account = register(auth_client)
    legacy = repository.get("legacy-link")
    assert legacy.owner_id is None
    assert legacy.total_clicks == 1
    assert claim_legacy(repository, "ALICE") == 1
    assigned = repository.get("legacy-link")
    assert assigned.owner_id == account["user"]["id"]
    assert assigned.total_clicks == 1
    assert assigned.last_clicked_at == legacy.last_clicked_at
    register(auth_client, "bob")
    assert claim_legacy(repository, "bob") == 0
    assert repository.get("legacy-link").owner_id == account["user"]["id"]
    with pytest.raises(ValueError, match="No account"):
        claim_legacy(repository, "missing")


def test_new_sessions_survive_service_restart(auth_client: TestClient) -> None:
    account = register(auth_client)
    old = auth_client.app.state.auth_service
    restarted = AuthService(old.repository, clock=old.clock, settings=old.settings)
    restarted.initialize()
    assert restarted.authenticate(account["access_token"]).username == "alice"


def test_invalid_authorization_does_not_fall_back_to_valid_cookie(auth_client: TestClient) -> None:
    register(auth_client)
    assert (
        auth_client.get("/private", headers={"Authorization": "Basic invalid"}).status_code == 401
    )
    assert (
        auth_client.get("/private", headers={"Authorization": "Bearer invalid"}).status_code == 401
    )


def test_runtime_security_settings_are_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("SESSION_DAYS", "3")
    settings = Settings.from_env()
    assert settings.rate_limit_enabled is False and settings.session_days == 3
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "maybe")
    with pytest.raises(ValueError, match="RATE_LIMIT_ENABLED"):
        Settings.from_env()
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    for invalid in ("zero", "0", "31"):
        monkeypatch.setenv("SESSION_DAYS", invalid)
        with pytest.raises(ValueError, match="SESSION_DAYS"):
            Settings.from_env()
    with pytest.raises(ValueError, match="SESSION_DAYS"):
        Settings(session_days=True)
    with pytest.raises(ValueError, match="RATE_LIMIT_ENABLED"):
        Settings(rate_limit_enabled="false")


def test_csrf_tokens_are_bound_to_their_own_session(auth_client: TestClient) -> None:
    alice = register(auth_client, "alice")
    bob = register(auth_client, "bob")
    forbidden = auth_client.post("/private", headers={"X-CSRF-Token": alice["csrf_token"]})
    assert forbidden.status_code == 403
    response = auth_client.post("/private", headers={"X-CSRF-Token": bob["csrf_token"]})
    assert response.json() == {"username": "bob"}
    alice_response = auth_client.get(
        "/private", headers={"Authorization": f"Bearer {alice['access_token']}"}
    )
    assert alice_response.json() == {"username": "alice"}


def test_registration_has_an_independent_ip_quota(auth_client: TestClient) -> None:
    for index in range(5):
        register(auth_client, f"user{index}")
    response = auth_client.post(
        "/auth/register", json={"username": "sixth-user", "password": PASSWORD}
    )
    assert response.status_code == 429
    assert "retry-after" in response.headers
    login = auth_client.post("/auth/login", json={"username": "user0", "password": PASSWORD})
    assert login.status_code == 200
