# Kairos Workspace Boundary

Workspace is a user-authorized local directory attached to a Task. PostgreSQL stores its business metadata; the directory itself remains user-owned local state. The database record contains `workspace_id`, owner, display name, canonical root path, permission mode, enabled flag, and timestamps. Task and workspace queries are owner-scoped.

## One path resolver

`WorkspacePathResolver` is the single canonicalization boundary. It requires an existing directory, normalizes absolute/relative paths, resolves symlinks and Windows junctions, and checks containment after real-path resolution. Routes, Agent code, and tools do not independently concatenate a workspace root. Harness `FileSystem` performs its own final containment check for every operation, so the Kairos resolver is an authorization/metadata boundary rather than a replacement filesystem sandbox.

The local folder picker is explicitly local-development-only. It can use a Windows native picker when enabled, or accept a manually entered absolute path. Browser `webkitdirectory` is not used because browser-relative file permissions are not an OS directory root that a backend worker can safely bind.

## Permission modes

| Mode | FileSystem | Shell | Additional condition |
|---|---|---|---|
| `NONE` | no workspace toolset | no | default when unbound |
| `READ_ONLY` | Harness read-only filtered toolset | no | read/list/search/info only |
| `READ_WRITE` | Harness read/write toolset | yes, rooted at workspace | local development slice |
| `LOCAL_FULL_ACCESS` | read/write | yes, rooted at workspace | explicit user selection and `KAIROS_LOCAL_MODE=true` |

The dynamic workspace factory receives only `workspace_id` and the captured permission in `KairosAgentDeps`. In the Temporal-generated dynamic-toolset Activities it loads owner-scoped metadata, verifies that permission has not changed, canonicalizes the root, and constructs `FileSystem(root_dir=...)` and, for writable modes, `Shell(cwd=...)`. The factory returns `None` for `NONE`.

This is why the workspace toolset is an upstream `DynamicToolset` with stable ID `kairos-workspace-v1`: the toolset is constructed at the Activity boundary while the executing DynamicToolset itself is registered at Agent construction. This satisfies Temporal's restriction against injecting an unregistered executing toolset at run time.

## File and shell policy

Harness's `FileSystem` is used directly with explicit Kairos denied/protected patterns for `.git`, `.env`, key/certificate files, and secret-shaped files. READ_ONLY uses Harness's official filtered tool names; Kairos does not copy or fork the filesystem implementation.

Harness `Shell` is rooted at the canonical workspace and uses `persist_cwd=False`, non-interactive mode, denied shell operators, bounded output, and an OS-aware allowlist of executables found on the current machine. Harness requires `allowed_commands` and `denied_commands` to be mutually exclusive; the Kairos allowlist intentionally excludes destructive executables, while retaining the effective destructive-command block. Provider, database, Temporal, session, and credential environment variables are stripped using Harness's `LLM_API_KEY_ENV_PATTERNS` plus Kairos patterns. `LOCAL_FULL_ACCESS` expands directory permission only; it never exports Kairos or provider secrets.

## Isolation proof target

Workspace A and Workspace B are separate metadata records and separate real directories. The targeted tests exercise the real Harness tools in both roots and reject A-to-B `../`, absolute, and symlink escape attempts. The full acceptance gate additionally requires a real model-directed Agent run to create and reread `kairos-agent-test.md` through Temporal Activities; without a real provider credential, that gate remains BLOCKED rather than being replaced with a fake success.

