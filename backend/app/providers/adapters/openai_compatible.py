"""Shared OpenAI-compatible model adapter core + four registrations.

``test_connection`` performs a minimal real request to ``{base_url}/models`` and
maps the status deterministically so unit tests drive every branch with a fake
transport (no real API keys, no network).
"""

from __future__ import annotations

from time import perf_counter

from app.providers.protocol import (
    ProviderDefinition,
    ProviderTestResult,
    ProviderTestStatus,
    ResolvedModel,
)
from app.providers.transport import HttpClient, HttpxTransport


def map_status(
    http_status: int, *, model_specific_404: bool = True
) -> tuple[ProviderTestStatus, str | None]:
    if http_status == 200:
        return ProviderTestStatus.AVAILABLE, None
    if http_status in (401, 403):
        return ProviderTestStatus.AUTH_FAILED, f"HTTP_{http_status}"
    if http_status == 404:
        if model_specific_404:
            return ProviderTestStatus.MODEL_NOT_FOUND, "HTTP_404"
        return ProviderTestStatus.NETWORK_ERROR, "HTTP_404"
    if http_status == 429:
        return ProviderTestStatus.RATE_LIMITED, "HTTP_429"
    return ProviderTestStatus.FAILED, f"HTTP_{http_status}"


class OpenAICompatibleModelProvider:
    definition: ProviderDefinition

    def __init__(self, definition: ProviderDefinition, http: HttpClient | None = None) -> None:
        self.definition = definition
        self._http = http or HttpxTransport()

    async def test_connection(
        self, *, api_key: str | None, model: str | None, base_url: str | None
    ) -> ProviderTestResult:
        endpoint = (base_url or self.definition.default_base_url or "").rstrip("/") + "/models"
        headers = {"Authorization": f"Bearer {api_key or ''}"}
        started = perf_counter()
        try:
            resp = await self._http.request(
                method="GET", url=endpoint, headers=headers, params=None, timeout_seconds=15.0
            )
        except Exception:
            return ProviderTestResult(
                status=ProviderTestStatus.NETWORK_ERROR,
                error_code="NETWORK_ERROR",
                message="无法连接 Provider",
                latency_ms=int((perf_counter() - started) * 1000),
            )
        status, code = map_status(resp.status_code)
        return ProviderTestResult(
            status=status,
            error_code=code,
            message="连接成功" if status is ProviderTestStatus.AVAILABLE else None,
            latency_ms=int((perf_counter() - started) * 1000),
        )

    def resolve_model(
        self, *, model: str, base_url: str | None, credential_version_id: int | None
    ) -> ResolvedModel:
        return ResolvedModel(
            provider_type=self.definition.provider_type,
            model_name=model,
            base_url=base_url or self.definition.default_base_url,
            credential_version_id=credential_version_id,
        )
