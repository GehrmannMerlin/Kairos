# Kairos Agent Runtime

本轮 Kairos 的执行核心只有一条路径：

```text
Vue 3 Task Chat
  -> FastAPI
  -> Temporal Client.start_workflow
  -> KairosAgentWorkflow
  -> kairos-agent-v1 (Pydantic AI Agent)
  -> TemporalDurability
  -> Temporal Activities
  -> httpx / Harness FileSystem / Harness Shell
  -> bounded tool result
  -> Pydantic AI observe and continue
  -> final answer
  -> EventEnvelope persisted by Activity
  -> PostgreSQL-backed SSE
  -> Vue timeline
```

## Boundary decisions

`kairos-agent-v1` is a normal Pydantic AI `Agent`, constructed once at module scope. Pydantic AI owns the model/tool/observe loop. Pydantic AI Harness owns the reusable filesystem and shell capabilities. `TemporalDurability` and the current `PydanticAIPlugin` integration own the durable model, toolset, validation, and tool-call Activities. Kairos does not add another loop, dispatcher, planner executor, or generic durable-tool wrapper.

The old static Plan DAG is not the new execution truth. A plan may become a later capability or business artifact, but this foundation calls the one Agent's `run()` and lets the model decide whether and when to call a registered tool. This is what makes a real `model -> tool -> observe -> model` loop possible.

## Durable registration

The web toolset has stable ID `kairos-web-v1`. The workspace toolset has stable ID `kairos-workspace-v1` and is registered during Agent construction as the upstream `DynamicToolset`. Kairos never passes an executing `FunctionToolset` or `DynamicToolset` through `agent.run(toolsets=...)`; current Temporal durability rejects that runtime injection pattern. The upstream DynamicToolset integration generates stable `get_tools`, `validate_args`, and `call_tool` Activities for the registered ID.

The workflow is a `PydanticAIWorkflow` and declares `__pydantic_ai_agents__ = [kairos_agent]`. The Worker connects with `PydanticAIPlugin()`, allowing the upstream plugin to register the Agent Activities. Workflow code only schedules Activities for database event/run metadata and invokes `agent.run`; network, database, filesystem, shell, and model provider side effects remain outside deterministic workflow code.

## Deps and providers

`KairosAgentDeps` contains only IDs, enums, and bounded Pydantic values: user, task, run, spec, workspace, permission, model-config, and optional search-provider IDs. It deliberately excludes sessions, clients, handles, credentials, and process objects. A worker-side provider resolver uses `model_config_id` and a worker-local credential source. Secret values never enter Workflow input, deps, tool arguments, Temporal history, SSE payloads, or logs.

The checked-in local adapter currently reads the credential from the worker process environment so the boundary is explicit. It is an adapter point for the existing Kairos CredentialVault/ProviderConfig when those application modules are restored; it is not a second provider framework.

## Events and SSE

Vue receives Kairos `EventEnvelope` values, not Pydantic AI internal event classes. The minimal event contract is `run.started`, `tool.started`, `tool.completed`, `tool.failed`, `run.completed`, and `run.failed`. Pydantic AI tool-call/result events are mapped by the Temporal durability event handler, whose database write runs as the upstream event-handler Activity. The workflow explicitly schedules run start/end event Activities. FastAPI's SSE endpoint reads committed PostgreSQL events with an owner/task cursor; it does not publish from deterministic workflow code.

Only bounded metadata is persisted: tool name, argument-validity/outcome, result length, error type, and answer length. Tool bodies and workspace file contents are not placed in Temporal history or the event stream.

## Browser boundary

Playwright is intentionally not attached to this durable main Agent. The local Harness source rejects the direct Playwright + Temporal composition. A later browser design will keep this durable main Agent and call a separately scoped browser Activity/non-durable browser Agent; that work is outside this foundation.

