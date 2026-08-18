"""Shared OpenAI-compatible model adapter core + four registrations.

``test_connection`` performs a minimal real request to ``{base_url}/models`` and
maps the status deterministically so unit tests drive every branch with a fake
transport (no real API keys, no network).
"""

from __future__ import annotations

from time import perf_counter

from app.providers import errors
from app.providers.protocol import (
    ModelCatalogResult,
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


def raise_for_search_status(http_status: int, *, retry_after_seconds: float | None = None) -> None:
    """Raise a typed ProviderError for a non-200 search response.

    ``search()`` must surface non-200 statuses so the M-16 provider-retry layer
    can classify/retry (429 backoff, auth fail-fast) instead of silently
    degrading a transient error into "0 results" / NO_MATCHING_PAGES.
    """
    if http_status == 200:
        return
    if http_status in (401, 403):
        raise errors.ProviderAuthFailedError("搜索服务认证失败")
    if http_status == 404:
        raise errors.ProviderNetworkError("搜索服务端点不可用")
    if http_status == 429:
        raise errors.ProviderRateLimitedError(
            "搜索服务限流", retry_after_seconds=retry_after_seconds
        )
    if 500 <= http_status < 600:
        raise errors.ProviderNetworkError("搜索服务返回服务端错误")
    raise errors.ProviderError(f"搜索服务返回 HTTP {http_status}")


def _parse_retry_after(resp) -> float | None:
    """Read the safe ``Retry-After`` header (seconds) when present."""
    value = (resp.headers or {}).get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class OpenAICompatibleModelProvider:
    definition: ProviderDefinition

    def __init__(self, definition: ProviderDefinition, http: HttpClient | None = None) -> None:
        self.definition = definition
        self._http = http or HttpxTransport()

    async def list_models(self, *, api_key: str | None, base_url: str | None) -> ModelCatalogResult:
        resolved_base_url = base_url or self.definition.default_base_url or ""
        endpoint = resolved_base_url.rstrip("/") + "/models"
        headers = {"Authorization": f"Bearer {api_key or ''}"}
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
                message="无法连接 Provider",
                latency_ms=int((perf_counter() - started) * 1000),
            )
        status, code = map_status(resp.status_code)
        models: tuple[str, ...] = ()
        message = None
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
                message = "无法读取模型目录"
        return ModelCatalogResult(
            status=status,
            models=models,
            resolved_base_url=resolved_base_url,
            error_code=code,
            message=message,
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
        return ProviderTestResult(
            status=status,
            error_code=code,
            message="连接成功" if status is ProviderTestStatus.AVAILABLE else catalog.message,
            latency_ms=catalog.latency_ms,
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
