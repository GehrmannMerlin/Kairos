"""M-13 审核 allowed_actions 派生策略（D-042/D-061，后端事实驱动）。

前端 allowed_actions 唯一来源；不同 review_reason 的记录不允许无条件批量通过。
"""

from __future__ import annotations

from typing import Any

from app.domain.errors import DomainError
from app.validation.contracts import AllowedReviewAction


class BatchCompatibilityError(DomainError):
    """批量动作与记录语义不兼容。"""

    code = "BATCH_INCOMPATIBLE"
    status_code = 422


class ReviewPolicy:
    @staticmethod
    def allowed_actions(*, record: Any) -> list[str]:
        if record.partition != "needs_review":
            return []
        actions = [
            AllowedReviewAction.APPROVE.value,
            AllowedReviewAction.EDIT.value,
            AllowedReviewAction.REJECT.value,
            AllowedReviewAction.AGENT_REEVALUATE.value,
        ]
        if record.review_type == "unresolved_conflict":
            actions.append(AllowedReviewAction.RESOLVE_CONFLICT.value)
        if record.review_type == "possible_duplicate":
            actions.append(AllowedReviewAction.MERGE_DUPLICATE.value)
        return actions

    @staticmethod
    def assert_batch_compatible(*, action: str, records: list[Any]) -> None:
        """D-061：批量 approve 只对 review_reason 完全一致的记录开放。"""
        if action != "approve":
            return
        reasons = {r.review_reason for r in records}
        if len(reasons) > 1:
            raise BatchCompatibilityError("不同复核原因的记录不能无条件批量通过")
