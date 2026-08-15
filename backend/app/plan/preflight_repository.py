"""Persistence for deterministic execution-preflight facts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.domain.models import ExecutionPreflightResult, Task

if TYPE_CHECKING:
    from app.plan.preflight import ExecutionPreflightOutcome


class ExecutionPreflightRepository:
    """Persist one immutable result per task/plan/manifest identity.

    The savepoint makes a simultaneous insert recoverable without rolling back
    the caller's session. The winning transaction alone reports ``created``.
    """

    def __init__(self, db: DbSession) -> None:
        self._db = db

    def get_or_create(
        self, outcome: ExecutionPreflightOutcome
    ) -> tuple[ExecutionPreflightResult, bool]:
        existing = self._find_existing(outcome)
        if existing is not None:
            return existing, False

        row = ExecutionPreflightResult(
            task_id=outcome.task_id,
            user_id=self._owner_id(outcome.task_id),
            spec_version=outcome.spec_version,
            plan_version=outcome.plan_version,
            capability_manifest_version=outcome.capability_manifest_version,
            status=outcome.status.value,
            issues=[issue.model_dump(mode="json") for issue in outcome.issues],
            search_config_id=outcome.search_config_id,
            search_config_version=outcome.search_config_version,
        )
        try:
            with self._db.begin_nested():
                self._db.add(row)
                self._db.flush()
        except IntegrityError:
            existing = self._find_existing(outcome)
            if existing is None:
                raise
            return existing, False

        self._db.commit()
        self._db.refresh(row)
        return row, True

    def _find_existing(self, outcome: ExecutionPreflightOutcome) -> ExecutionPreflightResult | None:
        return self._db.scalar(
            select(ExecutionPreflightResult).where(
                ExecutionPreflightResult.task_id == outcome.task_id,
                ExecutionPreflightResult.plan_version == outcome.plan_version,
                ExecutionPreflightResult.capability_manifest_version
                == outcome.capability_manifest_version,
            )
        )

    def _owner_id(self, task_id: int) -> int:
        user_id = self._db.scalar(select(Task.user_id).where(Task.id == task_id))
        if user_id is None:
            raise RuntimeError("owned task disappeared before preflight persistence")
        return user_id
