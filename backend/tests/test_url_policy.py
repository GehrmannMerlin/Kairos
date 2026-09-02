from __future__ import annotations

import socket

import httpx
import pytest
from app.domain import CollectionError
from app.url_policy import (
    RetryableFetchError,
    RobotsPolicy,
    canonicalize_url,
    request_with_safe_redirects,
    validate_public_http_url,
)


def test_canonicalize_url_normalizes_host_fragment_and_default_port() -> None:
    assert canonicalize_url("HTTPS://Example.COM:443/path?q=1#section") == "https://example.com/path?q=1"
    assert canonicalize_url("http://Example.COM:80/path") == "http://example.com/path"


def test_validate_public_http_url_rejects_unsupported_schemes_and_localhost() -> None:
    with pytest.raises(CollectionError, match="INVALID_SOURCE_URL"):
        validate_public_http_url("file:///etc/passwd")
    with pytest.raises(CollectionError, match="PRIVATE_ADDRESS_BLOCKED"):
        validate_public_http_url("http://localhost/private")


def test_validate_public_http_url_rejects_private_dns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        del args, kwargs
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", 80))]

    monkeypatch.setattr("app.url_policy.socket.getaddrinfo", fake_getaddrinfo)

    with pytest.raises(CollectionError, match="PRIVATE_ADDRESS_BLOCKED"):
        validate_public_http_url("https://public.example/private")


def test_redirect_target_is_revalidated_by_the_same_public_url_policy() -> None:
    with pytest.raises(CollectionError, match="PRIVATE_ADDRESS_BLOCKED"):
        validate_public_http_url("http://127.0.0.1:8080/internal")


@pytest.mark.asyncio
async def test_safe_redirect_rejects_public_to_loopback_before_second_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        del args, kwargs
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr("app.url_policy.socket.getaddrinfo", fake_getaddrinfo)
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8080/internal"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        with pytest.raises(CollectionError, match="PRIVATE_ADDRESS_BLOCKED"):
            await request_with_safe_redirects(client, "https://public.example/page")

    assert requests == ["https://public.example/page"]


@pytest.mark.asyncio
async def test_robots_policy_treats_not_found_as_allowed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    policy = RobotsPolicy(client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    await policy.check("https://example.com/page")


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_robots_policy_blocks_unauthorized_robots(status_code: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, request=request)

    policy = RobotsPolicy(client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with pytest.raises(CollectionError, match="ROBOTS_BLOCKED"):
        await policy.check("https://example.com/page")


@pytest.mark.asyncio
async def test_robots_policy_blocks_explicit_disallow() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="User-agent: *\nDisallow: /private\n", request=request)

    policy = RobotsPolicy(client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with pytest.raises(CollectionError, match="ROBOTS_BLOCKED"):
        await policy.check("https://example.com/private")


@pytest.mark.asyncio
async def test_robots_policy_retries_server_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    policy = RobotsPolicy(client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with pytest.raises(RetryableFetchError):
        await policy.check("https://example.com/page")
