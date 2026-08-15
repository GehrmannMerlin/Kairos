"""Minimal HTTP transport protocol + httpx implementation.

Adapters depend on this protocol, never on httpx directly, so connection-test
unit tests inject a fake transport (no real API keys, no network).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.providers import errors

_SAFE_RESPONSE_HEADERS = frozenset({"retry-after", "x-request-id", "request-id"})


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: Any = None
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)


class HttpClient(Protocol):
    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None,
        params: dict[str, str] | None,
        timeout_seconds: float,
        body: dict | None = None,
    ) -> HttpResponse: ...


class HttpxTransport:
    def __init__(
        self,
        timeout_seconds: float = 15.0,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._transport = transport

    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None,
        params: dict[str, str] | None,
        timeout_seconds: float | None = None,
        body: dict | None = None,
    ) -> HttpResponse:
        async with httpx.AsyncClient(
            timeout=timeout_seconds or self._timeout,
            transport=self._transport,
        ) as client:
            try:
                resp = await client.request(method, url, headers=headers, params=params, json=body)
            except httpx.ConnectTimeout as exc:
                raise errors.ProviderTimeoutError(phase=errors.TimeoutPhase.CONNECT) from exc
            except httpx.ReadTimeout as exc:
                raise errors.ProviderTimeoutError(phase=errors.TimeoutPhase.READ) from exc
            except httpx.ConnectError as exc:
                raise errors.ProviderNetworkError("Provider connection failed") from exc
            try:
                resp_body = resp.json()
            except Exception:
                resp_body = None
            safe_headers = {
                name.lower(): value
                for name, value in resp.headers.items()
                if name.lower() in _SAFE_RESPONSE_HEADERS
            }
            return HttpResponse(
                status_code=resp.status_code,
                body=resp_body,
                text=resp.text,
                headers=safe_headers,
            )
