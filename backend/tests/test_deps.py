from app.agent.deps import KairosAgentDeps
from app.domain import WorkspacePermission
from pydantic import TypeAdapter


def test_kairos_agent_deps_round_trips_without_runtime_handles_or_secrets() -> None:
    deps = KairosAgentDeps(
        user_id="user-a",
        task_id="task-a",
        task_run_id="run-a",
        spec_version_id=None,
        workspace_id="workspace-a",
        workspace_permission=WorkspacePermission.READ_WRITE,
        model_config_id="model-config-a",
        search_provider_config_id=None,
    )

    serialized = deps.model_dump_json()
    restored = TypeAdapter(KairosAgentDeps).validate_json(serialized)

    assert restored == deps
    assert all(
        forbidden not in serialized.lower()
        for forbidden in (
            "api_key",
            "credential_plaintext",
            "asyncsession",
            "httpx",
            "playwright",
            "s3client",
            "open_file",
            "process_handle",
        )
    )
