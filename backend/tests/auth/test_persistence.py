"""User / Session persistence basics, exercised against SQLite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.auth.repository import SessionRepository, UserRepository
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db_session(tmp_path) -> DbSession:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'auth.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


def test_user_create_and_query(db_session: DbSession) -> None:
    repo = UserRepository(db_session)
    user = repo.create("alice@example.com", "hashed-argon2")

    assert user.id is not None
    assert repo.get_by_email("alice@example.com") is not None
    assert repo.get_by_email("bob@example.com") is None
    assert repo.get_by_id(user.id) is not None
    assert repo.get_by_id(user.id).email == "alice@example.com"


def test_user_email_unique_enforced(db_session: DbSession) -> None:
    repo = UserRepository(db_session)
    repo.create("alice@example.com", "h1")
    with pytest.raises(IntegrityError):
        repo.create("alice@example.com", "h2")


def test_session_create_and_query(db_session: DbSession) -> None:
    users = UserRepository(db_session)
    sessions = SessionRepository(db_session)
    user = users.create("alice@example.com", "h1")
    now = datetime.now(UTC)

    session = sessions.create(user.id, "sha256-of-token", now + timedelta(days=7), "pytest")

    assert session.id is not None
    assert sessions.get_by_token_hash("sha256-of-token") is not None
    assert sessions.get_by_token_hash("missing") is None
    listed = sessions.list_by_user(user.id)
    assert [s.id for s in listed] == [session.id]


def test_session_token_hash_unique(db_session: DbSession) -> None:
    users = UserRepository(db_session)
    sessions = SessionRepository(db_session)
    user = users.create("alice@example.com", "h1")
    now = datetime.now(UTC)

    sessions.create(user.id, "same-hash", now + timedelta(days=7))
    with pytest.raises(IntegrityError):
        sessions.create(user.id, "same-hash", now + timedelta(days=7))


def test_session_revoke_and_revoke_all_except(db_session: DbSession) -> None:
    users = UserRepository(db_session)
    sessions = SessionRepository(db_session)
    user = users.create("alice@example.com", "h1")
    now = datetime.now(UTC)

    s1 = sessions.create(user.id, "hash-1", now + timedelta(days=7))
    s2 = sessions.create(user.id, "hash-2", now + timedelta(days=7))

    sessions.revoke(s1, now)
    assert sessions.get_by_id(s1.id).revoked_at is not None
    assert sessions.get_by_id(s2.id).revoked_at is None

    revoked = sessions.revoke_all_except(user.id, keep_session_id=s2.id, now=now)
    # s1 already revoked; s2 is kept; nothing left to revoke.
    assert revoked == 0
    assert sessions.get_by_id(s2.id).revoked_at is None


def test_session_supports_optional_metadata(db_session: DbSession) -> None:
    users = UserRepository(db_session)
    sessions = SessionRepository(db_session)
    user = users.create("alice@example.com", "h1")
    now = datetime.now(UTC)

    bare = sessions.create(user.id, "hash-no-agent", now + timedelta(days=7))
    assert bare.user_agent is None

    with_agent = sessions.create(user.id, "hash-with-agent", now + timedelta(days=7), "Mozilla/5.0")
    assert with_agent.user_agent == "Mozilla/5.0"
