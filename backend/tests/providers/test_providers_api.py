"""Provider HTTP API behavior (SQLite, no real keys)."""

from __future__ import annotations

import http.server
import json
import threading
from collections.abc import Iterator

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
PASSWORD = "password123"
MASTER_KEY = "ab" * 32


@pytest.fixture()
def env_master_key(monkeypatch) -> None:
    monkeypatch.setenv("KAIROS_CREDENTIAL_MASTER_KEY", MASTER_KEY)


class _StubHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps(
            {
                "object": "list",
                "data": [{"id": "gpt-4o-mini", "object": "model", "owned_by": "fixture"}],
            }
        ).encode()
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


@pytest.fixture()
def client_factory(env_master_key, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'prov_api.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override_db() -> Iterator:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    limiter = InMemoryLoginLimiter(max_attempts=3, window_seconds=100)
    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_login_limiter] = lambda: limiter

    def _make() -> TestClient:
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "confirm_password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_model(client: TestClient, **overrides) -> dict:
    body = {
        "name": "main",
        "provider_type": "openai",
        "model_name": "gpt-4o-mini",
        "api_key": "sk-secret-123",
        **overrides,
    }
    resp = client.post("/api/providers/models", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_model_never_returns_plaintext(client_factory) -> None:
    client = client_factory()
    with client:
        _register(client, "alice@example.com")
        created = _create_model(client)
        assert created["credential_configured"] is True
        text = repr(created)
        assert "sk-secret-123" not in text
        assert "ciphertext" not in text
        assert "wrapped" not in text
        assert "nonce" not in text
        assert "master_key" not in text


def test_definitions_endpoint_lists_registry(client_factory) -> None:
    client = client_factory()
    with client:
        _register(client, "alice@example.com")
        resp = client.get("/api/providers/definitions")
        assert resp.status_code == 200
        model_types = {d["provider_type"] for d in resp.json()["models"]}
        assert {
            "openai",
            "anthropic",
            "gemini",
            "deepseek",
            "openrouter",
            "ollama",
            "custom_openai_compatible",
        } <= model_types
        search_types = {d["provider_type"] for d in resp.json()["searches"]}
        assert "custom_compatible_search" in search_types


def test_probe_ambiguous_no_network_no_echo(client_factory) -> None:
    client = client_factory()
    with client:
        _register(client, "alice@example.com")
        # Generic sk-* key is AMBIGUOUS: the endpoint must NOT hit the network
        # and must not echo the key.
        resp = client.post("/api/providers/models/probe", json={"api_key": "sk-1234567890abcdef"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["detection_confidence"] == "AMBIGUOUS"
        assert body["status"] is None
        assert body["detected_provider"] is None
        assert "sk-1234567890abcdef" not in resp.text


def test_cross_user_blocked(client_factory) -> None:
    a = client_factory()
    b = client_factory()
    with a, b:
        _register(a, "alice@example.com")
        created = _create_model(a)
        _register(b, "bob@example.com")
        assert b.get("/api/providers/models").json()["configs"] == []
        resp = b.patch(
            f"/api/providers/models/{created['config_id']}",
            json={"name": "hack", "provider_type": "openai", "model_name": "x"},
        )
        assert resp.status_code == 404
        resp = b.delete(f"/api/providers/models/{created['config_id']}")
        assert resp.status_code == 404


def test_edit_and_replace_key_bump_version(client_factory) -> None:
    client = client_factory()
    with client:
        _register(client, "alice@example.com")
        created = _create_model(client)
        edited = client.patch(
            f"/api/providers/models/{created['config_id']}",
            json={"name": "main-2", "provider_type": "openai", "model_name": "gpt-4o"},
        )
        assert edited.status_code == 200
        assert edited.json()["version"] == 2
        replaced = client.post(
            f"/api/providers/models/{created['config_id']}/key", json={"api_key": "sk-new-key"}
        )
        assert replaced.status_code == 200
        assert replaced.json()["version"] == 3
        assert "sk-new-key" not in repr(replaced.json())


def test_set_default_and_delete(client_factory) -> None:
    client = client_factory()
    with client:
        _register(client, "alice@example.com")
        created = _create_model(client)
        defaulted = client.post(f"/api/providers/models/{created['config_id']}/default")
        assert defaulted.status_code == 200
        assert defaulted.json()["is_default"] is True
        deleted = client.delete(f"/api/providers/models/{created['config_id']}")
        assert deleted.status_code == 204
        remaining = client.get("/api/providers/models").json()["configs"]
        assert all(c["config_id"] != created["config_id"] for c in remaining)


def test_connection_test_against_stub(client_factory, stub_url: str) -> None:
    client = client_factory()
    with client:
        _register(client, "alice@example.com")
        created = _create_model(client, base_url=stub_url)
        tested = client.post(f"/api/providers/models/{created['config_id']}/test")
        assert tested.status_code == 200
        assert tested.json()["status"] == "AVAILABLE"


def test_transient_model_catalog_returns_ids_without_persisting_or_echoing_key(
    client_factory, stub_url: str
) -> None:
    client = client_factory()
    with client:
        _register(client, "alice@example.com")
        resp = client.post(
            "/api/providers/models/catalog",
            json={
                "provider_type": "custom_openai_compatible",
                "base_url": stub_url,
                "api_key": "transient-fixture-secret",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "AVAILABLE"
        assert body["models"] == ["gpt-4o-mini"]
        assert body["resolved_base_url"] == stub_url
        assert "transient-fixture-secret" not in resp.text
        assert client.get("/api/providers/models").json()["configs"] == []


# ---- Search config + probe regression (Tavily must NOT require Base URL) ----


def test_create_tavily_search_without_base_url(client_factory) -> None:
    """Tavily is a managed Search Provider: name + provider + api_key must save
    without any base_url. Regression for the user-visible drawer error."""
    client = client_factory()
    with client:
        _register(client, "alice@example.com")
        resp = client.post(
            "/api/providers/searches",
            json={"name": "Tavily", "provider_type": "tavily", "api_key": "tvly-secret-123"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["provider_type"] == "tavily"
        assert body["base_url"] is None
        assert body["credential_configured"] is True
        assert "tvly-secret-123" not in repr(body)


def test_custom_search_without_base_url_rejected(client_factory) -> None:
    """custom_compatible_search still requires a Base URL — per-definition."""
    client = client_factory()
    with client:
        _register(client, "alice@example.com")
        resp = client.post(
            "/api/providers/searches",
            json={"name": "custom", "provider_type": "custom_compatible_search", "api_key": "sk-x"},
        )
        assert resp.status_code == 422, resp.text


def test_search_probe_custom_available_against_stub(client_factory, stub_url: str) -> None:
    """Search probe endpoint performs a real minimal request and returns latency
    without persisting a config or a credential."""
    client = client_factory()
    with client:
        _register(client, "alice@example.com")
        resp = client.post(
            "/api/providers/searches/probe",
            json={
                "provider_type": "custom_compatible_search",
                "api_key": "sk-stub",
                "base_url": stub_url,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "AVAILABLE"
        assert body["provider_type"] == "custom_compatible_search"
        assert body["latency_ms"] is not None
        assert body["resolved_base_url"] == stub_url
        assert "sk-stub" not in resp.text
        # Probe must not create a SearchConfig / Credential.
        remaining = client.get("/api/providers/searches").json()["configs"]
        assert remaining == []
