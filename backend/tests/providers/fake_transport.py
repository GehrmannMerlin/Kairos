"""Fake HttpClient for provider tests (no real network / keys)."""

from __future__ import annotations

from app.providers import errors
from app.providers.transport import HttpResponse


class FakeHttpClient:
    def __init__(self, status_code: int = 200, body=None, *, raise_network: bool = False) -> None:
        self._status = status_code
        self._body = body
        self._raise_network = raise_network
        self.calls: list[dict] = []

    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout_seconds: float = 15.0,
        body: dict | None = None,
    ) -> HttpResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
                "timeout_seconds": timeout_seconds,
                "body": body,
            }
        )
        if self._raise_network:
            raise errors.ProviderNetworkError("boom")
        return HttpResponse(status_code=self._status, body=self._body)
