"""Tavily search provider adapter（DEPLOY-GATE-3 最小兼容：官方 Tavily API）。

官方协议（DEPLOY-GATE-3 需求六）：
  POST https://api.tavily.com/search
  Authorization: Bearer <TAVILY_API_KEY>
  Content-Type: application/json
  Body: {"query": "...", "search_depth": "basic", "max_results": 5}
  Response: {"results": [{"title", "url", "content", "score"}]}

Kairos 内部仍输出 canonical SearchResult（url/title/snippet/provider/rank/query），
不把 Tavily raw response 传播到 Domain 层（需求十）。只做 SourceSearch，不调用
Tavily Extract/Crawl/Map（需求十一）。
"""

from __future__ import annotations

from time import perf_counter

from app.providers.adapters.openai_compatible import map_status
from app.providers.protocol import ProviderDefinition, ProviderTestResult, ProviderTestStatus
from app.providers.search_protocol import SearchResult
from app.providers.transport import HttpClient, HttpxTransport

_DEFAULT_BASE_URL = "https://api.tavily.com"


class TavilySearchProvider:
    definition = ProviderDefinition(
        provider_type="tavily",
        display_name="Tavily",
        requires_api_key=True,
        requires_model_name=False,
        requires_base_url=False,
        default_base_url=_DEFAULT_BASE_URL,
        protocol_family="tavily",
    )

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpxTransport()

    async def test_connection(
        self, *, api_key: str | None, base_url: str | None
    ) -> ProviderTestResult:
        endpoint = self._endpoint(base_url)
        started = perf_counter()
        try:
            resp = await self._http.request(
                method="POST",
                url=endpoint,
                headers=self._headers(api_key),
                params=None,
                body={"query": "kairos", "search_depth": "basic", "max_results": 1},
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
        endpoint = self._endpoint(base_url)
        resp = await self._http.request(
            method="POST",
            url=endpoint,
            headers=self._headers(api_key),
            params=None,
            body={"query": query, "search_depth": "basic", "max_results": min(limit, 5)},
            timeout_seconds=15.0,
        )
        body = resp.body if isinstance(resp.body, dict) else {}
        raw = body.get("results") or []
        out: list[SearchResult] = []
        for idx, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                continue
            out.append(
                SearchResult(
                    url=item.get("url", ""),
                    title=item.get("title", ""),
                    snippet=item.get("content") or item.get("snippet") or "",
                    provider=self.definition.provider_type,
                    rank=idx,
                    query=query,
                )
            )
        return out

    @staticmethod
    def _endpoint(base_url: str | None) -> str:
        root = base_url.rstrip("/") if base_url else _DEFAULT_BASE_URL
        return f"{root}/search"

    @staticmethod
    def _headers(api_key: str | None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key or ''}",
            "Content-Type": "application/json",
        }


__all__ = ["TavilySearchProvider"]
