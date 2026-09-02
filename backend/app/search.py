from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings


class NormalizedSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    title: str
    snippet: str = Field(max_length=500)
    rank: int = Field(ge=1)
    provider_score: float | None = None
    published_at: str | None = None


class SearchProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    query: str
    results: list[NormalizedSearchResult] = Field(max_length=20)


class SearchProvider(Protocol):
    async def search(self, *, query: str, max_results: int) -> SearchProviderResponse:
        """Search the public web and return only Kairos-normalized result metadata."""


class SearchProviderError(RuntimeError):
    """A safe, classified Search Provider failure with no raw response or secret."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = code
        self.message = message[:500]
        self.retryable = retryable
        super().__init__(f"{code}: {self.message}")


def _bounded_result(item: dict[str, Any], rank: int) -> NormalizedSearchResult | None:
    url = item.get("url")
    if not isinstance(url, str) or not url.strip():
        return None
    title = item.get("title")
    snippet = item.get("content", item.get("snippet", ""))
    score = item.get("score")
    published_at = item.get("published_date", item.get("published_at"))
    return NormalizedSearchResult(
        url=url.strip(),
        title=title.strip()[:1000] if isinstance(title, str) else "",
        snippet=snippet.strip()[:500] if isinstance(snippet, str) else "",
        rank=rank,
        provider_score=score if isinstance(score, (int, float)) else None,
        published_at=published_at if isinstance(published_at, str) else None,
    )


class TavilySearchProvider:
    provider_name = "tavily"
    default_base_url = "https://api.tavily.com"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = default_base_url,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        max_attempts: int = 3,
    ) -> None:
        if not api_key:
            raise SearchProviderError("SEARCH_PROVIDER_AUTH_FAILED", "search provider credential is missing")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                base_url=self._base_url,
                timeout=get_settings().http_timeout_seconds,
            )
        )
        self._max_attempts = max(1, min(max_attempts, 3))

    async def search(self, *, query: str, max_results: int) -> SearchProviderResponse:
        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise SearchProviderError("SEARCH_QUERY_INVALID", "search query must not be empty")
        if max_results < 1 or max_results > 20:
            raise SearchProviderError("SEARCH_RESULT_LIMIT", "max_results must be between 1 and 20")

        payload = {
            "api_key": self._api_key,
            "query": normalized_query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }
        for attempt in range(self._max_attempts):
            try:
                async with self._client_factory() as client:
                    response = await client.post("/search", json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt + 1 == self._max_attempts:
                    raise SearchProviderError(
                        "SEARCH_PROVIDER_NETWORK_ERROR",
                        "search provider network request failed",
                        retryable=True,
                    ) from exc
                await asyncio.sleep(0.05 * (attempt + 1))
                continue

            if response.status_code in {401, 403}:
                raise SearchProviderError(
                    "SEARCH_PROVIDER_AUTH_FAILED", "search provider authentication failed"
                )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 == self._max_attempts:
                    raise SearchProviderError(
                        "SEARCH_PROVIDER_UNAVAILABLE",
                        "search provider temporarily unavailable",
                        retryable=True,
                    )
                await asyncio.sleep(0.05 * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise SearchProviderError("SEARCH_PROVIDER_FAILED", "search provider request failed")

            try:
                raw_results = response.json().get("results", [])
            except (ValueError, AttributeError) as exc:
                raise SearchProviderError(
                    "SEARCH_PROVIDER_INVALID_RESPONSE", "search provider response was invalid"
                ) from exc
            if not isinstance(raw_results, list):
                raise SearchProviderError(
                    "SEARCH_PROVIDER_INVALID_RESPONSE", "search provider response was invalid"
                )
            results = [
                normalized
                for index, item in enumerate(raw_results[:max_results], start=1)
                if isinstance(item, dict)
                for normalized in [_bounded_result(item, index)]
                if normalized is not None
            ]
            return SearchProviderResponse(
                provider=self.provider_name, query=normalized_query, results=results
            )

        raise SearchProviderError(
            "SEARCH_PROVIDER_UNAVAILABLE", "search provider temporarily unavailable", retryable=True
        )


def resolve_search_provider(config_id: str | None = None) -> SearchProvider:
    """Resolve a provider from worker configuration without exposing its credential to callers."""
    del config_id  # Persistence-backed provider configuration is intentionally out of this phase's scope.
    settings = get_settings()
    provider_name = os.getenv("KAIROS_SEARCH_PROVIDER", settings.search_provider_type).strip().lower()
    if provider_name != "tavily":
        raise SearchProviderError(
            "SEARCH_PROVIDER_NOT_CONFIGURED", "configured search provider is unavailable"
        )
    credential = os.getenv(settings.search_provider_credential_env)
    if not credential:
        raise SearchProviderError(
            "SEARCH_PROVIDER_CREDENTIAL_REQUIRED",
            "search provider credential is required in the worker environment",
        )
    return TavilySearchProvider(
        api_key=credential,
        base_url=settings.search_provider_base_url,
    )
