"""Persistence access for users and sessions.

Repositories always take an explicit ``user_id`` / owner boundary where relevant;
there is intentionally no "list all users" surface in M-02.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session as DbSession

from app.auth.models import Session, User


class UserRepository:
    def __init__(self, db: DbSession) -> None:
        self._db = db

    def create(self, email: str, password_hash: str, display_name: str | None = None) -> User:
        user = User(email=email, password_hash=password_hash, display_name=display_name)
        self._db.add(user)
        self._db.commit()
        self._db.refresh(user)
        return user

    def get_by_email(self, email: str) -> User | None:
        return self._db.scalar(select(User).where(User.email == email))

    def get_by_id(self, user_id: int) -> User | None:
        return self._db.get(User, user_id)

    def set_password_hash(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        self._db.commit()


class SessionRepository:
    def __init__(self, db: DbSession) -> None:
        self._db = db

    def create(
        self,
        user_id: int,
        token_hash: str,
        expires_at: datetime,
        user_agent: str | None = None,
    ) -> Session:
        session = Session(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent,
        )
        self._db.add(session)
        self._db.commit()
        self._db.refresh(session)
        return session

    def get_by_token_hash(self, token_hash: str) -> Session | None:
        return self._db.scalar(select(Session).where(Session.token_hash == token_hash))

    def get_by_id(self, session_id: int) -> Session | None:
        return self._db.get(Session, session_id)

    def list_by_user(self, user_id: int) -> list[Session]:
        stmt = select(Session).where(Session.user_id == user_id).order_by(Session.created_at.desc())
        return list(self._db.scalars(stmt))

    def revoke(self, session: Session, now: datetime) -> None:
        session.revoked_at = now
        self._db.commit()

    def revoke_all_except(self, user_id: int, keep_session_id: int, now: datetime) -> int:
        stmt = (
            update(Session)
            .where(
                Session.user_id == user_id,
                Session.id != keep_session_id,
                Session.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        cursor = cast(CursorResult[Any], self._db.execute(stmt))
        self._db.commit()
        return cursor.rowcount or 0
