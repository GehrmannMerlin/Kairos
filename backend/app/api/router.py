"""Top-level API router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import auth, health, providers, tasks

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(providers.router)
api_router.include_router(tasks.router)
