"""Search provider contract (M-03). Independent from ModelProvider.

The compatible HTTP contract (CUSTOM_COMPATIBLE_SEARCH):
  GET {base_url}/search?q=<query>&limit=<n>
  Authorization: Bearer <api_key>
  Response: {"results": [{"url", "title", "snippet"}]}
  Error mapping: 200->AVAILABLE; 401/403->AUTH_FAILED; 404->NETWORK_ERROR;
                 429->RATE_LIMITED; transport error->NETWORK_ERROR.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.providers.protocol import ProviderDefinition, ProviderTestResult


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    snippet: str
    provider: str
    rank: int | None
    query: str


class SearchProvider(Protocol):
    definition: ProviderDefinition

    async def test_connection(
        self, *, api_key: str | None, base_url: str | None
    ) -> ProviderTestResult: ...

    async def search(
        self, *, query: str, limit: int, api_key: str | None, base_url: str | None
    ) -> list[SearchResult]: ...
