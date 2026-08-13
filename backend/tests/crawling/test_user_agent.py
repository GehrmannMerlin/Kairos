"""DEPLOY-GATE-3 回归：HTTP transport 必须携带全站统一 User-Agent。

shanghai.gov.cn WAF 对 python-httpx 默认 UA 返回 403，对 KairosBot 返回 200。
默认 UA 必须同时应用到 discovery 与 fetch 两个 httpx transport，且显式传入的
headers（凭据等）不覆盖、保留。
"""

from __future__ import annotations

import httpx
import pytest
from app.crawling.http_fetch import DEFAULT_USER_AGENT as FETCH_UA
from app.crawling.http_fetch import _HttpxFetchTransport
from app.discovery.http import DEFAULT_USER_AGENT, _HttpxDiscoveryTransport


class _FakeResponse:
    status_code = 200
    headers = {}
    text = "ok"
    content = b"ok"


class _FakeClient:
    captured_headers: dict | None = None

    def __init__(self, **kw) -> None:
        self.kw = kw

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a) -> bool:
        return False

    async def request(self, method: str, url: str):  # noqa: ARG002
        _FakeClient.captured_headers = self.kw.get("headers")
        return _FakeResponse()


@pytest.fixture()
def _patch_httpx(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    _FakeClient.captured_headers = None


def test_default_user_agent_contract() -> None:
    """单一 canonical UA：http.py 定义，robots/fetch 复用同一常量。"""
    from app.discovery.robots import DEFAULT_USER_AGENT as RUA

    assert DEFAULT_USER_AGENT == "KairosBot"
    assert RUA == DEFAULT_USER_AGENT
    assert FETCH_UA == DEFAULT_USER_AGENT


@pytest.mark.asyncio
async def test_discovery_transport_sends_canonical_ua(_patch_httpx) -> None:
    transport = _HttpxDiscoveryTransport()
    await transport.request(method="GET", url="https://example.com/x", timeout_seconds=10)
    assert _FakeClient.captured_headers == {"User-Agent": "KairosBot"}


@pytest.mark.asyncio
async def test_fetch_transport_defaults_ua_and_preserves_explicit_headers(_patch_httpx) -> None:
    transport = _HttpxFetchTransport()
    # 无 headers → 默认 KairosBot
    await transport.request(
        method="GET", url="https://example.com/x", timeout_seconds=10, headers=None
    )
    assert _FakeClient.captured_headers == {"User-Agent": "KairosBot"}
    # 显式 UA 优先
    await transport.request(
        method="GET",
        url="https://example.com/x",
        timeout_seconds=10,
        headers={"User-Agent": "CustomBot"},
    )
    assert _FakeClient.captured_headers == {"User-Agent": "CustomBot"}
    # 凭据 headers 保留，UA 补充默认
    await transport.request(
        method="GET",
        url="https://example.com/x",
        timeout_seconds=10,
        headers={"Authorization": "Bearer x"},
    )
    assert _FakeClient.captured_headers == {
        "User-Agent": "KairosBot",
        "Authorization": "Bearer x",
    }
