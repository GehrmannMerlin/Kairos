"""Phase 4 browser toolset tests: durable agent holds kairos-browser-v1 but no Playwright."""

from __future__ import annotations

from datetime import timedelta

from app.agent.browser_tool import BROWSER_TOOL_ACTIVITY_CONFIG, browser_toolset
from app.agent.runtime import kairos_agent


def _toolset_ids() -> set[str]:
    return {
        getattr(ts, "id", None)
        for ts in kairos_agent.toolsets
        if hasattr(ts, "id") and ts.id is not None
    }


def test_durable_agent_has_browser_toolset() -> None:
    assert "kairos-browser-v1" in _toolset_ids()
    assert "kairos-browser-v1" == browser_toolset.id


def _collect_cap_names(cap) -> list[str]:
    names: list[str] = []
    for attr in ("_capabilities", "capabilities"):
        if hasattr(cap, attr):
            for child in getattr(cap, attr):
                names.extend(_collect_cap_names(child))
    names.append(type(cap).__name__)
    return names


def test_durable_agent_has_no_playwright_capability() -> None:
    """P4-A Gate: kairos-agent-v1 must NOT carry a live-browser capability."""
    caps = _collect_cap_names(kairos_agent.root_capability)
    assert not any("Playwright" in c or "Browser" in c for c in caps), caps
    assert "ResolveModelId" in caps
    assert "TemporalDurability" in caps


def test_browser_toolset_exposes_only_request_browser_task() -> None:
    """The durable toolset's public surface is exactly `request_browser_task`."""
    names = set(browser_toolset.tools)
    assert names == {"request_browser_task"}


def test_browser_activity_config_independent_and_bounded() -> None:
    """The browser path gets its own Activity budget, not the collection one."""
    config = BROWSER_TOOL_ACTIVITY_CONFIG
    assert config["start_to_close_timeout"] == timedelta(seconds=300)
    rp = config["retry_policy"]
    assert rp.maximum_attempts == 2
    assert rp.initial_interval == timedelta(seconds=5)
    assert rp.maximum_interval == timedelta(seconds=20)


def test_browser_toolset_is_not_playwright_browser() -> None:
    """The durable toolset is a FunctionToolset, not the Harness capability."""
    from pydantic_ai.toolsets import FunctionToolset

    assert isinstance(browser_toolset, FunctionToolset)
    assert not hasattr(browser_toolset, "_session")  # no live browser session