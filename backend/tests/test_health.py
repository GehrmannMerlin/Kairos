"""Health endpoint behavior (unit-level; live dependency checks are integration)."""

from __future__ import annotations

from app.api.routes import health
from app.main import create_app
from fastapi.testclient import TestClient


def _client() -> TestClient:
    return TestClient(create_app())


def test_health_live_ok() -> None:
    with _client() as client:
        resp = client.get("/api/health/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"]


def test_health_ready_all_ok_returns_200(monkeypatch) -> None:
    async def ok() -> health.CheckResult:
        return health.CheckResult(status="ok")

    monkeypatch.setattr(health, "check_postgresql", ok)
    monkeypatch.setattr(health, "check_temporal", ok)
    monkeypatch.setattr(health, "check_object_storage", ok)

    with _client() as client:
        resp = client.get("/api/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_ready_degraded_returns_503(monkeypatch) -> None:
    async def ok() -> health.CheckResult:
        return health.CheckResult(status="ok")

    async def broken() -> health.CheckResult:
        return health.CheckResult(status="error", error="connection refused")

    monkeypatch.setattr(health, "check_postgresql", ok)
    monkeypatch.setattr(health, "check_temporal", broken)
    monkeypatch.setattr(health, "check_object_storage", ok)

    with _client() as client:
        resp = client.get("/api/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["temporal"]["status"] == "error"
    assert body["checks"]["postgresql"]["status"] == "ok"
