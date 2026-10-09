from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai import RunContext
from pydantic_ai.toolsets import AbstractToolset, CombinedToolset, DynamicToolset
from pydantic_ai_harness.filesystem import READ_ONLY_TOOL_NAMES, FileSystem
from pydantic_ai_harness.shell import LLM_API_KEY_ENV_PATTERNS, Shell

from app.config import get_settings
from app.domain import WorkspaceMetadata, WorkspacePermission

if TYPE_CHECKING:
    from app.agent.deps import KairosAgentDeps


# Keep sensitive workspace metadata out of the model-visible filesystem surface. Harness also
# applies its own protected defaults; these explicit patterns make the Kairos policy auditable and
# ensure reads as well as writes skip local credentials and repository internals.
KAIROS_WORKSPACE_DENIED_PATTERNS: tuple[str, ...] = (
    ".git",
    ".git/*",
    "**/.git",
    "**/.git/*",
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "*.pem",
    "**/*.pem",
    "*.key",
    "**/*.key",
    "**/secrets*",
)
KAIROS_WORKSPACE_PROTECTED_PATTERNS = KAIROS_WORKSPACE_DENIED_PATTERNS


class WorkspacePathResolver:
    """Canonicalize and contain paths under one already-authorized workspace root."""

    def __init__(self, root_dir: str | Path) -> None:
        root = Path(root_dir).expanduser()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"workspace root must be an existing directory: {root}")
        self.root = self._canonical(root)

    @staticmethod
    def _canonical(path: str | Path) -> Path:
        # realpath resolves Windows junctions/symlinks as well as POSIX links. Path.resolve then
        # normalizes the remaining drive/case/relative path representation for comparisons.
        return Path(os.path.realpath(os.path.abspath(os.fspath(path)))).resolve()

    def resolve(self, candidate: str | Path) -> Path:
        raw = Path(candidate).expanduser()
        joined = raw if raw.is_absolute() else self.root / raw
        target = self._canonical(joined)
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"path is outside workspace: {candidate}") from exc
        return target


class LocalFolderPicker:
    """Local-development folder picker with an explicit absolute-path fallback."""

    def pick(self, manual_path: str | None = None) -> Path:
        if manual_path:
            return WorkspacePathResolver(manual_path).root
        settings = get_settings()
        if not settings.native_folder_picker_enabled or platform.system().lower() != "windows":
            raise ValueError("native folder picker is disabled; provide an absolute manual_path")
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError as exc:
            raise ValueError("native folder picker is unavailable; provide an absolute manual_path") from exc
        root = tk.Tk()
        root.withdraw()
        try:
            selected = filedialog.askdirectory(title="Select Kairos Workspace")
        finally:
            root.destroy()
        if not selected:
            raise ValueError("no folder was selected")
        return WorkspacePathResolver(selected).root


def tool_names_for_permission(permission: WorkspacePermission) -> set[str]:
    if permission is WorkspacePermission.NONE:
        return set()
    if permission is WorkspacePermission.READ_ONLY:
        return set(READ_ONLY_TOOL_NAMES)
    return set(READ_ONLY_TOOL_NAMES) | {
        "write_file",
        "edit_file",
        "create_directory",
    }


def _available_commands() -> list[str]:
    if platform.system().lower() == "windows":
        candidates = ["python", "git", "node", "npm", "powershell"]
    else:
        candidates = ["python3", "python", "git", "node", "npm", "pwd", "whoami"]
    return [command for command in candidates if shutil.which(command)]


def build_shell_for_workspace(metadata: WorkspaceMetadata) -> Shell:
    if metadata.permission_mode not in (
        WorkspacePermission.READ_WRITE,
        WorkspacePermission.LOCAL_FULL_ACCESS,
    ):
        raise ValueError("shell is only available for a writable workspace")
    if metadata.permission_mode is WorkspacePermission.LOCAL_FULL_ACCESS and not get_settings().local_mode:
        raise ValueError("LOCAL_FULL_ACCESS requires KAIROS_LOCAL_MODE=true")

    denied_env_patterns = [
        *LLM_API_KEY_ENV_PATTERNS,
        "KAIROS_CREDENTIAL_MASTER_KEY",
        "KAIROS_CREDENTIAL_MASTER_KEY*",
        "DATABASE_URL",
        "POSTGRES_*",
        "MINIO_*",
        "TEMPORAL_*",
        "SESSION_*",
        "SECRET_*",
    ]
    return Shell(
        cwd=WorkspacePathResolver(metadata.root_path).root,
        allowed_commands=_available_commands(),
        denied_commands=[],
        denied_operators=[";", "&&", "||", "|", ">", ">>", "<"],
        persist_cwd=False,
        allow_interactive=False,
        denied_env_patterns=denied_env_patterns,
    )


async def build_workspace_toolset(
    ctx: RunContext[KairosAgentDeps], metadata: WorkspaceMetadata
) -> AbstractToolset[KairosAgentDeps] | None:
    del ctx
    permission = metadata.permission_mode
    if permission is WorkspacePermission.NONE:
        return None
    if permission is WorkspacePermission.LOCAL_FULL_ACCESS and not get_settings().local_mode:
        raise ValueError("LOCAL_FULL_ACCESS requires KAIROS_LOCAL_MODE=true")

    root = WorkspacePathResolver(metadata.root_path).root
    filesystem = FileSystem(
        root_dir=root,
        allowed_patterns=[],
        denied_patterns=KAIROS_WORKSPACE_DENIED_PATTERNS,
        protected_patterns=KAIROS_WORKSPACE_PROTECTED_PATTERNS,
        read_only=permission is WorkspacePermission.READ_ONLY,
    ).get_toolset()
    if permission is WorkspacePermission.READ_ONLY:
        return filesystem
    return CombinedToolset([filesystem, build_shell_for_workspace(metadata).get_toolset()])


async def resolve_workspace_toolset(
    ctx: RunContext[KairosAgentDeps],
) -> AbstractToolset[KairosAgentDeps] | None:
    """Resolve workspace metadata in the Activity-side dynamic toolset factory."""
    from app.repositories import get_workspace_metadata

    deps = ctx.deps
    if deps.workspace_id is None or deps.workspace_permission is WorkspacePermission.NONE:
        return None
    metadata = await get_workspace_metadata(deps.workspace_id, deps.user_id)
    if metadata is None or not metadata.enabled:
        raise ValueError("workspace is unavailable or not owned by the current user")
    if metadata.permission_mode is not deps.workspace_permission:
        raise ValueError("workspace permission changed after the run was created")
    return await build_workspace_toolset(ctx, metadata)


workspace_dynamic_toolset = DynamicToolset(
    resolve_workspace_toolset,
    per_run_step=False,
    id="kairos-workspace-v1",
)
