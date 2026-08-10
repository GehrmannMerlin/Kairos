"""Minimal HTTP transport protocol + httpx implementation.

Adapters depend on this protocol, never on httpx directly, so connection-test
unit tests inject a fake transport (no real API keys, no network).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: Any = None
    text: str = ""


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
    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self._timeout = timeout_seconds

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
        async with httpx.AsyncClient(timeout=timeout_seconds or self._timeout) as client:
            resp = await client.request(method, url, headers=headers, params=params, json=body)
            try:
                resp_body = resp.json()
            except Exception:
                resp_body = None
            return HttpResponse(status_code=resp.status_code, body=resp_body, text=resp.text)
