"""PlanService — 持久化不可变 PlanVersion + 合法 Plan 自动启动 Workflow（M-08/D-038）。

D-038：低风险合法 Plan 不进行第二次 Plan 确认；Spec confirmed → PlanGenerator →
Validator → PlanVersion persisted → VALID → TaskWorkflowStarter.submit_validated_plan。
PlanVersion 不可变；后续 Replan 创建 vN+1，永不 UPDATE 已有版本。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.domain.idempotency import stable_fingerprint
from app.domain.models import ExecutionPreflightResult, PlanVersion
from app.domain.repository import PlanVersionRepository, RunRepository, TaskRepository
from app.state.events import append_domain_event, enqueue_outbox
from app.workflows.starter import TaskWorkflowStarter


def plan_fingerprint(graph: dict, registry_versions: dict) -> str:
    return stable_fingerprint("plan", graph, registry_versions)


@dataclass
class PlanCreatedResult:
    task_id: int
    plan_version: int
    validation_status: str
    run_id: int | None
    workflow_id: str | None


@dataclass(frozen=True)
class PreparedPlanStart:
    run_id: int
    workflow_id: str
    user_id: int
    task_id: int
    spec_version: int
    plan_version: int
    run_state: str


class PlanService:
    def __init__(
        self,
        db: Any,
        *,
        starter: TaskWorkflowStarter | Any | None,
        settings: Any | None = None,
    ) -> None:
        self._db = db
        self._starter = starter
        self._settings = settings

    @staticmethod
    def _preflight_issue_payloads(issues: list[Any]) -> list[dict]:
        """Expose only the safe, user-facing fields of immutable readiness facts."""
        return [
            {
                key: value
                for key, value in issue.model_dump(mode="json", exclude_none=True).items()
                if key in {"code", "safe_message", "node_id", "field"}
            }
            for issue in issues
        ]

    def require_ready_preflight(
        self,
        *,
        user_id: int,
        task_id: int,
        spec_version: int,
        plan_version: int,
        settings: Any,
    ) -> Any:
        """Persist/reuse readiness and reject before a Run can be prepared."""
        from app.domain.errors import DomainError, ExecutionPreflightBlockedError
        from app.plan.preflight import ExecutionPreflightService, ExecutionPreflightStatus

        plan = PlanVersionRepository(self._db).get_version(user_id, task_id, plan_version)
        if plan.spec_version != spec_version:
            raise DomainError("Plan 与 Spec 版本不匹配")
        if plan.validation_status not in {"VALID", "REQUIRES_APPROVAL"}:
            raise DomainError("该计划未通过启动校验")

        outcome = ExecutionPreflightService(self._db, settings=settings).evaluate(
            user_id=user_id,
            task_id=task_id,
            spec_version=spec_version,
            plan_version=plan_version,
        )
        issues = self._preflight_issue_payloads(outcome.issues)
        if outcome.created:
            task = TaskRepository(self._db).get_owned_for_update(user_id, task_id)
            event_type = (
                "task.execution_preflight_ready"
                if outcome.status is ExecutionPreflightStatus.READY
                else "task.execution_preflight_blocked"
            )
            payload = {
                "spec_version": spec_version,
                "plan_version": plan_version,
                "capability_manifest_version": outcome.capability_manifest_version,
                "preflight_status": outcome.status.value,
                "preflight_issues": issues,
            }
            append_domain_event(
                self._db,
                user_id=user_id,
                aggregate_type="task",
                aggregate_id=task_id,
                event_type=event_type,
                aggregate_version=task.version,
                payload=payload,
                actor_type="system",
            )
            enqueue_outbox(
                self._db,
                user_id=user_id,
                aggregate_type="task",
                aggregate_id=task_id,
                event_type=event_type,
                payload=payload,
                dispatch_key=(
                    f"task:{task_id}:execution_preflight:{plan_version}:"
                    f"{outcome.capability_manifest_version}"
                ),
            )
            self._db.commit()

        if outcome.status is ExecutionPreflightStatus.BLOCKED:
            message = issues[0]["safe_message"] if issues else "执行就绪检查未通过。"
            raise ExecutionPreflightBlockedError(
                message,
                context={
                    "preflight_status": outcome.status.value,
                    "preflight_issues": issues,
                },
            )
        return outcome

    def persist_plan(
        self,
        *,
        user_id: int,
        task_id: int,
        spec_version: int,
        graph: dict,
        validation_status: str,
        fingerprint_value: str,
        registry_versions: dict,
        model_config_id: str | None = None,
        model_config_version: int | None = None,
        generation_policy: str = "auto",
        trigger_reason: str | None = None,
        replan_evidence_refs: list | None = None,
        diff_summary: dict | None = None,
        validation_issues: list[dict] | None = None,
        expected_task_version: int | None = None,
    ) -> PlanVersion:
        task = TaskRepository(self._db).get_owned_for_update(user_id, task_id)
        if expected_task_version is not None and task.version != expected_task_version:
            from app.domain.errors import StaleVersionError

            raise StaleVersionError("任务已被其他操作修改")
        repo = PlanVersionRepository(self._db)
        version = repo.next_version(user_id, task_id)
        row = repo.create(
            user_id=user_id,
            task_id=task_id,
            spec_version=spec_version,
            version=version,
            payload={"graph": graph, "validator_issues": validation_issues or []},
            validation_status=validation_status,
            plan_fingerprint=fingerprint_value,
            registry_versions=registry_versions,
            model_config_id=model_config_id,
            model_config_version=model_config_version,
            generation_policy=generation_policy,
            trigger_reason=trigger_reason,
            replan_evidence_refs=replan_evidence_refs,
            diff_summary=diff_summary,
            commit=False,
        )
        task.current_plan_version = version
        task.version += 1
        self._db.add(task)
        payload = {"plan_version": version, "validation_status": validation_status}
        append_domain_event(
            self._db,
            user_id=user_id,
            aggregate_type="task",
            aggregate_id=task_id,
            event_type="task.plan_generated",
            aggregate_version=task.version,
            payload=payload,
            actor_type="system",
        )
        enqueue_outbox(
            self._db,
            user_id=user_id,
            aggregate_type="task",
            aggregate_id=task_id,
            event_type="task.plan_generated",
            payload=payload,
            dispatch_key=f"task:{task_id}:plan_generated",
        )
        self._db.commit()
        self._db.refresh(row)
        return row

    async def auto_start(
        self, *, user_id: int, task_id: int, spec_version: int, plan_version: int
    ) -> tuple[int | None, str | None]:
        if self._settings is None:
            raise RuntimeError("execution readiness settings are required")
        self.require_ready_preflight(
            user_id=user_id,
            task_id=task_id,
            spec_version=spec_version,
            plan_version=plan_version,
            settings=self._settings,
        )
        prepared = self.prepare_start(
            user_id=user_id,
            task_id=task_id,
            spec_version=spec_version,
            plan_version=plan_version,
        )
        started = await self.dispatch_prepared_start(prepared)
        return started.run_id, started.workflow_id

    def prepare_start(
        self, *, user_id: int, task_id: int, spec_version: int, plan_version: int
    ) -> PreparedPlanStart:
        """Serialize active-run lookup and creation on the owned task row."""

        from app.domain.errors import DomainError

        task = TaskRepository(self._db).get_owned_for_update(user_id, task_id)
        # The preflight read can leave Task in this Session's identity map while another
        # transaction advances its frozen pointers. Refresh under the acquired lock so a
        # stale cached instance can never authorize Run creation.
        self._db.refresh(task, with_for_update=True)
        plan = PlanVersionRepository(self._db).get_version(user_id, task_id, plan_version)
        if plan.spec_version != spec_version:
            raise DomainError("Plan 与 Spec 版本不匹配")
        if task.current_spec_version != spec_version or task.current_plan_version != plan_version:
            raise DomainError("Plan 与当前冻结任务版本不匹配")

        run_repo = RunRepository(self._db)
        run = run_repo.find_active_for_task(user_id, task_id)
        if run is not None and run.plan_version != plan_version:
            raise DomainError("任务已有其他计划版本正在执行")
        if run is None:
            run = run_repo.create(
                user_id=user_id,
                task_id=task_id,
                spec_version=spec_version,
                plan_version=plan_version,
                commit=False,
            )
            self._db.commit()
            self._db.refresh(run)
        else:
            # Release SELECT FOR UPDATE while the external Temporal RPC is in flight.
            self._db.commit()

        return PreparedPlanStart(
            run_id=run.id,
            workflow_id=f"task-workflow-{task_id}",
            user_id=user_id,
            task_id=task_id,
            spec_version=spec_version,
            plan_version=plan_version,
            run_state=run.state,
        )

    async def dispatch_prepared_start(
        self,
        prepared: PreparedPlanStart,
        *,
        starter: TaskWorkflowStarter | Any | None = None,
    ) -> Any:
        selected_starter = starter or self._starter
        if selected_starter is None:
            raise RuntimeError("workflow starter is required")
        if prepared.run_state == "running":
            from app.workflows.starter import RunStartedResult

            return RunStartedResult(prepared.run_id, prepared.workflow_id)
        return await selected_starter.start_persisted_run(
            user_id=prepared.user_id,
            task_id=prepared.task_id,
            run_id=prepared.run_id,
            spec_version=prepared.spec_version,
            plan_version=prepared.plan_version,
            workflow_id=prepared.workflow_id,
        )

    def create_replan(
        self,
        *,
        user_id: int,
        task_id: int,
        spec_version: int,
        graph: dict,
        fingerprint_value: str,
        registry_versions: dict,
        trigger_reason: str,
        replan_evidence_refs: list | None,
        diff_summary: dict | None,
    ) -> PlanVersion:
        """Replan 创建 vN+1 并保留 parent/diff/trigger/evidence；v1 永不修改。"""
        repo = PlanVersionRepository(self._db)
        parent = repo.latest_version(user_id, task_id)
        if parent is None:
            from app.domain.errors import DomainError

            raise DomainError("没有可重规划的 PlanVersion")
        version = parent.version + 1
        row = repo.create(
            user_id=user_id,
            task_id=task_id,
            spec_version=spec_version,
            version=version,
            payload={"graph": graph, "validator_issues": []},
            parent_plan_version_id=parent.id,
            validation_status="replan",
            plan_fingerprint=fingerprint_value,
            registry_versions=registry_versions,
            generation_policy="replan",
            trigger_reason=trigger_reason,
            replan_evidence_refs=replan_evidence_refs,
            diff_summary=diff_summary,
        )
        task = TaskRepository(self._db).get_owned(user_id, task_id)
        task.current_plan_version = version
        task.version += 1
        self._db.add(task)
        payload = {"plan_version": version, "validation_status": "replan"}
        append_domain_event(
            self._db,
            user_id=user_id,
            aggregate_type="task",
            aggregate_id=task_id,
            event_type="task.plan_replanned",
            aggregate_version=task.version,
            payload=payload,
            actor_type="system",
        )
        enqueue_outbox(
            self._db,
            user_id=user_id,
            aggregate_type="task",
            aggregate_id=task_id,
            event_type="task.plan_replanned",
            payload=payload,
            dispatch_key=f"task:{task_id}:plan_replanned",
        )
        self._db.commit()
        self._db.refresh(row)
        return row

    def get_plan_summary(self, *, user_id: int, task_id: int, plan_version: int) -> dict:
        row = PlanVersionRepository(self._db).get_version(user_id, task_id, plan_version)
        from app.plan.capabilities import CAPABILITY_MANIFEST_VERSION

        preflight = self._db.scalar(
            select(ExecutionPreflightResult).where(
                ExecutionPreflightResult.user_id == user_id,
                ExecutionPreflightResult.task_id == task_id,
                ExecutionPreflightResult.plan_version == plan_version,
                ExecutionPreflightResult.spec_version == row.spec_version,
                ExecutionPreflightResult.capability_manifest_version == CAPABILITY_MANIFEST_VERSION,
            )
        )
        graph = (row.payload or {}).get("graph", {}) if row.payload else {}
        nodes = graph.get("nodes", []) if graph else []
        active_run = RunRepository(self._db).find_active_for_task(user_id, task_id)
        if active_run is not None and active_run.plan_version != plan_version:
            active_run = None
        return {
            "task_id": task_id,
            "plan_version": row.version,
            "spec_version": row.spec_version,
            "validation_status": row.validation_status,
            "plan_fingerprint": row.plan_fingerprint,
            "node_count": len(nodes),
            "node_types": [n.get("node_type") for n in nodes],
            "diff_summary": row.diff_summary,
            "trigger_reason": row.trigger_reason,
            "run_id": active_run.id if active_run is not None else None,
            "run_state": active_run.state if active_run is not None else None,
            "start_recoverable": bool(active_run is not None and active_run.state == "pending"),
            "validator_issues": (row.payload or {}).get("validator_issues", []),
            "preflight_status": preflight.status if preflight is not None else None,
            "preflight_issues": (
                [
                    {
                        key: value
                        for key, value in issue.items()
                        if key in {"code", "safe_message", "node_id", "field"}
                    }
                    for issue in preflight.issues
                ]
                if preflight is not None
                else []
            ),
            "created_at": row.created_at,
        }

    def list_plan_summaries(self, *, user_id: int, task_id: int) -> list[dict]:
        rows = PlanVersionRepository(self._db).list_for_task(user_id, task_id)
        return [
            self.get_plan_summary(user_id=user_id, task_id=task_id, plan_version=r.version)
            for r in rows
        ]
