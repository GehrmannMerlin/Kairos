"""Password / token / rate-limit primitives."""

from __future__ import annotations

from app.auth.password import hash_password, verify_password
from app.auth.rate_limit import InMemoryLoginLimiter
from app.auth.tokens import generate_session_token, hash_session_token


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_password_hash_is_salted() -> None:
    assert hash_password("same") != hash_password("same")


def test_session_token_and_hash() -> None:
    token = generate_session_token()
    assert len(token) >= 32
    digest = hash_session_token(token)
    assert digest != token
    assert len(digest) == 64
    # Deterministic: same token hashes to the same digest.
    assert hash_session_token(token) == digest


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_limiter_blocks_after_max_attempts() -> None:
    clock = FakeClock()
    limiter = InMemoryLoginLimiter(max_attempts=3, window_seconds=100, clock=clock)

    assert limiter.is_blocked("alice@example.com") is False
    for _ in range(3):
        limiter.record_failure("alice@example.com")
    assert limiter.is_blocked("alice@example.com") is True
    # Different key unaffected.
    assert limiter.is_blocked("bob@example.com") is False


def test_limiter_window_expiry_unblocks() -> None:
    clock = FakeClock()
    limiter = InMemoryLoginLimiter(max_attempts=2, window_seconds=100, clock=clock)

    limiter.record_failure("a")
    limiter.record_failure("a")
    assert limiter.is_blocked("a") is True

    clock.now += 101
    assert limiter.is_blocked("a") is False


def test_limiter_reset_clears() -> None:
    limiter = InMemoryLoginLimiter(max_attempts=1, window_seconds=100)
    limiter.record_failure("a")
    assert limiter.is_blocked("a") is True
    limiter.reset("a")
    assert limiter.is_blocked("a") is False
