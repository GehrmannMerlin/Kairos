"""Stable provider error taxonomy (M-03)."""

from __future__ import annotations


class ProviderError(Exception):
    code: str = "PROVIDER_ERROR"
    status_code: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class ProviderValidationError(ProviderError):
    code = "PROVIDER_VALIDATION_ERROR"
    status_code = 422


class ModelNotConfiguredError(ProviderError):
    code = "MODEL_NOT_CONFIGURED"
    status_code = 409


class SearchProviderNotConfiguredError(ProviderError):
    code = "SEARCH_PROVIDER_NOT_CONFIGURED"
    status_code = 409
