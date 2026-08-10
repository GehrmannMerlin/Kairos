"""AuthService behavior against SQLite (no HTTP layer)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.auth import errors, tokens
from app.auth.password import verify_password
from app.auth.rate_limit import InMemoryLoginLimiter
from app.auth.repository import SessionRepository, UserRepository
from app.auth.service import AuthService
from app.config import Settings
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def service_and_db(tmp_path) -> tuple[AuthService, DbSession]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'service.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'service.db'}", session_cookie_max_age_seconds=3600
    )
    limiter = InMemoryLoginLimiter(max_attempts=3, window_seconds=100)
    service = AuthService(UserRepository(db), SessionRepository(db), settings, limiter)
    yield service, db
    db.close()


def _register(
    service: AuthService, email: str, password: str = "pass1234"
) -> tuple[object, object, str]:
    return service.register(email, password, "pytest")


def test_register_creates_user_and_session(service_and_db: tuple[AuthService, DbSession]) -> None:
    service, _ = service_and_db
    user, session, raw_token = _register(service, "alice@example.com")

    assert user.id is not None
    assert session.id is not None
    assert verify_password("pass1234", user.password_hash) is True
    assert "pass1234" not in user.password_hash
    # Raw token is never persisted; only its sha256 digest.
    assert session.token_hash == tokens.hash_session_token(raw_token)
    assert session.token_hash != raw_token


def test_register_duplicate_email_rejected(service_and_db: tuple[AuthService, DbSession]) -> None:
    service, _ = service_and_db
    _register(service, "alice@example.com")
    with pytest.raises(errors.EmailTakenError):
        _register(service, "  Alice@Example.COM ")  # normalization + uniqueness


def test_register_normalizes_email(service_and_db: tuple[AuthService, DbSession]) -> None:
    service, _ = service_and_db
    user, _, _ = _register(service, "  Alice@Example.COM ")
    assert user.email == "alice@example.com"


def test_login_success_and_failure(service_and_db: tuple[AuthService, DbSession]) -> None:
    service, _ = service_and_db
    _register(service, "alice@example.com", "right-password")

    user, session, token = service.login(
        "alice@example.com", "right-password", rate_limit_key="1.2.3.4"
    )
    assert user.email == "alice@example.com"
    assert session.id is not None
    assert tokens.hash_session_token(token) == session.token_hash

    with pytest.raises(errors.InvalidCredentialsError):
        service.login("alice@example.com", "wrong", rate_limit_key="1.2.3.5")


def test_login_failure_does_not_reveal_email_existence(
    service_and_db: tuple[AuthService, DbSession],
) -> None:
    service, _ = service_and_db
    _register(service, "alice@example.com", "pw")

    with pytest.raises(errors.InvalidCredentialsError) as known:
        service.login("alice@example.com", "bad", rate_limit_key="ip-1")
    with pytest.raises(errors.InvalidCredentialsError) as unknown:
        service.login("nobody@example.com", "bad", rate_limit_key="ip-2")

    assert known.value.code == unknown.value.code
    assert str(known.value) == str(unknown.value)


def test_login_rate_limit_blocks(service_and_db: tuple[AuthService, DbSession]) -> None:
    service, _ = service_and_db
    _register(service, "alice@example.com", "pw")
    for _ in range(3):
        with pytest.raises(errors.InvalidCredentialsError):
            service.login("alice@example.com", "bad", rate_limit_key="ip-x")
    with pytest.raises(errors.RateLimitedError):
        service.login("alice@example.com", "pw", rate_limit_key="ip-x")


def test_authenticate_session_valid_revoked_expired(
    service_and_db: tuple[AuthService, DbSession],
) -> None:
    service, db = service_and_db
    _, _, token = _register(service, "alice@example.com")

    user, session = service.authenticate_session(token)
    assert user is not None and session is not None

    # Revoked
    service.logout(session)
    assert service.authenticate_session(token) is None

    # Expired (insert directly with past expiry)
    db.expire_all()
    past = datetime.now(UTC) - timedelta(seconds=1)
    sessions = SessionRepository(db)
    sessions.create(user.id, tokens.hash_session_token("expired-token"), past)
    assert service.authenticate_session("expired-token") is None

    # Invalid token
    assert service.authenticate_session("nope") is None
    assert service.authenticate_session(None) is None


def test_change_password_invalidates_other_sessions_and_rotates_current(
    service_and_db: tuple[AuthService, DbSession],
) -> None:
    service, _ = service_and_db
    user, session_a, token_a = _register(service, "alice@example.com", "old-pw")
    _, session_b, token_b = service.login("alice@example.com", "old-pw", rate_limit_key="other-ip")

    with pytest.raises(errors.InvalidCredentialsError):
        service.change_password(user, session_a, "wrong-old", "new-pw")

    old_hash = user.password_hash
    new_user, new_session, new_token = service.change_password(user, session_a, "old-pw", "new-pw")
    assert new_user.password_hash != old_hash
    assert verify_password("new-pw", new_user.password_hash) is True

    # Old sessions (a and b) no longer authenticate; new token does.
    assert service.authenticate_session(token_a) is None
    assert service.authenticate_session(token_b) is None
    assert service.authenticate_session(new_token) is not None
    assert new_session.id != session_a.id


def test_revoke_session_ownership_enforced(service_and_db: tuple[AuthService, DbSession]) -> None:
    service, _ = service_and_db
    user_a, session_a, _ = _register(service, "alice@example.com")
    user_b, session_b, _ = _register(service, "bob@example.com")

    service.revoke_session(user_a, session_a.id)  # own session ok

    # A cannot revoke B's session: NOT_FOUND (no existence leak)
    with pytest.raises(errors.NotFoundError):
        service.revoke_session(user_a, session_b.id)


def test_revoke_other_sessions(service_and_db: tuple[AuthService, DbSession]) -> None:
    service, _ = service_and_db
    user, session_a, _ = _register(service, "alice@example.com")
    _, session_b, _ = service.login("alice@example.com", "pass1234", rate_limit_key="ip-1")

    count = service.revoke_other_sessions(user, keep_session_id=session_a.id)
    assert count == 1
    assert service.list_sessions(user) and True
