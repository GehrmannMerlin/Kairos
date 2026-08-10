"""Domain commands: transition_task / transition_node / checkpoint (M-04).

All writes for one command happen in the same db transaction and commit once;
a failure rolls back state, event and outbox together.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.models import Checkpoint, DomainEvent
from app.domain.repository import (
    CheckpointRepository,
    NodeAttemptRepository,
    NodeRunRepository,
    TaskRepository,
)
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
    ) -> DomainEvent:
        db = self._tasks._db
        task = self._tasks.get_owned(user_id, task_id)
        from app.domain.errors import StaleVersionError
        from app.state.states import TaskState

        current = TaskState(task.state)
        next_state = assert_task_transition(current, command)
        if task.version != expected_version:
            raise StaleVersionError("任务已被其他操作修改")

        payload: dict = {
            "command": command,
            "from_state": task.state,
            "to_state": next_state.value,
            "reason": reason,
        }
        if next_state == TaskState.DELETED:
            task.deleted_at = datetime.now(UTC)
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
        db.commit()
        db.refresh(event)
        return event

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
        if existing is not None:
            if existing.input_fingerprint != input_fingerprint:
                from app.domain.errors import DomainError

                raise DomainError("相同批次身份但输入指纹不同")
            return existing  # replay: reuse committed result
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
        db.commit()
        db.refresh(row)
        return row
