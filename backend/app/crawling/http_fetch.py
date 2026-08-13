"""安全 HTTP Fetch transport（M-10 / 十六）。

复用 M-09 的 SSRF 守卫（app.discovery.ssrf.assert_safe_url）与“每跳重定向重新校验”
的安全模型，不建立第二套 HTTP client；只扩展真 Fetch 所需能力：完整 body、
size cap、timing、redirect chain、header allowlist。响应头只记录 allowlist 摘要
（绝不含 Set-Cookie/Authorization/Cookie/secret）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin

from app.crawling.contracts import redact_headers
from app.crawling.errors import FetchErrorCode, HttpFetchError
from app.discovery.errors import DiscoveryError
from app.discovery.http import DEFAULT_USER_AGENT
from app.discovery.ssrf import assert_safe_url

_REDIRECT_STATUS = (301, 302, 303, 307, 308)


@dataclass
class _RawResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes


class FetchTransport(Protocol):
    async def request(
        self,
        *,
        method: str,
        url: str,
        timeout_seconds: float,
        headers: dict[str, str] | None,
    ) -> _RawResponse: ...


class _HttpxFetchTransport:
    async def request(
        self,
        *,
        method: str,
        url: str,
        timeout_seconds: float,
        headers: dict[str, str] | None,
    ) -> _RawResponse:
        import httpx

        # 默认带全站统一 UA（显式传入的 headers 不覆盖）；避免 python-httpx 默认
        # UA 被站点 WAF/反爬拦截（DEPLOY-GATE-3：shanghai.gov.cn 403）。
        merged = dict(headers or {})
        merged.setdefault("User-Agent", DEFAULT_USER_AGENT)
        async with httpx.AsyncClient(
            timeout=timeout_seconds, follow_redirects=False, headers=merged
        ) as client:
            resp = await client.request(method, url)
            return _RawResponse(
                status_code=resp.status_code,
                headers=dict(resp.headers),
                content=resp.content,
            )


@dataclass
class FetchedBody:
    status_code: int
    body: bytes
    final_url: str
    content_type: str | None
    headers_allowlist: dict[str, str]
    redirect_chain: list[dict] = field(default_factory=list)
    duration_ms: int = 0


class SafeFetchHttp:
    """完整 Fetch transport：SSRF 守卫 + 有界重定向逐跳复验 + size cap + header 脱敏。

    本地 fixture 测试通过显式 allow_hosts 绕过 SSRF；Production 默认空（M-09 语义）。
    """

    def __init__(
        self,
        transport: FetchTransport | None = None,
        *,
        allow_hosts: frozenset[str] = frozenset(),
        max_redirects: int = 5,
        max_bytes: int = 5_000_000,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._transport: FetchTransport = transport or _HttpxFetchTransport()
        self._allow_hosts = allow_hosts
        self._max_redirects = max_redirects
        self._max_bytes = max_bytes
        self._timeout_seconds = timeout_seconds

    async def _hop(
        self, method: str, url: str, headers: dict[str, str] | None
    ) -> _RawResponse:
        # 每跳重定向重新 SSRF/scheme 校验（十五）：不能安全 URL → 127.0.0.1 后继续
        assert_safe_url(url, allow_hosts=self._allow_hosts)
        return await self._transport.request(
            method=method,
            url=url,
            timeout_seconds=self._timeout_seconds,
            headers=headers,
        )

    def _location(self, resp: _RawResponse, current: str) -> str | None:
        loc = (resp.headers or {}).get("location")
        if resp.status_code in _REDIRECT_STATUS and loc:
            return urljoin(current, loc)
        return None

    async def get_bytes(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> FetchedBody:
        current = url
        chain: list[dict] = []
        for _ in range(self._max_redirects):
            resp = await self._hop("GET", current, headers)
            next_url = self._location(resp, current)
            if next_url is not None:
                chain.append({"from": current, "to": next_url, "status": resp.status_code})
                current = next_url
                continue
            body = resp.content or b""
            if len(body) > self._max_bytes:
                raise HttpFetchError(
                    FetchErrorCode.SIZE_LIMIT_EXCEEDED,
                    f"下载内容超过大小上限 {self._max_bytes} bytes",
                )
            h = resp.headers or {}
            return FetchedBody(
                status_code=resp.status_code,
                body=body,
                final_url=current,
                content_type=h.get("content-type"),
                headers_allowlist=redact_headers(h),
                redirect_chain=chain,
            )
        raise HttpFetchError(FetchErrorCode.TOO_MANY_REDIRECTS, "重定向次数超限")


def map_transport_error(exc: Exception) -> HttpFetchError:
    """把 transport/网络异常映射为 canonical FetchErrorCode（十七）。"""
    code = FetchErrorCode.INTERNAL_ERROR
    import socket
    import ssl

    if isinstance(exc, HttpFetchError):
        return exc
    if isinstance(exc, DiscoveryError):  # SSRF 拦截
        return HttpFetchError(FetchErrorCode.SSRF_BLOCKED, str(exc))
    if isinstance(exc, (TimeoutError,)):
        return HttpFetchError(FetchErrorCode.TIMEOUT, str(exc))
    if isinstance(exc, socket.gaierror):
        return HttpFetchError(FetchErrorCode.DNS_ERROR, str(exc))
    if isinstance(exc, (ConnectionError, ssl.SSLError)):
        return HttpFetchError(FetchErrorCode.CONNECTION_ERROR, str(exc))
    return HttpFetchError(code, str(exc))
