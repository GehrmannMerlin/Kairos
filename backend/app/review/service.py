"""M-13 ReviewService：approve/reject/edit/agent_reevaluate/batch（D-042/D-061）。

状态变化走领域命令 + append_domain_event，单事务提交；人工修正写入 record_field_overrides
（original/final/value_source/modified_by/modified_at），不覆盖 FieldEvidence/PageSnapshot。
record.* 事件 payload 携带 task_id，供 SSE 任务流重放（app/api/events.query_task_events）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session as DbSession

from app.domain.errors import DomainError
from app.domain.models import Record
from app.review.contracts import (
    BatchReviewCommand,
    BatchReviewItem,
    BatchReviewResponse,
    FieldEdit,
    RecordReviewCommand,
    RecordView,
    ReviewAction,
)
from app.review.policy import BatchCompatibilityError, ReviewPolicy
from app.review.repository import ReviewRepository
from app.review.views import to_view
from app.state.events import append_domain_event


class ReviewConflictError(DomainError):
    """数据版本冲突或审核动作非法。"""

    code = "REVIEW_CONFLICT"
    status_code = 409


class ReviewService:
    def __init__(self, db: DbSession) -> None:
        self._db = db
        self._repo = ReviewRepository(db)

    def execute(self, *, user_id: int, record_id: int, cmd: RecordReviewCommand) -> RecordView:
        record = self._repo.get_record_owned(user_id=user_id, record_id=record_id)
        self._assert_version(record, cmd.expected_data_version)
        actions = ReviewPolicy.allowed_actions(record=record)
        if cmd.action.value not in actions:
            raise ReviewConflictError("当前记录不允许执行该审核动作")
        if cmd.action is ReviewAction.APPROVE:
            self._apply_partition(
                record,
                "passed",
                action_type="approve",
                event="record.approved",
                reason=cmd.reason,
                user_id=user_id,
            )
        elif cmd.action is ReviewAction.REJECT:
            self._apply_partition(
                record,
                "rejected",
                action_type="reject",
                event="record.rejected",
                reason=cmd.reason,
                user_id=user_id,
            )
        elif cmd.action is ReviewAction.EDIT:
            self._apply_edits(user_id=user_id, record=record, edits=cmd.edits)
        elif cmd.action is ReviewAction.AGENT_REEVALUATE:
            from app.review.reevaluate import request_reevaluate

            request_reevaluate(self._db, user_id=user_id, record=record, reason=cmd.reason)
        else:
            raise ReviewConflictError("该动作需要更高级处理，暂不开放")
        self._db.commit()
        overrides = self._repo.list_overrides(user_id=user_id, record_id=record.id)
        return to_view(record, overrides, self._repo.url_for_record(record=record))

    def batch(self, *, user_id: int, task_id: int, cmd: BatchReviewCommand) -> BatchReviewResponse:
        from app.domain.idempotency import stable_fingerprint

        records = [
            self._repo.get_record_owned(user_id=user_id, record_id=rid) for rid in cmd.record_ids
        ]
        batch_op = stable_fingerprint(
            f"batch:{user_id}:{task_id}:{cmd.action}:{cmd.record_ids}:{cmd.reason}"
        )
        # 语义兼容前置校验（D-061）：不兼容整批拒绝，不出现"部分通过"
        try:
            ReviewPolicy.assert_batch_compatible(action=cmd.action, records=records)
        except BatchCompatibilityError as exc:
            failed_results = [
                BatchReviewItem(record_id=r.id, ok=False, error=str(exc)) for r in records
            ]
            return BatchReviewResponse(batch_operation_id=batch_op, results=failed_results)

        results: list[BatchReviewItem] = []
        for record in records:
            try:
                expected = cmd.expected_data_versions.get(record.id, record.data_version)
                self._assert_version(record, expected)
                if cmd.action == "approve":
                    self._apply_partition(
                        record,
                        "passed",
                        action_type="approve",
                        event="record.approved_batch",
                        reason=cmd.reason,
                        user_id=user_id,
                        batch_operation_id=batch_op,
                    )
                elif cmd.action == "reject":
                    self._apply_partition(
                        record,
                        "rejected",
                        action_type="reject",
                        event="record.rejected_batch",
                        reason=cmd.reason,
                        user_id=user_id,
                        batch_operation_id=batch_op,
                    )
                else:  # agent_reevaluate
                    from app.review.reevaluate import request_reevaluate

                    request_reevaluate(
                        self._db,
                        user_id=user_id,
                        record=record,
                        reason=cmd.reason,
                        batch_operation_id=batch_op,
                    )
                results.append(
                    BatchReviewItem(record_id=record.id, ok=True, partition=record.partition)
                )
            except Exception as exc:  # noqa: BLE001 —— 单条失败不拖垮整批
                results.append(BatchReviewItem(record_id=record.id, ok=False, error=str(exc)))
        self._db.commit()
        return BatchReviewResponse(batch_operation_id=batch_op, results=results)

    # ---- internal ----
    @staticmethod
    def _assert_version(record: Record, expected: int) -> None:
        if expected != record.data_version:
            raise ReviewConflictError("记录已更新，请刷新后重试")

    def _apply_partition(
        self,
        record: Record,
        partition: str,
        *,
        action_type: str,
        event: str,
        reason: str | None,
        user_id: int,
        batch_operation_id: str | None = None,
    ) -> None:
        prev_review_type = record.review_type
        prev_review_reason = record.review_reason
        record.partition = partition
        record.review_type = None
        record.review_reason = None
        record.data_version += 1
        append_domain_event(
            self._db,
            user_id=user_id,
            aggregate_type="record",
            aggregate_id=record.id,
            aggregate_version=record.data_version,
            event_type=event,
            payload={
                "task_id": record.task_id,
                "partition": partition,
                "reason": reason,
                "data_version": record.data_version,
            },
            actor_type="user",
            actor_id=user_id,
            run_id=record.run_id,
        )
        self._repo.create_review_action(
            user_id=user_id,
            task_id=record.task_id,
            record_id=record.id,
            action_type=action_type,
            review_type=prev_review_type,
            review_reason=prev_review_reason,
            batch_operation_id=batch_operation_id,
            reason=reason,
            reviewed_by=user_id,
        )

    def _apply_edits(self, *, user_id: int, record: Record, edits: list[FieldEdit]) -> None:
        current = dict(record.payload)
        for edit in edits:
            original = current.get(edit.field_name)
            self._repo.create_override(
                user_id=user_id,
                task_id=record.task_id,
                record_id=record.id,
                field_name=edit.field_name,
                original_value=str(original) if original is not None else None,
                final_value=edit.final_value,
                modified_by=user_id,
            )
        record.data_version += 1
        append_domain_event(
            self._db,
            user_id=user_id,
            aggregate_type="record",
            aggregate_id=record.id,
            aggregate_version=record.data_version,
            event_type="record.edited",
            payload={
                "task_id": record.task_id,
                "fields": [e.field_name for e in edits],
                "data_version": record.data_version,
            },
            actor_type="user",
            actor_id=user_id,
            run_id=record.run_id,
        )
        self._repo.create_review_action(
            user_id=user_id,
            task_id=record.task_id,
            record_id=record.id,
            action_type="edit",
            review_type=record.review_type,
            review_reason=record.review_reason,
            batch_operation_id=None,
            reason=None,
            reviewed_by=user_id,
            detail={"fields": [e.field_name for e in edits]},
        )
