"""Health endpoints.

``/health/live`` reflects API process liveness only.
``/health/ready`` reflects whether key infrastructure dependencies
(PostgreSQL, Temporal, object storage) are usable. It deliberately does NOT
depend on Model/Search providers or any web service.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import get_settings
from app.infra.db import ping
from app.infra.deps import get_object_storage, get_session_factory
from app.infra.temporal import create_temporal_client

router = APIRouter(tags=["health"])

TEMPORAL_HEALTH_TIMEOUT_SECONDS = 5.0


class LiveResponse(BaseModel):
    status: str
    service: str


class CheckResult(BaseModel):
    status: str
    error: str | None = None


class ReadyResponse(BaseModel):
    status: str
    checks: dict[str, CheckResult]


async def check_postgresql() -> CheckResult:
    try:
        session = get_session_factory()()
        try:
            await asyncio.to_thread(ping, session)
        finally:
            session.close()
        return CheckResult(status="ok")
    except Exception as exc:  # noqa: BLE001 - readiness reports any failure
        return CheckResult(status="error", error=f"{type(exc).__name__}: {exc}")


async def check_temporal() -> CheckResult:
    try:
        settings = get_settings()
        client = await asyncio.wait_for(
            create_temporal_client(settings), timeout=TEMPORAL_HEALTH_TIMEOUT_SECONDS
        )
        healthy = await asyncio.wait_for(
            client.service_client.check_health(), timeout=TEMPORAL_HEALTH_TIMEOUT_SECONDS
        )
        if not healthy:
            return CheckResult(status="error", error="temporal health check returned false")
        return CheckResult(status="ok")
    except Exception as exc:  # noqa: BLE001
        return CheckResult(status="error", error=f"{type(exc).__name__}: {exc}")


async def check_object_storage() -> CheckResult:
    try:
        storage = get_object_storage()
        await storage.ensure_bucket()
        return CheckResult(status="ok")
    except Exception as exc:  # noqa: BLE001
        return CheckResult(status="error", error=f"{type(exc).__name__}: {exc}")


@router.get("/health/live", response_model=LiveResponse)
async def health_live() -> LiveResponse:
    return LiveResponse(status="ok", service=get_settings().app_name)


@router.get("/health/ready")
async def health_ready() -> JSONResponse:
    pg, temporal, storage = await asyncio.gather(
        check_postgresql(), check_temporal(), check_object_storage()
    )
    checks = {
        "postgresql": pg,
        "temporal": temporal,
        "object_storage": storage,
    }
    all_ok = all(check.status == "ok" for check in checks.values())
    payload = ReadyResponse(status="ok" if all_ok else "degraded", checks=checks)
    status_code = 200 if all_ok else 503
    return JSONResponse(status_code=status_code, content=payload.model_dump())
