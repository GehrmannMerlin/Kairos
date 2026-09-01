from pathlib import Path

import pytest
from app.agent.deps import KairosAgentDeps
from app.domain import WorkspaceMetadata, WorkspacePermission
from app.workspace import build_workspace_toolset, workspace_dynamic_toolset
from pydantic_ai import RunContext


def test_workspace_dynamic_toolset_has_stable_durable_identity() -> None:
    assert workspace_dynamic_toolset.id == "kairos-workspace-v1"


@pytest.mark.asyncio
async def test_workspace_toolset_resolves_real_harness_tools_for_read_write(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    metadata = WorkspaceMetadata(
        workspace_id="workspace-a",
        owner_id="user-a",
        display_name="Workspace A",
        root_path=str(root),
        permission_mode=WorkspacePermission.READ_WRITE,
    )
    deps = KairosAgentDeps(
        user_id="user-a",
        task_id="task-a",
        task_run_id="run-a",
        workspace_id=metadata.workspace_id,
        workspace_permission=metadata.permission_mode,
        model_config_id="model-config-a",
    )
    ctx = RunContext[object](deps=deps, model=None, usage=None, run_id="run-a")

    toolset = await build_workspace_toolset(ctx, metadata)
    tools = await toolset.get_tools(ctx)

    assert {"read_file", "write_file", "edit_file"}.issubset(tools)
    assert "run_command" in tools
