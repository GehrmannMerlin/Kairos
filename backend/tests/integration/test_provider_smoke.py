"""M-03 Provider/Credential Smoke against live local PostgreSQL.

Chain: A registers -> creates ModelConfig with key -> response has no plaintext
-> DB has no plaintext -> test connection against local stub -> AVAILABLE ->
edit (version+1) -> replace key (credential+config version+1) -> B is blocked ->
create SearchConfig -> test AVAILABLE -> delete config -> no longer listed.

No real commercial API key required: a local stub HTTP server plays the provider.
"""

from __future__ import annotations

import http.server
import json
import threading
from uuid import uuid4

import pytest
from app.auth.models import User
from app.credentials.models import CredentialVersion
from app.infra.deps import get_session_factory
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import delete

pytestmark = pytest.mark.integration

COOKIE = "kairos_session"
PASSWORD = "password-123"
SECRET = "sk-test-secret-000"


class _StubHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"results": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: ANN002
        pass


@pytest.fixture(scope="module")
def stub_url() -> str:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "confirm_password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _assert_no_plaintext_in_db() -> None:
    session = get_session_factory()()
    try:
        rows = session.query(CredentialVersion).all()
        text = repr([{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows])
        assert SECRET not in text
        for r in rows:
            if r.status == "active":
                assert r.secret_ciphertext != b""
    finally:
        session.close()


def _cleanup_users(emails: list[str]) -> None:
    session = get_session_factory()()
    try:
        session.execute(delete(User).where(User.email.in_(emails)))
        session.commit()
    finally:
        session.close()


def test_provider_credential_smoke(stub_url: str) -> None:
    tag = uuid4().hex[:8]
    email_a = f"alice-{tag}@example.com"
    email_b = f"bob-{tag}@example.com"
    try:
        app = create_app()
        with TestClient(app) as a, TestClient(app) as b:
            _register(a, email_a)
            _register(b, email_b)

            # A creates a model config with an API key.
            created = a.post(
                "/api/providers/models",
                json={
                    "name": "smoke",
                    "provider_type": "openai",
                    "model_name": "gpt-4o-mini",
                    "base_url": stub_url,
                    "api_key": SECRET,
                },
            )
            assert created.status_code == 201, created.text
            created_body = created.json()
            assert SECRET not in repr(created_body)
            assert created_body["credential_configured"] is True

            # Connection test against the local stub -> AVAILABLE.
            tested = a.post(f"/api/providers/models/{created_body['config_id']}/test")
            assert tested.status_code == 200
            assert tested.json()["status"] == "AVAILABLE"

            # Edit -> version +1.
            edited = a.patch(
                f"/api/providers/models/{created_body['config_id']}",
                json={"name": "smoke-2", "provider_type": "openai", "model_name": "gpt-4o"},
            )
            assert edited.status_code == 200
            assert edited.json()["version"] == created_body["version"] + 1

            # Replace key -> credential version +1 and config version +1.
            replaced = a.post(
                f"/api/providers/models/{created_body['config_id']}/key",
                json={"api_key": SECRET + "x"},
            )
            assert replaced.status_code == 200
            assert replaced.json()["version"] == created_body["version"] + 2

            # Cross-user: B sees nothing and cannot touch A's config.
            assert b.get("/api/providers/models").json()["configs"] == []
            blocked = b.patch(
                f"/api/providers/models/{created_body['config_id']}",
                json={"name": "hack", "provider_type": "openai", "model_name": "x"},
            )
            assert blocked.status_code == 404

            # Search config create + test -> AVAILABLE.
            s_created = a.post(
                "/api/providers/searches",
                json={
                    "name": "search-1",
                    "provider_type": "custom_compatible_search",
                    "base_url": stub_url,
                    "api_key": "sk-search",
                },
            )
            assert s_created.status_code == 201
            s_tested = a.post(f"/api/providers/searches/{s_created.json()['config_id']}/test")
            assert s_tested.status_code == 200
            assert s_tested.json()["status"] == "AVAILABLE"

            # Delete -> no longer listed as an available config.
            deleted = a.delete(f"/api/providers/models/{created_body['config_id']}")
            assert deleted.status_code == 204
            remaining = a.get("/api/providers/models").json()["configs"]
            assert all(c["config_id"] != created_body["config_id"] for c in remaining)

        # Database holds no plaintext secret (encrypted at rest).
        _assert_no_plaintext_in_db()
    finally:
        _cleanup_users([email_a, email_b])
