from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.rate_limit import RateLimiter
from app.repository import LinkRepository
from tests.conftest import MutableClock


@pytest.fixture
def limiter(tmp_path: Path, clock: MutableClock) -> RateLimiter:
    repository = LinkRepository(tmp_path / "limits.sqlite3")
    repository.initialize()
    instance = RateLimiter(repository, clock=clock)
    instance.initialize()
    return instance


def test_quota_is_atomic_under_concurrent_requests(limiter: RateLimiter) -> None:
    def consume(_: int) -> bool:
        try:
            limiter.check("create", "alice", 8)
            return True
        except HTTPException as exc:
            assert exc.status_code == 429
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(consume, range(32)))
    assert sum(results) == 8
    with limiter.repository.connection() as connection:
        assert connection.execute("SELECT used FROM rate_limits").fetchone()[0] == 8


def test_quotas_are_separate_by_bucket_and_identity(limiter: RateLimiter) -> None:
    limiter.check("create", "alice", 1)
    with pytest.raises(HTTPException) as rejected:
        limiter.check("create", "alice", 1)
    assert rejected.value.status_code == 429
    limiter.check("create", "bob", 1)
    limiter.check("export", "alice", 1)
    with limiter.repository.connection() as connection:
        identities = {row[0] for row in connection.execute("SELECT identity FROM rate_limits")}
    assert "alice" not in identities and "bob" not in identities


def test_weighted_requests_do_not_consume_extra_quota_when_rejected(limiter: RateLimiter) -> None:
    limiter.check("bulk", "alice", 10, cost=7)
    with pytest.raises(HTTPException):
        limiter.check("bulk", "alice", 10, cost=4)
    limiter.check("bulk", "alice", 10, cost=3)
    with pytest.raises(HTTPException):
        limiter.check("bulk", "alice", 10)


def test_quota_persists_across_new_limiter_instances(limiter: RateLimiter) -> None:
    limiter.check("create", "alice", 1)
    restarted = RateLimiter(limiter.repository, clock=limiter.clock)
    restarted.initialize()
    with pytest.raises(HTTPException):
        restarted.check("create", "alice", 1)


def test_window_boundary_and_expired_row_cleanup(limiter: RateLimiter, clock: MutableClock) -> None:
    limiter.check("create", "alice", 1)
    clock.advance(seconds=59.5)
    with pytest.raises(HTTPException) as rejected:
        limiter.check("create", "alice", 1)
    assert rejected.value.headers["Retry-After"] == "1"
    clock.advance(seconds=0.5)
    limiter.check("create", "alice", 1)
    with limiter.repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM rate_limits").fetchone()[0] == 1


def test_disabled_limits_do_not_consume_capacity(limiter: RateLimiter) -> None:
    limiter.enabled = False
    for _ in range(5):
        limiter.check("create", "alice", 1)
    with limiter.repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM rate_limits").fetchone()[0] == 0


@pytest.mark.parametrize("options", [{"cost": 0}, {"window": 0}, {"limit": 0}, {"cost": True}])
def test_invalid_quota_configuration_fails_clearly(limiter: RateLimiter, options: dict) -> None:
    kwargs = {"limit": 1, **options}
    with pytest.raises(ValueError, match="positive integers"):
        limiter.check("create", "alice", **kwargs)
