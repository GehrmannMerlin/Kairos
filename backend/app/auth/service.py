"""AuthService: registration, login, session lifecycle and password change.

This is the only place that touches password hashes and session tokens for the
API. Routes stay thin and delegate here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.auth import errors, tokens
from app.auth.models import Session, User
from app.auth.password import hash_password, verify_password
from app.auth.rate_limit import LoginRateLimiter
from app.auth.repository import SessionRepository, UserRepository
from app.config import Settings


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _as_utc(value: datetime) -> datetime:
    """Normalize a DB-read datetime to UTC.

    SQLite returns offset-naive datetimes even for ``timezone=True`` columns,
    while PostgreSQL returns aware ones; both represent UTC here.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class AuthService:
    def __init__(
        self,
        users: UserRepository,
        sessions: SessionRepository,
        settings: Settings,
        limiter: LoginRateLimiter,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._settings = settings
        self._limiter = limiter

    def register(
        self, email: str, password: str, user_agent: str | None = None
    ) -> tuple[User, Session, str]:
        normalized = normalize_email(email)
        if self._users.get_by_email(normalized) is not None:
            raise errors.EmailTakenError("该邮箱已被注册")
        user = self._users.create(normalized, hash_password(password))
        return self._create_session(user, user_agent)

    def login(
        self,
        email: str,
        password: str,
        rate_limit_key: str,
        user_agent: str | None = None,
    ) -> tuple[User, Session, str]:
        if self._limiter.is_blocked(rate_limit_key):
            raise errors.RateLimitedError("登录尝试过于频繁，请稍后再试")

        user = self._users.get_by_email(normalize_email(email))
        if user is None or not verify_password(password, user.password_hash):
            # Unified message: does not reveal whether the email exists.
            self._limiter.record_failure(rate_limit_key)
            raise errors.InvalidCredentialsError("邮箱或密码不正确")

        self._limiter.reset(rate_limit_key)
        return self._create_session(user, user_agent)

    def authenticate_session(self, raw_token: str | None) -> tuple[User, Session] | None:
        if not raw_token:
            return None
        session = self._sessions.get_by_token_hash(tokens.hash_session_token(raw_token))
        if session is None or session.revoked_at is not None:
            return None
        now = datetime.now(UTC)
        if _as_utc(session.expires_at) <= now:
            return None
        user = self._users.get_by_id(session.user_id)
        if user is None:
            return None
        return user, session

    def logout(self, session: Session) -> None:
        self._sessions.revoke(session, datetime.now(UTC))

    def change_password(
        self,
        user: User,
        current_session: Session,
        current_password: str,
        new_password: str,
    ) -> tuple[User, Session, str]:
        if not verify_password(current_password, user.password_hash):
            raise errors.InvalidCredentialsError("当前密码不正确")

        self._users.set_password_hash(user, hash_password(new_password))
        now = datetime.now(UTC)
        # Revoke every other session, then rotate the current one with a fresh token.
        self._sessions.revoke_all_except(user.id, keep_session_id=current_session.id, now=now)
        self._sessions.revoke(current_session, now)
        return self._create_session(user, current_session.user_agent)

    def list_sessions(self, user: User) -> list[Session]:
        return self._sessions.list_by_user(user.id)

    def revoke_session(self, user: User, session_id: int) -> None:
        session = self._sessions.get_by_id(session_id)
        if session is None:
            raise errors.NotFoundError("会话不存在")
        errors.assert_owned(session.user_id, user.id)
        self._sessions.revoke(session, datetime.now(UTC))

    def revoke_other_sessions(self, user: User, keep_session_id: int) -> int:
        return self._sessions.revoke_all_except(
            user.id, keep_session_id=keep_session_id, now=datetime.now(UTC)
        )

    def _create_session(self, user: User, user_agent: str | None) -> tuple[User, Session, str]:
        token = tokens.generate_session_token()
        digest = tokens.hash_session_token(token)
        expires_at = datetime.now(UTC) + timedelta(
            seconds=self._settings.session_cookie_max_age_seconds
        )
        session = self._sessions.create(user.id, digest, expires_at, user_agent)
        return user, session, token
