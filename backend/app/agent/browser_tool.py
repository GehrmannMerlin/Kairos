"""kairos-browser-v1 toolset: the single browser escalation tool for the durable agent.

`request_browser_task` is the only browser tool the durable main Agent
(`kairos-agent-v1`) holds. It does NOT hold any low-level Playwright tool
(navigate/click/snapshot/javascript) — that life exists only inside the
Browser Activity via `BrowserTaskRunner`, whose temporary NON-DURABLE agent owns
real Chromium. This tool validates scope and runs the Activity workflow; the
Chromium session never crosses this boundary.
"""

from __future__ import annotations

from datetime import timedelta

from pydantic_ai import FunctionToolset, RunContext, Tool
from temporalio.common import RetryPolicy

from app.agent.deps import KairosAgentDeps
from app.domain import (
    BrowserLimits,
    BrowserTaskResult,
    CollectionError,
)
from app.repositories import get_collection_spec_for_run

# Independent Activity budget for the browser path (playwright + model + render).
# The real Chromium lifecycle sits entirely inside the start_to_close window; a
# bounded transient retry (new session each attempt) is allowlisted.
BROWSER_TOOL_ACTIVITY_CONFIG = {
    "start_to_close_timeout": timedelta(seconds=300),
    "retry_policy": RetryPolicy(
        maximum_attempts=2,
        initial_interval=timedelta(seconds=5),
        maximum_interval=timedelta(seconds=20),
    ),
}


def _server_browser_limits(spec_limits: BrowserLimits) -> BrowserLimits:
    """Freeze the resolved links: use the spec-frozen defaults, never a live server value."""
    return spec_limits


async def request_browser_task(
    ctx: RunContext[KairosAgentDeps], source_id: str
) -> BrowserTaskResult:
    """Request a Browser escalation capture for `source_id` (the only browser tool).

    Only the source_id crosses the durable agent boundary; the URL is resolved
    from the confirmed CollectionSpec/CollectionSource, so the model never
    chooses a target URL (§36). The runner holds the read-only policy and the
    step/time budgets; it returns a bounded result with no HTML/DOM/screenshot.
    """
    deps = ctx.deps
    if deps.spec_version_id is None:
        raise CollectionError("COLLECTION_SPEC_REQUIRED", "collection spec is required")
    spec = await get_collection_spec_for_run(
        deps.task_id, deps.task_run_id, deps.user_id, deps.spec_version_id
    )
    if spec is None:
        raise CollectionError("COLLECTION_SCOPE_NOT_FOUND", "collection run scope not found")
    limits = _server_browser_limits(spec.browser_limits)

    from app.browser.runner import run_browser_task_for_source

    return await run_browser_task_for_source(
        owner_id=deps.user_id,
        task_id=deps.task_id,
        task_run_id=deps.task_run_id,
        spec_version_id=deps.spec_version_id,
        source_id=source_id,
        model_config_id=deps.model_config_id,
        max_steps=limits.max_steps_per_task,
        task_timeout_seconds=limits.task_timeout_seconds,
    )


browser_toolset = FunctionToolset(
    [Tool(request_browser_task, name="request_browser_task")],
    id="kairos-browser-v1",
    instructions=(
        "After an HTTP snapshot is insufficient (little visible text, an app "
        "shell, JS-required content, hidden/expandable data), call "
        "request_browser_task(source_id) to render the page in a real browser. "
        "On success, re-inspect the returned snapshot with inspect_snapshot and "
        "then commit_extraction. Never request a browser for robots-blocked, "
        "private, 403/401, 404, or captcha sources — handle those as "
        "BLOCKED/FAILED normally. Do not use the browser to log in, submit, "
        "purchase, upload, or download."
    ),
    metadata={"temporal": BROWSER_TOOL_ACTIVITY_CONFIG},
)

__all__ = ["BROWSER_TOOL_ACTIVITY_CONFIG", "request_browser_task", "browser_toolset"]