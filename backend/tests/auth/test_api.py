"""Auth HTTP API behavior via TestClient (SQLite)."""

from __future__ import annotations

import pytest
from app.auth.deps import get_login_limiter
from app.auth.rate_limit import InMemoryLoginLimiter
from app.infra.db import Base
from app.infra.deps import get_db
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

COOKIE = "kairos_session"


@pytest.fixture()
def client(tmp_path) -> TestClient:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'api.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    limiter = InMemoryLoginLimiter(max_attempts=3, window_seconds=100)
    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_login_limiter] = lambda: limiter
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _register(client: TestClient, email: str, password: str = "password123") -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "confirm_password": password},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_register_sets_session_cookie(client: TestClient) -> None:
    body = _register(client, "alice@example.com")
    assert body["user"]["email"] == "alice@example.com"
    assert body["session"]["id"]
    assert COOKIE in client.cookies

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"


def test_register_duplicate_email_returns_409(client: TestClient) -> None:
    _register(client, "alice@example.com")
    resp = client.post(
        "/api/auth/register",
        json={
            "email": "alice@example.com",
            "password": "password123",
            "confirm_password": "password123",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "EMAIL_TAKEN"


def test_protected_route_rejects_unauthenticated(client: TestClient) -> None:
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "AUTH_REQUIRED"


def test_login_success_and_unified_failure(client: TestClient) -> None:
    _register(client, "alice@example.com", "password123")

    ok = client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "password123"}
    )
    assert ok.status_code == 200
    assert COOKIE in client.cookies

    bad = client.post("/api/auth/login", json={"email": "alice@example.com", "password": "nope"})
    assert bad.status_code == 401
    assert bad.json()["detail"]["code"] == "INVALID_CREDENTIALS"

    # Same unified message for a non-existent email.
    ghost = client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "nope"})
    assert ghost.status_code == 401
    assert ghost.json()["detail"]["message"] == bad.json()["detail"]["message"]


def test_login_rate_limit_returns_429(client: TestClient) -> None:
    _register(client, "alice@example.com", "password123")
    for _ in range(3):
        resp = client.post(
            "/api/auth/login", json={"email": "alice@example.com", "password": "bad"}
        )
        assert resp.status_code == 401

    blocked = client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "password123"}
    )
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "RATE_LIMITED"


def test_logout_clears_cookie_and_revokes_session(client: TestClient) -> None:
    _register(client, "alice@example.com", "password123")
    assert client.get("/api/auth/me").status_code == 200

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 204
    assert COOKIE not in client.cookies

    assert client.get("/api/auth/me").status_code == 401


def test_revoke_session_invalidates_that_session_only(client: TestClient) -> None:
    first = _register(client, "alice@example.com", "password123")
    first_token = client.cookies.get(COOKIE)
    session_a_id = first["session"]["id"]

    # Second login -> second session (jar now holds token B)
    client.post("/api/auth/login", json={"email": "alice@example.com", "password": "password123"})
    token_b = client.cookies.get(COOKIE)
    assert token_b != first_token

    # Revoke session A while authenticated as session B.
    client.cookies.set(COOKIE, token_b)
    resp = client.delete(f"/api/auth/sessions/{session_a_id}")
    assert resp.status_code == 204

    # Session A token is now invalid; session B still works.
    client.cookies.set(COOKIE, first_token)
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.set(COOKIE, token_b)
    assert client.get("/api/auth/me").status_code == 200


def test_cross_user_revoke_returns_404(client: TestClient) -> None:
    _register(client, "alice@example.com", "password123")
    token_a = client.cookies.get(COOKIE)

    b_body = _register(client, "bob@example.com", "password123")

    client.cookies.set(COOKIE, token_a)
    resp = client.delete(f"/api/auth/sessions/{b_body['session']['id']}")
    # 404, not 403: must not reveal that B's session exists.
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "NOT_FOUND"


def test_change_password_rotates_current_and_invalidates_old(client: TestClient) -> None:
    _register(client, "alice@example.com", "old-password-1")
    old_token = client.cookies.get(COOKIE)

    resp = client.post(
        "/api/auth/password",
        json={
            "current_password": "old-password-1",
            "new_password": "new-password-2",
            "confirm_password": "new-password-2",
        },
    )
    assert resp.status_code == 200
    new_token = client.cookies.get(COOKIE)
    assert new_token != old_token

    # Old session invalid; new cookie works; old password fails login.
    client.cookies.set(COOKIE, old_token)
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.set(COOKIE, new_token)
    assert client.get("/api/auth/me").status_code == 200
    assert (
        client.post(
            "/api/auth/login", json={"email": "alice@example.com", "password": "old-password-1"}
        ).status_code
        == 401
    )

    relogin = client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "new-password-2"}
    )
    assert relogin.status_code == 200
