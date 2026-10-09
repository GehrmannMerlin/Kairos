from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.domain import CollectionError

KAIROS_USER_AGENT = "KairosBot/0.1"
MAX_REDIRECTS = 5
ROBOTS_MAX_BYTES = 256 * 1024


class RetryableFetchError(RuntimeError):
    """A network/server outcome that Temporal may retry."""


def _split_http_url(url: str) -> SplitResult:
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
    except ValueError as exc:
        raise CollectionError("INVALID_SOURCE_URL", "URL has an invalid host or port") from exc
    if scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        raise CollectionError("INVALID_SOURCE_URL", "only absolute http and https URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise CollectionError("INVALID_SOURCE_URL", "URL credentials are not allowed")
    return parsed


def canonicalize_url(url: str) -> str:
    parsed = _split_http_url(url.strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise CollectionError("INVALID_SOURCE_URL", "URL has an invalid host or port") from exc
    hostname = parsed.hostname
    assert hostname is not None
    scheme = parsed.scheme.lower()
    hostname = hostname.lower()
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def _is_non_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not address.is_global or address.is_private or address.is_loopback or address.is_reserved


def validate_public_http_url(
    url: str,
    *,
    resolver: object | None = None,
) -> str:
    canonical = canonicalize_url(url)
    parsed = urlsplit(canonical)
    hostname = parsed.hostname
    assert hostname is not None
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise CollectionError("PRIVATE_ADDRESS_BLOCKED", "localhost is not a public source")

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_non_public_address(literal):
            raise CollectionError("PRIVATE_ADDRESS_BLOCKED", "source address is not public")
        return canonical

    resolve = resolver or socket.getaddrinfo
    try:
        addresses = resolve(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except (OSError, socket.gaierror, TypeError) as exc:
        raise CollectionError("INVALID_SOURCE_URL", "source hostname could not be resolved") from exc
    if not addresses:
        raise CollectionError("INVALID_SOURCE_URL", "source hostname has no address")
    for entry in addresses:
        sockaddr = entry[4]
        address = ipaddress.ip_address(sockaddr[0])
        if _is_non_public_address(address):
            raise CollectionError("PRIVATE_ADDRESS_BLOCKED", "source resolves to a non-public address")
    return canonical


async def request_with_safe_redirects(
    client: httpx.AsyncClient,
    url: str,
) -> tuple[httpx.Response, str]:
    current = validate_public_http_url(url)
    for redirect_count in range(MAX_REDIRECTS + 1):
        response = await client.get(current, follow_redirects=False)
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response, current
        location = response.headers.get("location")
        if not location:
            return response, current
        if redirect_count == MAX_REDIRECTS:
            raise CollectionError("REDIRECT_LIMIT", "source exceeded the redirect limit")
        current = validate_public_http_url(urljoin(current, location))
    raise CollectionError("REDIRECT_LIMIT", "source exceeded the redirect limit")


@dataclass(slots=True)
class RobotsPolicy:
    timeout_seconds: float = 10.0
    client_factory: Callable[[], httpx.AsyncClient] | None = None

    async def check(self, url: str) -> None:
        canonical = validate_public_http_url(url)
        parsed = urlsplit(canonical)
        robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
        try:
            client = (
                self.client_factory()
                if self.client_factory is not None
                else httpx.AsyncClient(
                    follow_redirects=False,
                    timeout=self.timeout_seconds,
                    headers={"User-Agent": KAIROS_USER_AGENT},
                )
            )
            async with client:
                response, _ = await request_with_safe_redirects(client, robots_url)
                if response.status_code in {404, 410}:
                    return
                if response.status_code in {401, 403}:
                    raise CollectionError("ROBOTS_BLOCKED", "robots policy denied this source")
                if response.status_code >= 500:
                    raise RetryableFetchError(f"robots service returned {response.status_code}")
                if response.status_code < 200 or response.status_code >= 300:
                    raise CollectionError("ROBOTS_BLOCKED", "robots policy did not allow this source")
                body = response.content
                if len(body) > ROBOTS_MAX_BYTES:
                    raise RetryableFetchError("robots response exceeded the bounded limit")
                parser = RobotFileParser()
                parser.parse(body.decode("utf-8", errors="replace").splitlines())
                if not parser.can_fetch(KAIROS_USER_AGENT, canonical):
                    raise CollectionError("ROBOTS_BLOCKED", "robots policy denied this source")
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise RetryableFetchError(f"robots transport failed: {type(exc).__name__}") from exc
