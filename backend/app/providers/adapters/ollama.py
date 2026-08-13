"""Ollama adapter: no API key, local endpoint, GET /api/tags."""

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


class OllamaModelProvider:
    definition = ProviderDefinition(
        provider_type="ollama",
        display_name="Ollama",
        requires_api_key=False,
        requires_model_name=True,
        requires_base_url=True,
        default_base_url="http://localhost:11434",
        protocol_family="ollama",
        base_url_mode=BaseUrlMode.LOCAL_REQUIRED,
    )

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpxTransport()

    async def test_connection(
        self, *, api_key: str | None, model: str | None, base_url: str | None
    ) -> ProviderTestResult:
        endpoint = (base_url or self.definition.default_base_url or "").rstrip("/") + "/api/tags"
        started = perf_counter()
        try:
            resp = await self._http.request(
                method="GET", url=endpoint, headers=None, params=None, timeout_seconds=15.0
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
