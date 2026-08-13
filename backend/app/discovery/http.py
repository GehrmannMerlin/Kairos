"""SSRF 保护的最小发现 HTTP 传输（M-09）。

仅用于 robots.txt / sitemap XML / RSS / Atom / seed HTML 的轻量读取与 HEAD
metadata。每次请求前 + 每跳重定向都重新执行 SSRF 校验；有界重定向不自动跟随。
不写 PageSnapshot、不执行 JS、不保存正文（M-10 负责完整 Fetch）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.discovery.errors import DiscoveryValidationError
from app.discovery.ssrf import assert_safe_url

_MAX_REDIRECTS = 5
_REDIRECT_STATUS = (301, 302, 303, 307, 308)

# 全站统一 User-Agent：robots 策略匹配与实际 HTTP 请求使用同一 UA，避免默认
# ``python-httpx`` UA 被站点 WAF/反爬识别拦截（DEPLOY-GATE-3：shanghai.gov.cn 对
# python-httpx UA 返回 403，对 KairosBot 返回 200）。
DEFAULT_USER_AGENT = "KairosBot"


@dataclass
class DiscoveryTextResponse:
    text: str
    status_code: int
    final_url: str
    content_type: str | None = None


@dataclass
class DiscoveryHeadResponse:
    status_code: int
    final_url: str


@dataclass
class _HttpResponse:
    status_code: int
    headers: dict[str, str]
    text: str


class DiscoveryTransport(Protocol):
    async def request(self, *, method: str, url: str, timeout_seconds: float) -> _HttpResponse: ...


class _HttpxDiscoveryTransport:
    async def request(self, *, method: str, url: str, timeout_seconds: float) -> _HttpResponse:
        import httpx

        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        ) as client:
            resp = await client.request(method, url)
            return _HttpResponse(
                status_code=resp.status_code,
                headers=dict(resp.headers),
                text=resp.text,
            )


class DiscoveryHttp:
    """SSRF 保护的最小发现 HTTP：get_text / head。

    本地 fixture 测试通过显式 allow_hosts 绕过 SSRF；Production 保持空 allow_hosts。
    """

    def __init__(
        self,
        transport: DiscoveryTransport | None = None,
        *,
        allow_hosts: frozenset[str] = frozenset(),
    ) -> None:
        self._transport: DiscoveryTransport = transport or _HttpxDiscoveryTransport()
        self._allow_hosts = allow_hosts

    async def _hop(self, method: str, url: str, timeout_seconds: float) -> _HttpResponse:
        assert_safe_url(url, allow_hosts=self._allow_hosts)
        resp = await self._transport.request(
            method=method, url=url, timeout_seconds=timeout_seconds
        )
        return resp

    def _location(self, resp: _HttpResponse, current: str) -> str | None:
        loc = (resp.headers or {}).get("location")
        if resp.status_code in _REDIRECT_STATUS and loc:
            from urllib.parse import urljoin

            return urljoin(current, loc)
        return None

    async def get_text(self, url: str, timeout_seconds: float = 20.0) -> DiscoveryTextResponse:
        current = url
        for _ in range(_MAX_REDIRECTS):
            resp = await self._hop("GET", current, timeout_seconds)
            next_url = self._location(resp, current)
            if next_url is not None:
                current = next_url
                continue
            headers = resp.headers or {}
            return DiscoveryTextResponse(
                text=resp.text,
                status_code=resp.status_code,
                final_url=current,
                content_type=headers.get("content-type"),
            )
        raise DiscoveryValidationError("重定向次数超限")

    async def head(self, url: str, timeout_seconds: float = 15.0) -> DiscoveryHeadResponse:
        current = url
        for _ in range(_MAX_REDIRECTS):
            resp = await self._hop("HEAD", current, timeout_seconds)
            next_url = self._location(resp, current)
            if next_url is not None:
                current = next_url
                continue
            return DiscoveryHeadResponse(status_code=resp.status_code, final_url=current)
        raise DiscoveryValidationError("重定向次数超限")
