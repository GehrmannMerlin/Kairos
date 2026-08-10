"""Stable domain error taxonomy (M-04)."""

from __future__ import annotations


class DomainError(Exception):
    code: str = "DOMAIN_ERROR"
    status_code: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict[str, str]:
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
