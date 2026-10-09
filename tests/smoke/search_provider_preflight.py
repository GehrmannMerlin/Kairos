"""Probe the configured real SearchProvider without printing credentials or raw results."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_APP_ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.search import SearchProviderError, resolve_search_provider  # noqa: E402


async def _run() -> int:
    settings = get_settings()
    if not os.getenv(settings.search_provider_credential_env):
        print("BLOCKED: real search-provider credential is missing")
        return 2
    try:
        provider = resolve_search_provider()
        response = await provider.search(
            query="Python web frameworks official documentation",
            max_results=3,
        )
    except SearchProviderError as exc:
        print(f"BLOCKED: {exc.code}: {exc.message}")
        return 2
    except Exception as exc:  # noqa: BLE001 - preflight must not print provider response or secrets
        print(f"BLOCKED: {type(exc).__name__}")
        return 2
    if not response.results or not all(
        item.url.startswith(("http://", "https://")) for item in response.results
    ):
        print("BLOCKED: provider returned no valid HTTP/HTTPS result")
        return 2
    print(f"provider={response.provider} results={len(response.results)} PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
