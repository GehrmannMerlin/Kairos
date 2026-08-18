"""Bocha Web Search provider adapter (M-03 / D-069).

官方协议（Web Search API）：
  POST https://api.bochaai.com/v1/web-search
  Authorization: Bearer <BOCHA_API_KEY>
  Content-Type: application/json
  Body: {"query": "...", "count": 3}
  Response: {"code": "0", "data": {"webPages": {"value": [{"url","name","snippet"}]}}}
            （兼容 webPages 直接位于顶层的情况）

Kairos 内部仍输出 canonical SearchResult（url/title/snippet/provider/rank/query），
不把 Bocha raw response 传播到 Domain 层。只做 SourceSearch，不调用 Bocha
ai-search（AI 问答搜索）。
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

from app.providers.adapters.openai_compatible import (
    _parse_retry_after,
    map_status,
    raise_for_search_status,
)
from app.providers.errors import (
    ProviderAuthFailedError,
    ProviderError,
    ProviderRateLimitedError,
)
from app.providers.protocol import (
    BaseUrlMode,
    ProviderDefinition,
    ProviderTestResult,
    ProviderTestStatus,
)
from app.providers.search_protocol import SearchResult
from app.providers.transport import HttpClient, HttpxTransport

_DEFAULT_BASE_URL = "https://api.bochaai.com"
_MAX_COUNT = 50  # Bocha 官方 count 上限 1-50


def _raise_for_body_code(body: Any) -> None:
    """Bocha 把业务错误放在 HTTP 200 + JSON body ``code`` 字段（如 429 限流）。

    HTTP 状态检查无法发现这类错误，必须按 body.code 分类，否则限流会被吞成
    「0 结果 → NO_MATCHING_PAGES」。成功码同时兼容 200（实测）与 "0"（官方文档）。
    """
    if not isinstance(body, dict):
        return
    code = body.get("code")
    if code is None or code in (0, "0", 200, "200"):
        return
    try:
        code_int = int(code)
    except (TypeError, ValueError):
        return
    if code_int in (401, 403):
        raise ProviderAuthFailedError("搜索服务认证失败")
    if code_int == 429:
        raise ProviderRateLimitedError("搜索服务限流")
    raise ProviderError(f"搜索服务返回错误 code={code}")


def _extract_web_pages(body: Any) -> list[dict]:
    """从 Bocha 响应取 webPages.value[]（兼容顶层与 data 包裹两种 shape）。"""
    if not isinstance(body, dict):
        return []
    web_pages = body.get("webPages")
    if not isinstance(web_pages, dict) and isinstance(body.get("data"), dict):
        web_pages = body.get("data", {}).get("webPages")
    if not isinstance(web_pages, dict):
        return []
    value = web_pages.get("value") or []
    return [item for item in value if isinstance(item, dict)]


class BochaSearchProvider:
    definition = ProviderDefinition(
        provider_type="bocha",
        display_name="Bocha",
        requires_api_key=True,
        requires_model_name=False,
        requires_base_url=False,
        default_base_url=_DEFAULT_BASE_URL,
        protocol_family="bocha",
        base_url_mode=BaseUrlMode.MANAGED,
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
                body={"query": "kairos", "count": 1},
                timeout_seconds=15.0,
            )
        except Exception:
            return ProviderTestResult(
                status=ProviderTestStatus.NETWORK_ERROR,
                error_code="NETWORK_ERROR",
                latency_ms=int((perf_counter() - started) * 1000),
            )
        status, code = map_status(resp.status_code, model_specific_404=False)
        if status is ProviderTestStatus.AVAILABLE:
            if not isinstance(resp.body, dict):
                status, code = ProviderTestStatus.FAILED, "INVALID_RESPONSE"
            else:
                body_code = resp.body.get("code")
                if body_code is not None and body_code not in (0, "0", 200, "200"):
                    try:
                        body_code_int = int(body_code)
                    except (TypeError, ValueError):
                        body_code_int = None
                    if body_code_int in (401, 403):
                        status, code = ProviderTestStatus.AUTH_FAILED, "BODY_401"
                    elif body_code_int == 429:
                        status, code = ProviderTestStatus.RATE_LIMITED, "BODY_429"
                    else:
                        status, code = ProviderTestStatus.FAILED, f"BODY_{body_code}"
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
            body={"query": query, "count": max(1, min(limit, _MAX_COUNT))},
            timeout_seconds=15.0,
        )
        raise_for_search_status(resp.status_code, retry_after_seconds=_parse_retry_after(resp))
        _raise_for_body_code(resp.body)
        out: list[SearchResult] = []
        for idx, item in enumerate(_extract_web_pages(resp.body), start=1):
            # 单条缺 title/snippet 不影响保留合法 URL；url 缺失由下游 merge 丢弃。
            out.append(
                SearchResult(
                    url=item.get("url") or "",
                    title=item.get("name") or item.get("title") or "",
                    snippet=item.get("snippet") or item.get("summary") or "",
                    provider=self.definition.provider_type,
                    rank=idx,
                    query=query,
                )
            )
        return out

    @staticmethod
    def _endpoint(base_url: str | None) -> str:
        root = base_url.rstrip("/") if base_url else _DEFAULT_BASE_URL
        return f"{root}/v1/web-search"

    @staticmethod
    def _headers(api_key: str | None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key or ''}",
            "Content-Type": "application/json",
        }


__all__ = ["BochaSearchProvider"]
