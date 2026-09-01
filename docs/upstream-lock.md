# Kairos upstream lock

本文件锁定本轮源码研究和运行依赖所对应的 upstream 版本。禁止使用 `latest` 或未记录 SHA 的浮动源码。

| Dependency | Local source repo | Branch | Source commit | Runtime dependency |
|---|---|---|---|---|
| Pydantic AI Core | `D:\Develop\kairos\pydantic-ai` | `main` | `90de62ab5594912fc0e73a51b7adb2d4c0ee7554` | local editable path `pydantic-ai/pydantic_ai_slim` |
| Pydantic AI Harness | `D:\Develop\kairos\pydantic-ai-harness` | `main` | `9e1c16b52025ad42749dae1a1d901dffdf55432e` | local editable path `pydantic-ai-harness` |
| Temporal Python SDK | `D:\Develop\kairos\Temporal` | `main` | `b6ee53cf748cfc98d9e32eee2d136ca8abb9a6ad` | `temporalio==1.32.0` wheel, matching the checkout's `pyproject.toml` version |

## Why Temporal uses the matching wheel

The local Temporal checkout is a `maturin` project with a Rust bridge. The current Windows machine has no `rustc`/`cargo`; the attempted editable build tried to download rustup and failed with a TLS connection error. We therefore do not pretend that checkout is editable. The runtime pins the exact package version declared by that checkout (`1.32.0`), while the checkout remains the authoritative source used for the API audit. Installing Rust and switching to the exact editable source is a future developer-environment improvement, not a hidden fallback.

## Python and dependency source

- Supported Python: `>=3.11,<3.14` for Kairos local development.
- Upstream declared requirement: `>=3.10`.
- Current verified interpreter: Python 3.11.8.
- Pydantic AI and Harness resolve from local editable paths.
- Temporal runtime resolves from the exact `1.32.0` wheel because the local native bridge cannot build on this machine.
- `uv.lock` is generated in `kairos-app/backend` and is the dependency resolution record for this app.

## Secret boundary

Provider keys and credential plaintext are not in this file, `uv.lock`, Workflow input, `KairosAgentDeps`, tool args, Temporal History, SSE, or logs.

