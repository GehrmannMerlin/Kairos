from pathlib import Path

import pytest
from app.domain import WorkspacePermission
from app.workspace import WorkspacePathResolver, tool_names_for_permission
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai_harness.filesystem import FileSystem


def test_read_only_workspace_exposes_only_harness_read_tools() -> None:
    assert tool_names_for_permission(WorkspacePermission.READ_ONLY) == {
        "read_file",
        "list_directory",
        "search_files",
        "find_files",
        "file_info",
    }
    assert tool_names_for_permission(WorkspacePermission.NONE) == set()


def test_workspace_path_resolver_rejects_parent_and_absolute_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace-a"
    other = tmp_path / "workspace-b"
    workspace.mkdir()
    other.mkdir()
    resolver = WorkspacePathResolver(workspace)

    assert resolver.resolve("notes.txt") == workspace / "notes.txt"
    with pytest.raises(ValueError, match="outside workspace"):
        resolver.resolve("../workspace-b/secret.txt")
    with pytest.raises(ValueError, match="outside workspace"):
        resolver.resolve(str(other / "secret.txt"))


def test_workspace_path_resolver_rejects_symlink_escape_when_supported(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace-a"
    other = tmp_path / "workspace-b"
    workspace.mkdir()
    other.mkdir()
    (other / "secret.txt").write_text("private", encoding="utf-8")
    link = workspace / "linked"
    try:
        link.symlink_to(other, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable for this local Windows account")

    with pytest.raises(ValueError, match="outside workspace"):
        WorkspacePathResolver(workspace).resolve("linked/secret.txt")


@pytest.mark.asyncio
async def test_harness_file_tools_write_and_read_a_b_in_isolation(tmp_path: Path) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()

    filesystem_a = FileSystem(root_dir=workspace_a).get_toolset()
    filesystem_b = FileSystem(root_dir=workspace_b).get_toolset()
    fixed_text = "Kairos durable workspace isolation test."

    await filesystem_a.write_file("kairos-agent-test.md", fixed_text)
    await filesystem_b.write_file("kairos-agent-test.md", fixed_text)

    assert "Kairos durable workspace isolation test." in await filesystem_a.read_file("kairos-agent-test.md")
    assert "Kairos durable workspace isolation test." in await filesystem_b.read_file("kairos-agent-test.md")
    with pytest.raises(ModelRetry, match="outside the root directory"):
        await filesystem_a.read_file("../workspace-b/kairos-agent-test.md")
    with pytest.raises(ModelRetry, match="outside the root directory"):
        await filesystem_a.read_file(str(workspace_b / "kairos-agent-test.md"))


@pytest.mark.asyncio
async def test_harness_denies_sensitive_workspace_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("OPENAI_API_KEY=must-not-be-read", encoding="utf-8")

    filesystem = FileSystem(
        root_dir=workspace,
        denied_patterns=[".env"],
        protected_patterns=[".env"],
    ).get_toolset()
    with pytest.raises(ModelRetry):
        await filesystem.read_file(".env")
