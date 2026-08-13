"""PlanService — 持久化不可变 PlanVersion + 合法 Plan 自动启动 Workflow（M-08/D-038）。

D-038：低风险合法 Plan 不进行第二次 Plan 确认；Spec confirmed → PlanGenerator →
Validator → PlanVersion persisted → VALID → TaskWorkflowStarter.submit_validated_plan。
PlanVersion 不可变；后续 Replan 创建 vN+1，永不 UPDATE 已有版本。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.idempotency import stable_fingerprint
from app.domain.models import PlanVersion
from app.domain.repository import PlanVersionRepository, TaskRepository
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


class PlanService:
    def __init__(self, db: Any, *, starter: TaskWorkflowStarter | Any) -> None:
        self._db = db
        self._starter = starter

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
    ) -> PlanVersion:
        repo = PlanVersionRepository(self._db)
        version = repo.next_version(user_id, task_id)
        row = repo.create(
            user_id=user_id,
            task_id=task_id,
            spec_version=spec_version,
            version=version,
            payload={"graph": graph},
            validation_status=validation_status,
            plan_fingerprint=fingerprint_value,
            registry_versions=registry_versions,
            model_config_id=model_config_id,
            model_config_version=model_config_version,
            generation_policy=generation_policy,
            trigger_reason=trigger_reason,
            replan_evidence_refs=replan_evidence_refs,
            diff_summary=diff_summary,
        )
        task = TaskRepository(self._db).get_owned(user_id, task_id)
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
        started = await self._starter.submit_validated_plan(
            user_id=user_id,
            task_id=task_id,
            spec_version=spec_version,
            plan_version=plan_version,
        )
        return started.run_id, started.workflow_id

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
            payload={"graph": graph},
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
        graph = (row.payload or {}).get("graph", {}) if row.payload else {}
        nodes = graph.get("nodes", []) if graph else []
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
            "created_at": row.created_at,
        }

    def list_plan_summaries(self, *, user_id: int, task_id: int) -> list[dict]:
        rows = PlanVersionRepository(self._db).list_for_task(user_id, task_id)
        return [
            self.get_plan_summary(user_id=user_id, task_id=task_id, plan_version=r.version)
            for r in rows
        ]
