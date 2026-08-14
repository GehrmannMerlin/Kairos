"""Native Anthropic model adapter (minimal connection test against /v1/models)."""

from __future__ import annotations

from time import perf_counter

from app.providers.adapters.openai_compatible import map_status
from app.providers.protocol import (
    BaseUrlMode,
    ModelCatalogResult,
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

    async def list_models(self, *, api_key: str | None, base_url: str | None) -> ModelCatalogResult:
        resolved_base_url = base_url or self.definition.default_base_url or ""
        endpoint = resolved_base_url.rstrip("/") + "/v1/models"
        headers = {"x-api-key": api_key or "", "anthropic-version": ANTHROPIC_VERSION}
        started = perf_counter()
        try:
            resp = await self._http.request(
                method="GET", url=endpoint, headers=headers, params=None, timeout_seconds=15.0
            )
        except Exception:
            return ModelCatalogResult(
                status=ProviderTestStatus.NETWORK_ERROR,
                resolved_base_url=resolved_base_url,
                error_code="NETWORK_ERROR",
                latency_ms=int((perf_counter() - started) * 1000),
            )
        status, code = map_status(resp.status_code)
        models: tuple[str, ...] = ()
        if status is ProviderTestStatus.AVAILABLE:
            rows = resp.body.get("data") if isinstance(resp.body, dict) else None
            ids = (
                [row.get("id") for row in rows if isinstance(row, dict)]
                if isinstance(rows, list)
                else []
            )
            models = tuple(
                dict.fromkeys(value for value in ids if isinstance(value, str) and value)
            )
            if not models:
                status = ProviderTestStatus.FAILED
                code = "INVALID_CATALOG_RESPONSE"
        return ModelCatalogResult(
            status=status,
            models=models,
            resolved_base_url=resolved_base_url,
            error_code=code,
            latency_ms=int((perf_counter() - started) * 1000),
        )

    async def test_connection(
        self, *, api_key: str | None, model: str | None, base_url: str | None
    ) -> ProviderTestResult:
        catalog = await self.list_models(api_key=api_key, base_url=base_url)
        status = catalog.status
        code = catalog.error_code
        if status is ProviderTestStatus.AVAILABLE and model and model not in catalog.models:
            status = ProviderTestStatus.MODEL_NOT_FOUND
            code = "MODEL_NOT_FOUND"
        return ProviderTestResult(status=status, error_code=code, latency_ms=catalog.latency_ms)

    def resolve_model(
        self, *, model: str, base_url: str | None, credential_version_id: int | None
    ) -> ResolvedModel:
        return ResolvedModel(
            provider_type=self.definition.provider_type,
            model_name=model,
            base_url=base_url or self.definition.default_base_url,
            credential_version_id=credential_version_id,
        )
