"""Auth API routes.

Thin layer: validate DTO → call AuthService → set/clear the session cookie.
No SQL or password logic here.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response

from app.auth.deps import get_auth_service, require_session, require_user
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.schemas import (
    AuthResponse,
    ChangePasswordCommand,
    LoginCommand,
    RegisterCommand,
    SessionDto,
    SessionsResponse,
    UserDto,
)
from app.auth.service import AuthService
from app.config import Settings, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_cookie_max_age_seconds,
        httponly=settings.session_cookie_httponly,
        samesite=settings.session_cookie_samesite,
        secure=settings.session_cookie_secure,
        path=settings.session_cookie_path,
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(key=settings.session_cookie_name, path=settings.session_cookie_path)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _session_dto(session: AuthSession, current_id: int) -> SessionDto:
    return SessionDto(
        id=session.id,
        created_at=_utc(session.created_at) or datetime.now(UTC),
        expires_at=_utc(session.expires_at) or datetime.now(UTC),
        revoked_at=_utc(session.revoked_at),
        is_current=session.id == current_id,
    )


def _auth_response(user: User, session: AuthSession) -> AuthResponse:
    return AuthResponse(
        user=UserDto.model_validate(user),
        session=_session_dto(session, session.id),
    )


@router.post("/register", response_model=AuthResponse, status_code=201)
def register(
    cmd: RegisterCommand,
    response: Response,
    request: Request,
    service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> AuthResponse:
    user, session, token = service.register(
        cmd.email, cmd.password, request.headers.get("user-agent")
    )
    _set_session_cookie(response, settings, token)
    return _auth_response(user, session)


@router.post("/login", response_model=AuthResponse)
def login(
    cmd: LoginCommand,
    response: Response,
    request: Request,
    service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> AuthResponse:
    client_host = request.client.host if request.client else "unknown"
    user, session, token = service.login(
        cmd.email,
        cmd.password,
        rate_limit_key=f"login:{client_host}",
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookie(response, settings, token)
    return _auth_response(user, session)


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    session: AuthSession = Depends(require_session),
    service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> None:
    service.logout(session)
    _clear_session_cookie(response, settings)
    return None


@router.get("/me", response_model=UserDto)
def me(user: User = Depends(require_user)) -> UserDto:
    return UserDto.model_validate(user)


@router.post("/password", response_model=AuthResponse)
def change_password(
    cmd: ChangePasswordCommand,
    response: Response,
    user: User = Depends(require_user),
    session: AuthSession = Depends(require_session),
    service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> AuthResponse:
    _, new_session, new_token = service.change_password(
        user, session, cmd.current_password, cmd.new_password
    )
    _set_session_cookie(response, settings, new_token)
    return _auth_response(user, new_session)


@router.get("/sessions", response_model=SessionsResponse)
def list_sessions(
    user: User = Depends(require_user),
    session: AuthSession = Depends(require_session),
    service: AuthService = Depends(get_auth_service),
) -> SessionsResponse:
    sessions = service.list_sessions(user)
    return SessionsResponse(sessions=[_session_dto(s, session.id) for s in sessions])


@router.post("/sessions/logout-others", status_code=204)
def logout_others(
    user: User = Depends(require_user),
    session: AuthSession = Depends(require_session),
    service: AuthService = Depends(get_auth_service),
) -> None:
    service.revoke_other_sessions(user, session.id)
    return None


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: int,
    user: User = Depends(require_user),
    service: AuthService = Depends(get_auth_service),
) -> None:
    service.revoke_session(user, session_id)
    return None
