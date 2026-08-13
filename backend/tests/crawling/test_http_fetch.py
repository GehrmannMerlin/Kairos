"""SafeFetchHttp 单元测试：SSRF 重定向复验 / header 脱敏 / size cap。"""
from __future__ import annotations

import pytest
from app.crawling.errors import FetchErrorCode, HttpFetchError
from app.crawling.http_fetch import SafeFetchHttp
from app.discovery.ssrf import SSRFBlockedError
from tests.crawling.conftest import SITE_HOST, FakeFetchTransport


def _transport(routes) -> FakeFetchTransport:
    return FakeFetchTransport(routes)


@pytest.mark.asyncio
async def test_redirect_revalidates_ssrf() -> None:
    # 安全 URL → 302 → 127.0.0.1：第二跳 SSRF 复验必须拦截（不能安全→私网继续）
    transport = _transport(
        {
            "/": {
                "status": 302,
                "headers": {"location": "http://127.0.0.1/x"},
                "body": b"",
            }
        }
    )
    http = SafeFetchHttp(transport=transport, allow_hosts=frozenset({SITE_HOST}))
    with pytest.raises((SSRFBlockedError, HttpFetchError)):
        await http.get_bytes(f"http://{SITE_HOST}/")


@pytest.mark.asyncio
async def test_header_allowlist_redacts_secrets() -> None:
    transport = _transport(
        {
            "/": {
                "status": 200,
                "headers": {
                    "set-cookie": "session=SECRET",
                    "authorization": "Bearer SECRET",
                    "content-type": "text/html",
                    "etag": '"abc"',
                },
                "body": b"<html>ok</html>",
            }
        }
    )
    http = SafeFetchHttp(transport=transport, allow_hosts=frozenset({SITE_HOST}))
    body = await http.get_bytes(f"http://{SITE_HOST}/")
    assert "set-cookie" not in body.headers_allowlist
    assert "authorization" not in body.headers_allowlist
    assert body.headers_allowlist.get("content-type") == "text/html"
    assert body.headers_allowlist.get("etag") == '"abc"'


@pytest.mark.asyncio
async def test_size_cap_enforced() -> None:
    transport = _transport(
        {"/": {"status": 200, "body": b"x" * 100}}
    )
    http = SafeFetchHttp(transport=transport, allow_hosts=frozenset({SITE_HOST}), max_bytes=10)
    with pytest.raises(HttpFetchError) as exc:
        await http.get_bytes(f"http://{SITE_HOST}/")
    assert exc.value.code == FetchErrorCode.SIZE_LIMIT_EXCEEDED
