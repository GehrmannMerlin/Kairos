import os
from pathlib import Path

import pytest
from app.domain import WorkspaceMetadata, WorkspacePermission
from app.workspace import build_shell_for_workspace


def test_shell_uses_workspace_cwd_and_does_not_persist_between_runs(tmp_path: Path) -> None:
    metadata = WorkspaceMetadata(
        workspace_id="workspace-a",
        owner_id="user-a",
        display_name="Workspace A",
        root_path=str(tmp_path),
        permission_mode=WorkspacePermission.READ_WRITE,
    )

    shell = build_shell_for_workspace(metadata)

    assert Path(shell.cwd).resolve() == tmp_path.resolve()
    assert shell.persist_cwd is False
    assert "KAIROS_CREDENTIAL_MASTER_KEY" in shell.denied_env_patterns


@pytest.mark.asyncio
async def test_shell_executes_from_the_workspace_cwd(tmp_path: Path) -> None:
    metadata = WorkspaceMetadata(
        workspace_id="workspace-a",
        owner_id="user-a",
        display_name="Workspace A",
        root_path=str(tmp_path),
        permission_mode=WorkspacePermission.READ_WRITE,
    )
    toolset = build_shell_for_workspace(metadata).get_toolset()
    command = "powershell -NoProfile -Command Get-Location" if os.name == "nt" else "pwd"

    output = await toolset.run_command(command)

    assert str(tmp_path).lower() in output.lower()
