"""Native Anthropic model adapter (minimal connection test against /v1/models)."""

from __future__ import annotations

from time import perf_counter

from app.providers.adapters.openai_compatible import map_status
from app.providers.protocol import (
    BaseUrlMode,
    ProviderDefinition,
    ProviderTestResult,
    ProviderTestStatus,
    ResolvedModel,
)
from app.providers.transport import HttpClient, HttpxTransport

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicModelProvider:
    definition = ProviderDefinition(
        provider_type="anthropic",
        display_name="Anthropic",
        requires_api_key=True,
        requires_model_name=True,
        requires_base_url=False,
        default_base_url="https://api.anthropic.com",
        protocol_family="anthropic",
        base_url_mode=BaseUrlMode.MANAGED,
    )

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpxTransport()

    async def test_connection(
        self, *, api_key: str | None, model: str | None, base_url: str | None
    ) -> ProviderTestResult:
        endpoint = (base_url or self.definition.default_base_url or "").rstrip("/") + "/v1/models"
        headers = {"x-api-key": api_key or "", "anthropic-version": ANTHROPIC_VERSION}
        started = perf_counter()
        try:
            resp = await self._http.request(
                method="GET", url=endpoint, headers=headers, params=None, timeout_seconds=15.0
            )
        except Exception:
            return ProviderTestResult(
                status=ProviderTestStatus.NETWORK_ERROR,
                error_code="NETWORK_ERROR",
                latency_ms=int((perf_counter() - started) * 1000),
            )
        status, code = map_status(resp.status_code)
        return ProviderTestResult(
            status=status, error_code=code, latency_ms=int((perf_counter() - started) * 1000)
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
