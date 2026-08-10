"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# Unit tests must not spin up OTel background exporters to an unreachable collector.
os.environ.setdefault("KAIROS_OTEL_ENABLED", "false")


@pytest.fixture(autouse=True)
def _clear_process_caches() -> Iterator[None]:
    """Reset process-scoped singletons between tests to avoid env bleed."""
    from app.config import get_settings
    from app.infra import deps

    get_settings.cache_clear()
    deps.get_session_factory.cache_clear()
    deps.get_object_storage.cache_clear()
    yield


@pytest.fixture
def run_integration() -> bool:
    """True when integration tests against live local services are requested."""
    return os.environ.get("KAIROS_RUN_INTEGRATION", "0") == "1"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip integration-marked tests unless KAIROS_RUN_INTEGRATION=1."""
    if os.environ.get("KAIROS_RUN_INTEGRATION", "0") == "1":
        return
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(
                pytest.mark.skip(
                    reason="integration test; set KAIROS_RUN_INTEGRATION=1 with services up"
                )
            )
