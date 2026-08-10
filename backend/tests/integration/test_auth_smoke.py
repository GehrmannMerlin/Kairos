"""M-02 Auth Smoke against the live local PostgreSQL.

Chain: A registers -> session -> /me; B registers; A cannot touch B's session
(404); A changes password (old session invalid); re-login works; logout
invalidates the session.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.auth.models import User
from app.infra.deps import get_session_factory
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import delete

pytestmark = pytest.mark.integration

COOKIE = "kairos_session"
PASSWORD = "password-123"


@pytest.fixture()
def app() -> TestClient:
    return create_app()


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "confirm_password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _cleanup_users(emails: list[str]) -> None:
    session = get_session_factory()()
    try:
        session.execute(delete(User).where(User.email.in_(emails)))
        session.commit()
    finally:
        session.close()


def test_auth_smoke(app: TestClient) -> None:
    tag = uuid4().hex[:8]
    email_a = f"alice-{tag}@example.com"
    email_b = f"bob-{tag}@example.com"

    try:
        # A registers -> session established -> /me works
        with TestClient(app) as client_a, TestClient(app) as client_b:
            a_body = _register(client_a, email_a)
            token_a = client_a.cookies.get(COOKIE)
            assert client_a.get("/api/auth/me").status_code == 200

            # B registers -> separate session
            b_body = _register(client_b, email_b)

            # A attempts to revoke B's session -> 404 (no existence leak)
            revoke = client_a.delete(f"/api/auth/sessions/{b_body['session']['id']}")
            assert revoke.status_code == 404

            # A changes password -> old session invalid, new cookie works
            change = client_a.post(
                "/api/auth/password",
                json={
                    "current_password": PASSWORD,
                    "new_password": "password-456",
                    "confirm_password": "password-456",
                },
            )
            assert change.status_code == 200, change.text
            new_token = client_a.cookies.get(COOKIE)
            assert new_token != token_a
            assert client_a.get("/api/auth/me").status_code == 200

            # Old token no longer authenticates (fresh client, single cookie).
            with TestClient(app) as old_session_client:
                old_session_client.cookies.set(COOKIE, token_a)
                assert old_session_client.get("/api/auth/me").status_code == 401

            # Re-login with the new password succeeds.
            relogin = client_a.post(
                "/api/auth/login", json={"email": email_a, "password": "password-456"}
            )
            assert relogin.status_code == 200

            # Logout -> protected endpoint rejects.
            assert client_a.post("/api/auth/logout").status_code == 204
            assert client_a.get("/api/auth/me").status_code == 401

        # Sanity: the session id returned came from real rows.
        assert a_body["session"]["id"] > 0
        assert b_body["session"]["id"] > 0
    finally:
        _cleanup_users([email_a, email_b])
