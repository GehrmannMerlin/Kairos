"""Stable provider error taxonomy (M-03)."""

from __future__ import annotations

from enum import StrEnum


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


class ProviderAuthFailedError(ProviderError):
    code = "AUTH_FAILED"
    status_code = 401


class ProviderModelNotFoundError(ProviderError):
    code = "MODEL_NOT_FOUND"
    status_code = 404


class ProviderRateLimitedError(ProviderError):
    code = "RATE_LIMITED"
    status_code = 429

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.request_id = request_id


class ProviderNetworkError(ProviderError):
    code = "NETWORK_ERROR"
    status_code = 503


class TimeoutPhase(StrEnum):
    CONNECT = "connect"
    READ = "read"
    OVERALL = "overall"


class ProviderTimeoutError(ProviderError):
    code = "PROVIDER_TIMEOUT"
    status_code = 504

    def __init__(self, *, phase: TimeoutPhase) -> None:
        self.phase = phase
        super().__init__(f"provider timeout during {phase.value}")

    def to_dict(self) -> dict[str, str]:
        payload = super().to_dict()
        payload["phase"] = self.phase.value
        return payload


class ProviderInferenceError(ProviderError):
    """A provider call succeeded transport-wise but produced no usable output."""

    code = "PROVIDER_INFERENCE_ERROR"
    status_code = 502
