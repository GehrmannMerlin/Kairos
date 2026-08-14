"""Native Google Gemini model adapter (minimal connection test).

Gemini returns HTTP 400 with an "API key not valid" payload for bad keys, so
400 is mapped to AUTH_FAILED (unlike other providers).
"""

from __future__ import annotations

from time import perf_counter

from app.providers.protocol import (
    BaseUrlMode,
    ModelCatalogResult,
    ProviderDefinition,
    ProviderTestResult,
    ProviderTestStatus,
    ResolvedModel,
)
from app.providers.transport import HttpClient, HttpxTransport


def map_gemini_status(http_status: int) -> tuple[ProviderTestStatus, str | None]:
    if http_status == 200:
        return ProviderTestStatus.AVAILABLE, None
    if http_status in (400, 401, 403):
        return ProviderTestStatus.AUTH_FAILED, f"HTTP_{http_status}"
    if http_status == 404:
        return ProviderTestStatus.MODEL_NOT_FOUND, "HTTP_404"
    if http_status == 429:
        return ProviderTestStatus.RATE_LIMITED, "HTTP_429"
    return ProviderTestStatus.FAILED, f"HTTP_{http_status}"


class GeminiModelProvider:
    definition = ProviderDefinition(
        provider_type="gemini",
        display_name="Google Gemini",
        requires_api_key=True,
        requires_model_name=True,
        requires_base_url=False,
        default_base_url="https://generativelanguage.googleapis.com/v1beta",
        protocol_family="gemini",
        base_url_mode=BaseUrlMode.MANAGED,
    )

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpxTransport()

    async def list_models(self, *, api_key: str | None, base_url: str | None) -> ModelCatalogResult:
        resolved_base_url = base_url or self.definition.default_base_url or ""
        endpoint = resolved_base_url.rstrip("/") + "/models"
        started = perf_counter()
        try:
            resp = await self._http.request(
                method="GET",
                url=endpoint,
                headers={"x-goog-api-key": api_key or ""},
                params={"key": api_key or "", "pageSize": "1000"},
                timeout_seconds=15.0,
            )
        except Exception:
            return ModelCatalogResult(
                status=ProviderTestStatus.NETWORK_ERROR,
                resolved_base_url=resolved_base_url,
                error_code="NETWORK_ERROR",
                latency_ms=int((perf_counter() - started) * 1000),
            )
        status, code = map_gemini_status(resp.status_code)
        models: tuple[str, ...] = ()
        if status is ProviderTestStatus.AVAILABLE:
            rows = resp.body.get("models") if isinstance(resp.body, dict) else None
            ids: list[str] = []
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    methods = row.get("supportedGenerationMethods")
                    if not isinstance(methods, list) or "generateContent" not in methods:
                        continue
                    value = row.get("baseModelId")
                    if not isinstance(value, str) or not value:
                        name = row.get("name")
                        value = name.removeprefix("models/") if isinstance(name, str) else ""
                    if value:
                        ids.append(value)
            models = tuple(dict.fromkeys(ids))
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
