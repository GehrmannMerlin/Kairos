"""Auth FastAPI dependencies: CurrentUser, current session, ownership guard.

``assert_owned`` is the unified ownership primitive future business modules
reuse; cross-user access raises 404 so existence is never revealed.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Request
from sqlalchemy.orm import Session as DbSession

from app.auth import errors
from app.auth.errors import assert_owned  # noqa: F401 - unified guard reused by modules
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.rate_limit import InMemoryLoginLimiter
from app.auth.repository import SessionRepository, UserRepository
from app.auth.service import AuthService
from app.config import Settings, get_settings
from app.infra.deps import get_db


@lru_cache
def get_login_limiter() -> InMemoryLoginLimiter:
    """Process-scoped login limiter (single-instance dev).

    Swappable behind ``LoginRateLimiter`` for a shared implementation later.
    """
    settings = get_settings()
    return InMemoryLoginLimiter(
        max_attempts=settings.auth_login_max_attempts,
        window_seconds=settings.auth_login_window_seconds,
    )


def get_user_repo(db: DbSession = Depends(get_db)) -> UserRepository:
    return UserRepository(db)


def get_session_repo(db: DbSession = Depends(get_db)) -> SessionRepository:
    return SessionRepository(db)


def get_auth_service(
    users: UserRepository = Depends(get_user_repo),
    sessions: SessionRepository = Depends(get_session_repo),
    settings: Settings = Depends(get_settings),
    limiter: InMemoryLoginLimiter = Depends(get_login_limiter),
) -> AuthService:
    return AuthService(users, sessions, settings, limiter)


def _resolve_auth(
    request: Request,
    settings: Settings,
    service: AuthService,
) -> tuple[User, AuthSession]:
    cached = getattr(request.state, "auth", None)
    if cached is not None:
        return cached
    token = request.cookies.get(settings.session_cookie_name)
    result = service.authenticate_session(token)
    if result is None:
        raise errors.AuthenticationRequiredError("请先登录")
    request.state.auth = result
    return result


def require_user(
    request: Request,
    settings: Settings = Depends(get_settings),
    service: AuthService = Depends(get_auth_service),
) -> User:
    user, _ = _resolve_auth(request, settings, service)
    return user


def require_session(
    request: Request,
    settings: Settings = Depends(get_settings),
    service: AuthService = Depends(get_auth_service),
) -> AuthSession:
    _, session = _resolve_auth(request, settings, service)
    return session
