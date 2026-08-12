"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.router import api_router
from app.config import get_settings
from app.infra.telemetry import init_fastapi_telemetry, setup_otel


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.env == "production":
        settings.validate_runtime()  # M-17：production 配置违规立即失败，不静默带病上线
    setup_otel(settings)

    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/api")
    register_exception_handlers(app)

    init_fastapi_telemetry(app, settings)
    return app


app = create_app()
