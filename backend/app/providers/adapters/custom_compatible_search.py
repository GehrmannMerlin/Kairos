"""Minimal pluggable compatible search adapter (M-03 scope only).

Implements the documented compatible contract so M-09 can build SourceSearch
orchestration on top; M-03 does not implement search strategy / frontier /
robots.
"""

from __future__ import annotations

from time import perf_counter

from app.providers.adapters.openai_compatible import map_status
from app.providers.protocol import (
    ProviderDefinition,
    ProviderTestResult,
    ProviderTestStatus,
)
from app.providers.search_protocol import SearchResult
from app.providers.transport import HttpClient, HttpxTransport


class CustomCompatibleSearchProvider:
    definition = ProviderDefinition(
        provider_type="custom_compatible_search",
        display_name="Custom Compatible Search",
        requires_api_key=True,
        requires_model_name=False,
        requires_base_url=True,
        default_base_url=None,
        protocol_family="compatible_search",
    )

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpxTransport()

    async def test_connection(
        self, *, api_key: str | None, base_url: str | None
    ) -> ProviderTestResult:
        endpoint = f"{base_url.rstrip('/')}/search" if base_url else ""
        started = perf_counter()
        try:
            resp = await self._http.request(
                method="GET",
                url=endpoint,
                headers={"Authorization": f"Bearer {api_key or ''}"},
                params={"q": "kairos", "limit": "1"},
                timeout_seconds=15.0,
            )
        except Exception:
            return ProviderTestResult(
                status=ProviderTestStatus.NETWORK_ERROR,
                error_code="NETWORK_ERROR",
                latency_ms=int((perf_counter() - started) * 1000),
            )
        status, code = map_status(resp.status_code, model_specific_404=False)
        return ProviderTestResult(
            status=status, error_code=code, latency_ms=int((perf_counter() - started) * 1000)
        )

    async def search(
        self, *, query: str, limit: int, api_key: str | None, base_url: str | None
    ) -> list[SearchResult]:
        endpoint = f"{base_url.rstrip('/')}/search" if base_url else ""
        resp = await self._http.request(
            method="GET",
            url=endpoint,
            headers={"Authorization": f"Bearer {api_key or ''}"},
            params={"q": query, "limit": str(limit)},
            timeout_seconds=15.0,
        )
        body = resp.body if isinstance(resp.body, dict) else {}
        raw = body.get("results") or []
        out: list[SearchResult] = []
        for idx, item in enumerate(raw, start=1):
            out.append(
                SearchResult(
                    url=item["url"],
                    title=item.get("title", ""),
                    snippet=item.get("snippet", ""),
                    provider=self.definition.provider_type,
                    rank=idx,
                    query=query,
                )
            )
        return out
