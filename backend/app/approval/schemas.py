"""Canonical Approval state / scope vocabulary (M-08 / D-017).

单一事实来源：状态与授权粒度只有这一组 enum，禁止散落 boolean requires_approval
或重复的 WAITING_CONFIRMATION / BLOCK_APPROVAL 等第二套语义。
"""

from __future__ import annotations

from enum import StrEnum


class ApprovalState(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class ApprovalScope(StrEnum):
    THIS_ACTION = "this_action"
    SAME_PARAMETERS_BATCH = "same_parameters_batch"
    TASK_SCOPED_LIMITED = "task_scoped_limited"
