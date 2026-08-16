"""Domain commands: transition_task / transition_node / checkpoint (M-04).

All writes for one command happen in the same db transaction and commit once;
a failure rolls back state, event and outbox together.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.models import Checkpoint, CollectionSpecVersion, DomainEvent
from app.domain.repository import (
    CheckpointRepository,
    NodeAttemptRepository,
    NodeRunRepository,
    SpecVersionRepository,
    TaskRepository,
)
from app.domain.spec import validate_confirmable_spec_payload
from app.state.events import append_domain_event, enqueue_outbox
from app.state.states import assert_node_transition, assert_task_transition


class DomainService:
    def __init__(
        self,
        task_repo: TaskRepository,
        node_repo: NodeRunRepository | None = None,
        attempt_repo: NodeAttemptRepository | None = None,
    ) -> None:
        self._tasks = task_repo
        self._nodes = node_repo or NodeRunRepository(task_repo._db)
        self._attempts = attempt_repo or NodeAttemptRepository(task_repo._db)

    def transition_task(
        self,
        *,
        user_id: int,
        task_id: int,
        command: str,
        expected_version: int,
        actor_type: str = "user",
        actor_id: int | None = None,
        reason: str | None = None,
        commit: bool = True,
    ) -> DomainEvent:
        db = self._tasks._db
        task = self._tasks.get_owned(user_id, task_id)
        from app.domain.errors import StaleVersionError
        from app.state.states import TaskState

        if task.version != expected_version:
            raise StaleVersionError("任务已被其他操作修改")

        current = TaskState(task.state)
        if command == "restore":
            # M-15：恢复到软删除前的终态（生命周期/可见性语义，不改写 Run execution facts）。
            next_state = TaskState(task.restore_state or "DRAFT")
        else:
            next_state = assert_task_transition(current, command)

        payload: dict = {
            "command": command,
            "from_state": task.state,
            "to_state": next_state.value,
            "reason": reason,
        }
        if command == "delete":
            task.deleted_at = datetime.now(UTC)
            task.restore_state = task.state
        elif command == "restore":
            task.deleted_at = None
            task.restore_state = None
        task.state = next_state.value
        task.version += 1
        db.add(task)

        event = append_domain_event(
            db,
            user_id=user_id,
            aggregate_type="task",
            aggregate_id=task_id,
            event_type=f"task.{command}",
            aggregate_version=task.version,
            payload=payload,
            actor_type=actor_type,
            actor_id=actor_id,
        )
        enqueue_outbox(
            db,
            user_id=user_id,
            aggregate_type="task",
            aggregate_id=task_id,
            event_type=f"task.{command}",
            payload=payload,
            dispatch_key=f"task:{task_id}:{command}",
        )
        if commit:
            db.commit()
            db.refresh(event)
        return event

    def confirm_spec(
        self,
        *,
        user_id: int,
        task_id: int,
        expected_version: int,
        spec_payload: dict,
        actor_id: int | None = None,
    ) -> CollectionSpecVersion:
        """Freeze a CollectionSpecVersion in one transaction (M-06).

        - Server-side typed validation of the payload.
        - optimistic version control (STALE_VERSION on mismatch).
        - DRAFT -> QUEUED via the M-04 state machine (submit); a QUEUED task may
          be re-confirmed as a new version before execution starts.
        - creates an immutable version row + task.current_spec_version +
          append-only DomainEvent + transactional Outbox, committed once.
        - Confirming never UPDATEs an existing version; a revision becomes vN+1.
        """
        from app.domain.errors import IllegalTransitionError, StaleVersionError
        from app.state.states import TaskState

        db = self._tasks._db
        task = self._tasks.get_owned(user_id, task_id)
        if task.version != expected_version:
            raise StaleVersionError("任务已被其他操作修改")

        current = TaskState(task.state)
        if current == TaskState.DRAFT:
            next_state = assert_task_transition(current, "submit")
        elif current == TaskState.QUEUED:
            next_state = current  # pre-execution revision; already queued
        else:
            raise IllegalTransitionError("当前状态不允许确认采集方案")

        spec = validate_confirmable_spec_payload(spec_payload)
        specs = SpecVersionRepository(db)
        version = specs.next_version(user_id, task_id)

        row = CollectionSpecVersion(
            user_id=user_id,
            task_id=task_id,
            version=version,
            spec_type="collection",
            schema_version=spec.schema_version,
            payload=spec.model_dump(mode="json"),
            confirmed_at=datetime.now(UTC),
            confirmed_by=actor_id or user_id,
        )
        db.add(row)

        task.current_spec_version = version
        if spec.task_type is not None:
            task.task_type = spec.task_type.value
        task.state = next_state.value
        task.version += 1
        db.add(task)

        payload: dict = {
            "command": "confirm_spec",
            "spec_version": version,
            "from_state": current.value,
            "to_state": next_state.value,
        }
        event = append_domain_event(
            db,
            user_id=user_id,
            aggregate_type="task",
            aggregate_id=task_id,
            event_type="task.spec_confirmed",
            aggregate_version=task.version,
            payload=payload,
            actor_type="user",
            actor_id=actor_id or user_id,
        )
        enqueue_outbox(
            db,
            user_id=user_id,
            aggregate_type="task",
            aggregate_id=task_id,
            event_type="task.spec_confirmed",
            payload=payload,
            dispatch_key=f"task:{task_id}:spec_confirmed",
        )
        db.commit()
        db.refresh(row)
        db.refresh(event)
        return row

    def transition_node(
        self,
        *,
        user_id: int,
        node_run_id: int,
        command: str,
        expected_version: int,
        actor_type: str = "system",
        actor_id: int | None = None,
        reason: str | None = None,
    ) -> DomainEvent:
        db = self._nodes._db
        node = self._nodes.get_owned(user_id, node_run_id)
        from app.domain.errors import StaleVersionError
        from app.state.states import NodeState

        current = NodeState(node.state)
        next_state = assert_node_transition(current, command)
        if node.version != expected_version:
            raise StaleVersionError("节点已被其他操作修改")

        payload: dict = {
            "command": command,
            "from_state": node.state,
            "to_state": next_state.value,
            "reason": reason,
        }
        if next_state == NodeState.RUNNING:
            attempt_no = self._attempts.next_attempt(node.id)
            self._attempts.create(user_id=user_id, node_run_id=node.id, attempt=attempt_no)
        node.state = next_state.value
        node.version += 1
        db.add(node)

        event = append_domain_event(
            db,
            user_id=user_id,
            aggregate_type="node_run",
            aggregate_id=node_run_id,
            event_type=f"node.{command}",
            aggregate_version=node.version,
            payload=payload,
            actor_type=actor_type,
            actor_id=actor_id,
            run_id=node.run_id,
            node_run_id=node_run_id,
        )
        enqueue_outbox(
            db,
            user_id=user_id,
            aggregate_type="node_run",
            aggregate_id=node_run_id,
            event_type=f"node.{command}",
            payload=payload,
            dispatch_key=f"node:{node_run_id}:{command}",
        )
        db.commit()
        db.refresh(event)
        return event

    def commit_checkpoint(
        self,
        *,
        user_id: int,
        task_id: int,
        run_id: int,
        batch_identity: str,
        spec_version: int,
        plan_version: int,
        node_run_id: int | None,
        input_fingerprint: str,
        committed_refs: dict,
        content_hash: str | None,
    ) -> Checkpoint:
        """Record a committed batch. Replay reuses; differing fingerprint conflicts."""
        db = self._tasks._db
        repo = CheckpointRepository(db)
        existing = repo.find_by_batch(run_id, batch_identity)
        if existing is not None and existing.input_fingerprint != input_fingerprint:
            from app.domain.errors import DomainError

            raise DomainError("相同批次身份但输入指纹不同")
        if existing is None:
            from sqlalchemy.exc import IntegrityError

            row: Checkpoint | None = None
            try:
                with db.begin_nested():
                    row = repo.create(
                        user_id=user_id,
                        task_id=task_id,
                        run_id=run_id,
                        batch_identity=batch_identity,
                        spec_version=spec_version,
                        plan_version=plan_version,
                        node_run_id=node_run_id,
                        input_fingerprint=input_fingerprint,
                        committed_object_refs=committed_refs,
                        content_hash=content_hash,
                    )
                    db.flush()
            except IntegrityError:
                row = repo.find_by_batch(run_id, batch_identity)
                if row is None:
                    raise
        else:
            row = existing
        assert row is not None
        if row.input_fingerprint != input_fingerprint:
            from app.domain.errors import DomainError

            raise DomainError("相同批次身份但输入指纹不同")
        from app.execution.lifecycle import append_checkpoint_event

        append_checkpoint_event(db, row)
        db.commit()
        db.refresh(row)
        return row
