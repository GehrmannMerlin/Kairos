"""Real DeepSeek structured-plan release gate, run inside the Staging API container.

The acceptance user must already own an available DeepSeek ModelConfig whose key is
stored in CredentialVault. This program accepts no command-line arguments and never
reads an API key from the process environment.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

APP_ROOT = Path("/app")
if not APP_ROOT.exists():
    APP_ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(APP_ROOT))

from app.agents.goal_understanding import GoalUnderstandingAgent  # noqa: E402
from app.agents.plan_service import PlanGenerationService  # noqa: E402
from app.agents.service import GoalUnderstandingService  # noqa: E402
from app.auth.repository import UserRepository  # noqa: E402
from app.config import Settings  # noqa: E402
from app.credentials import crypto  # noqa: E402
from app.credentials.repository import CredentialRepository  # noqa: E402
from app.credentials.vault import CredentialVault  # noqa: E402
from app.domain.models import PlanVersion, Run  # noqa: E402
from app.domain.repository import TaskRepository  # noqa: E402
from app.domain.service import DomainService  # noqa: E402
from app.domain.spec import SourceScope, SpecDraftPayload  # noqa: E402
from app.domain.task_draft import TaskDraftService  # noqa: E402
from app.domain.task_types import TaskType  # noqa: E402
from app.infra.deps import get_session_factory  # noqa: E402
from app.infra.temporal import create_temporal_client  # noqa: E402
from app.plan.nodes import NodeRegistry  # noqa: E402
from app.plan.schemas import PlanGraphDraft, PlanValidationResult  # noqa: E402
from app.plan.service import PlanService, plan_fingerprint  # noqa: E402
from app.plan.validator import validate_plan  # noqa: E402
from app.providers.errors import ProviderInferenceError, ProviderTimeoutError  # noqa: E402
from app.providers.repository import ModelConfigRepository, SearchConfigRepository  # noqa: E402
from app.providers.service import ProviderService  # noqa: E402
from app.workflows.starter import TaskWorkflowStarter  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

REAL_CASES = (
    ("A", "采集山东省人民政府官网发布的最近一个月的干部任前公示信息"),
    ("B", "采集上海市人民政府官网最近一个月的任前公示信息"),
)
REQUIRED_RESULT_FIELDS = {
    "goal_ms",
    "plan_model_1_ms",
    "repair_used",
    "plan_model_2_ms",
    "plan_total_ms",
    "validation_result",
    "plan_version",
    "run_id",
    "workflow_id",
}


@dataclass(frozen=True)
class AcceptanceResult:
    test: str
    task_id: int | None
    goal_ms: int
    plan_model_1_ms: int
    repair_used: bool
    plan_model_2_ms: int
    plan_total_ms: int
    validation_result: str
    plan_version: int | None
    run_id: int | None
    workflow_id: str | None
    first_plan_valid: bool


def _build_services(db: Any, settings: Settings) -> tuple[ProviderService, CredentialVault]:
    vault = CredentialVault(
        master_key=crypto.master_key_from_env_value(settings.credential_master_key),
        key_version=settings.credential_key_version,
        repository=CredentialRepository(db),
    )
    provider = ProviderService(
        vault=vault,
        model_configs=ModelConfigRepository(db),
        search_configs=SearchConfigRepository(db),
    )
    return provider, vault


def _safe_result_json(result: AcceptanceResult, settings: Settings) -> str:
    payload = asdict(result)
    assert payload.keys() >= REQUIRED_RESULT_FIELDS
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    known_secrets = [settings.credential_master_key, settings.s3_secret_key]
    if any(secret and secret in serialized for secret in known_secrets):
        raise RuntimeError("secret leakage detected in acceptance result")
    return serialized


async def run_real_case(
    *, label: str, prompt: str, user: Any, db: Any, settings: Settings
) -> AcceptanceResult:
    provider, vault = _build_services(db, settings)
    config = provider.require_available_model_config(user)
    if config.provider_type != "deepseek" or config.credential_version_id is None:
        raise RuntimeError("acceptance user must have a Vault-backed DeepSeek default model")

    drafts = TaskDraftService(db)
    task, _ = drafts.create_draft_with_message(
        user_id=user.id,
        content=prompt,
        idempotency_key=f"structured-plan-acceptance-{label.lower()}-{os.urandom(8).hex()}",
    )
    goal = await GoalUnderstandingService(
        db,
        provider_service=provider,
        vault=vault,
        agent=GoalUnderstandingAgent(settings=settings),
    ).understand_for_task(user=user, task_id=task.id)
    if goal.status != "SUCCEEDED" or goal.spec_draft is None or goal.audit is None:
        raise RuntimeError("goal understanding did not produce a fresh CollectionSpec draft")

    task = drafts.get_task(user_id=user.id, task_id=task.id)
    spec = DomainService(TaskRepository(db)).confirm_spec(
        user_id=user.id,
        task_id=task.id,
        expected_version=task.version,
        spec_payload=goal.spec_draft,
        actor_id=user.id,
    )
    task = drafts.get_task(user_id=user.id, task_id=task.id)
    task_type = TaskType(spec.payload.get("task_type") or "SPECIFIED_SOURCE")
    generation = await PlanGenerationService(
        provider_service=provider,
        vault=vault,
        registry=NodeRegistry(),
        settings=settings,
    ).generate_for_task(user=user, spec_payload=spec.payload, task_type=task_type)
    if generation.validation_result not in {
        PlanValidationResult.VALID,
        PlanValidationResult.REQUIRES_APPROVAL,
    }:
        raise RuntimeError("real plan is not startable")

    graph = generation.graph.model_dump(mode="json")
    registry_versions = {
        definition.node_type.value: definition.definition_version
        for definition in NodeRegistry().all()
    }
    service = PlanService(db, starter=None)
    plan = service.persist_plan(
        user_id=user.id,
        task_id=task.id,
        spec_version=spec.version,
        graph=graph,
        validation_status=generation.validation_result.value,
        fingerprint_value=plan_fingerprint(graph, registry_versions),
        registry_versions=registry_versions,
        model_config_id=generation.audit.get("model_config_id"),
        model_config_version=generation.audit.get("model_config_version"),
        validation_issues=[
            issue.model_dump(mode="json", exclude_none=True, exclude={"expected_schema"})
            for issue in generation.issues
        ],
        expected_task_version=task.version,
    )
    prepared = service.prepare_start(
        user_id=user.id,
        task_id=task.id,
        spec_version=spec.version,
        plan_version=plan.version,
    )
    temporal = await create_temporal_client(settings)
    await service.dispatch_prepared_start(prepared, starter=TaskWorkflowStarter(temporal, settings))

    db.expire_all()
    plan_count = db.scalar(
        select(func.count()).select_from(PlanVersion).where(PlanVersion.task_id == task.id)
    )
    run_count = db.scalar(select(func.count()).select_from(Run).where(Run.task_id == task.id))
    if plan_count != 1 or run_count != 1:
        raise RuntimeError("duplicate or missing PlanVersion/Run")

    attempt_ms = tuple(int(value) for value in generation.audit["generation_attempt_ms"])
    first_plan_valid = not generation.repair_used
    return AcceptanceResult(
        test=label,
        task_id=task.id,
        goal_ms=int(goal.audit["duration_ms"]),
        plan_model_1_ms=attempt_ms[0],
        repair_used=generation.repair_used,
        plan_model_2_ms=attempt_ms[1] if len(attempt_ms) > 1 else 0,
        plan_total_ms=int(generation.audit["duration_ms"]),
        validation_result=generation.validation_result.value,
        plan_version=plan.version,
        run_id=prepared.run_id,
        workflow_id=prepared.workflow_id,
        first_plan_valid=first_plan_valid,
    )


class _FixturePlanAgent:
    def __init__(self, graphs: list[PlanGraphDraft]) -> None:
        self._graphs = list(graphs)

    async def generate(
        self, inp: Any, resolved: Any, *, api_key: str | None = None
    ) -> PlanGraphDraft:
        return self._graphs.pop(0)


async def run_controlled_repair_fixture() -> AcceptanceResult:
    registry = NodeRegistry()
    spec = SpecDraftPayload(
        task_type=TaskType.SPECIFIED_SOURCE,
        goal="controlled resource edge repair",
        source_scope=SourceScope(
            mode=TaskType.SPECIFIED_SOURCE,
            seed_urls=["https://example.com/notices"],
        ),
    ).model_dump(mode="json")
    invalid = PlanGraphDraft.model_validate(
        {
            "schema_version": "m08.1",
            "task_id": 0,
            "spec_version": 1,
            "task_type": "SPECIFIED_SOURCE",
            "nodes": [
                {
                    "node_id": "fetch-1",
                    "node_type": "fetch",
                    "definition_version": "1.0.0",
                    "parameters": {"url_template": "https://example.com/notices"},
                    "depends_on": [],
                },
                {
                    "node_id": "normalize-1",
                    "node_type": "normalize",
                    "definition_version": "1.0.0",
                    "parameters": {},
                    "depends_on": ["fetch-1"],
                },
            ],
            "edges": [
                {
                    "from_node_id": "fetch-1",
                    "to_node_id": "normalize-1",
                    "resource_refs": [{"kind": "snapshot", "ref_key": "snapshot:1"}],
                }
            ],
        }
    )
    initial = validate_plan(invalid, spec, registry)
    if "RESOURCE_EDGE_INCOMPATIBLE" not in {issue.code for issue in initial.issues}:
        raise RuntimeError("controlled repair fixture did not trigger the expected issue")
    repaired = PlanGraphDraft.model_validate(
        {
            "schema_version": "m08.1",
            "task_id": 0,
            "spec_version": 1,
            "task_type": "SPECIFIED_SOURCE",
            "nodes": [
                {
                    "node_id": "fetch-1",
                    "node_type": "fetch",
                    "definition_version": "1.0.0",
                    "parameters": {"url_template": "https://example.com/notices"},
                    "depends_on": [],
                }
            ],
            "edges": [],
        }
    )
    service = PlanGenerationService(
        registry=registry, agent=cast(Any, _FixturePlanAgent([invalid, repaired]))
    )
    inp = service.build_input(spec, TaskType.SPECIFIED_SOURCE, task_id=0, spec_version=1)
    outcome = await service._repair_loop(inp, None, max_repairs=1)
    if not outcome.repair_used:
        raise RuntimeError("controlled repair was not used")
    attempt_ms = tuple(int(value) for value in outcome.audit["generation_attempt_ms"])
    return AcceptanceResult(
        test="C",
        task_id=None,
        goal_ms=0,
        plan_model_1_ms=attempt_ms[0],
        repair_used=True,
        plan_model_2_ms=attempt_ms[1],
        plan_total_ms=int(outcome.audit["duration_ms"]),
        validation_result=outcome.validation_result.value,
        plan_version=None,
        run_id=None,
        workflow_id=None,
        first_plan_valid=False,
    )


async def _main() -> int:
    settings = Settings()
    if settings.env != "staging" or settings.provider_inference_timeout_seconds != 45:
        raise RuntimeError("this gate requires Staging with the 45-second provider deadline")
    email = os.environ.get("KAIROS_ACCEPTANCE_EMAIL", "").strip().lower()
    if not email:
        raise RuntimeError("KAIROS_ACCEPTANCE_EMAIL is required")

    db = get_session_factory()()
    try:
        user = UserRepository(db).get_by_email(email)
        if user is None:
            raise RuntimeError("acceptance user does not exist")
        real_results = [
            await run_real_case(
                label=label,
                prompt=prompt,
                user=user,
                db=db,
                settings=settings,
            )
            for label, prompt in REAL_CASES
        ]
        for result in real_results:
            print(_safe_result_json(result, settings), flush=True)
        if all(result.first_plan_valid for result in real_results):
            fixture_result = await run_controlled_repair_fixture()
            print(_safe_result_json(fixture_result, settings), flush=True)
        return 0
    finally:
        db.close()


def main() -> None:
    try:
        code = asyncio.run(_main())
    except (ProviderTimeoutError, ProviderInferenceError) as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}), flush=True)
        raise SystemExit(1) from None
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}), flush=True)
        raise SystemExit(1) from None
    raise SystemExit(code)


if __name__ == "__main__":
    main()
