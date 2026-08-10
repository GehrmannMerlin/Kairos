"""FastAPI exception handlers mapping domain errors to stable responses.

Response shape: ``{"detail": {"code": str, "message": str}}``.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.auth.errors import AuthError


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthError)
    async def handle_auth_error(_request: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.to_dict()},
        )
