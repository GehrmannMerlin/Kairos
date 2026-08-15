"""Stable domain error taxonomy (M-04)."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    code: str = "DOMAIN_ERROR"
    status_code: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


class IllegalTransitionError(DomainError):
    code = "ILLEGAL_TRANSITION"
    status_code = 409


class StaleVersionError(DomainError):
    code = "STALE_VERSION"
    status_code = 409


class IdempotencyConflictError(DomainError):
    code = "IDEMPOTENCY_CONFLICT"
    status_code = 409


class SpecValidationError(DomainError):
    code = "SPEC_VALIDATION_ERROR"
    status_code = 422


class PlanStartFailedError(DomainError):
    code = "PLAN_START_FAILED"
    status_code = 503

    def __init__(self, message: str, *, context: dict[str, Any]) -> None:
        super().__init__(message)
        self.context = context

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), **self.context}


class PlanGenerationTimeoutError(DomainError):
    code = "PLAN_GENERATION_TIMEOUT"
    status_code = 504
