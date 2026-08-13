"""Top-level API router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    approvals,
    artifacts,
    auth,
    completion,
    credentials,
    events,
    evidence,
    execution,
    health,
    plans,
    providers,
    quality,
    records,
    settings_data,
    tasks,
    templates,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(providers.router)
api_router.include_router(tasks.router)
api_router.include_router(templates.router)
api_router.include_router(plans.router)
api_router.include_router(approvals.router)
api_router.include_router(approvals.task_router)
api_router.include_router(credentials.router)
api_router.include_router(credentials.saved_router)
api_router.include_router(records.router)
api_router.include_router(events.router)
api_router.include_router(quality.router)
api_router.include_router(execution.router)
api_router.include_router(evidence.router)
api_router.include_router(artifacts.router)
api_router.include_router(completion.router)
api_router.include_router(settings_data.router)
