"""Phase 4 browser boundary: durable+browser composition is rejected; session lifecycle works."""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import ResolveModelId
from pydantic_ai.durable_exec.temporal import TemporalDurability
from pydantic_ai.exceptions import UserError
from pydantic_ai_harness.playwright import EgressPolicy, PlaywrightBrowser, PlaywrightBrowserSession


def _deferred_agent() -> Agent[None, str]:
    return Agent(
        "kairos:local-model",
        capabilities=[ResolveModelId(lambda ctx, model_id: None)],
        defer_model_check=True,
    )


def test_durable_plus_browser_is_rejected() -> None:
    """Harness refuses PlaywrightBrowser on a durable-execution agent at construction."""
    with pytest.raises(UserError, match="does not support durable execution"):
        Agent(
            "kairos:local-model",
            capabilities=[TemporalDurability(), PlaywrightBrowser()],
            defer_model_check=True,
        )


def test_browser_agent_without_durability_constructs() -> None:
    """A non-durable agent may carry PlaywrightBrowser (the Phase 4 runner shape)."""
    agent = Agent(
        "kairos:local-model",
        capabilities=[ResolveModelId(lambda ctx, model_id: None), PlaywrightBrowser()],
        defer_model_check=True,
    )
    assert agent is not None


@pytest.mark.asyncio
async def test_session_launch_navigate_capture_close() -> None:
    """Real Chromium: launch, navigate to a JS page, capture text, and release cleanly."""
    launched = False
    try:
        async with PlaywrightBrowserSession(policy=EgressPolicy()) as session:
            page = await session.ensure_page()
            launched = True
            await page.goto("https://quotes.toscrape.com/js/", timeout=60_000)
            await page.wait_for_load_state("domcontentloaded")
            text = await page.inner_text("body")
            assert text and len(text) > 200, "rendered page returned little text"
            assert "quote" in text.lower(), "JS-rendered quote content is missing"
    finally:
        # If this leaks, the runner process would keep a chrome.exe alive after the test.
        assert launched
        # Session context manager teardown has already run; nothing to clean here.
