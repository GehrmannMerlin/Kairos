"""extend plan_versions and approvals for M-08

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-11

M-08 增量扩展（expand/contract，兼容旧行）：
- plan_versions 增加 parent_plan_version_id / validation_status / plan_fingerprint /
  model_config_id / model_config_version / registry_versions / generation_policy /
  trigger_reason / replan_evidence_refs / diff_summary。
- approvals 增加 plan_version / node_id / node_type / target / approved_scope /
  credential_ref / status_payload / resolved_by / consumed_at。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plan_versions", sa.Column("parent_plan_version_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "plan_versions",
        sa.Column("validation_status", sa.String(length=30), nullable=False, server_default="pending"),
    )
    op.add_column(
        "plan_versions",
        sa.Column("plan_fingerprint", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column("plan_versions", sa.Column("model_config_id", sa.String(length=32), nullable=True))
    op.add_column("plan_versions", sa.Column("model_config_version", sa.Integer(), nullable=True))
    op.add_column(
        "plan_versions",
        sa.Column(
            "registry_versions",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.add_column(
        "plan_versions",
        sa.Column("generation_policy", sa.String(length=30), nullable=False, server_default="auto"),
    )
    op.add_column("plan_versions", sa.Column("trigger_reason", sa.String(length=500), nullable=True))
    op.add_column("plan_versions", sa.Column("replan_evidence_refs", sa.JSON(), nullable=True))
    op.add_column("plan_versions", sa.Column("diff_summary", sa.JSON(), nullable=True))

    op.add_column("approvals", sa.Column("plan_version", sa.Integer(), nullable=True))
    op.add_column("approvals", sa.Column("node_id", sa.String(length=50), nullable=True))
    op.add_column("approvals", sa.Column("node_type", sa.String(length=50), nullable=True))
    op.add_column("approvals", sa.Column("target", sa.String(length=500), nullable=True))
    op.add_column(
        "approvals",
        sa.Column("approved_scope", sa.String(length=30), nullable=False, server_default="this_action"),
    )
    op.add_column("approvals", sa.Column("credential_ref", sa.JSON(), nullable=True))
    op.add_column("approvals", sa.Column("status_payload", sa.JSON(), nullable=True))
    op.add_column("approvals", sa.Column("resolved_by", sa.BigInteger(), nullable=True))
    op.add_column("approvals", sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("approvals", "consumed_at")
    op.drop_column("approvals", "resolved_by")
    op.drop_column("approvals", "status_payload")
    op.drop_column("approvals", "credential_ref")
    op.drop_column("approvals", "approved_scope")
    op.drop_column("approvals", "target")
    op.drop_column("approvals", "node_type")
    op.drop_column("approvals", "node_id")
    op.drop_column("approvals", "plan_version")

    op.drop_column("plan_versions", "diff_summary")
    op.drop_column("plan_versions", "replan_evidence_refs")
    op.drop_column("plan_versions", "trigger_reason")
    op.drop_column("plan_versions", "generation_policy")
    op.drop_column("plan_versions", "registry_versions")
    op.drop_column("plan_versions", "model_config_version")
    op.drop_column("plan_versions", "model_config_id")
    op.drop_column("plan_versions", "plan_fingerprint")
    op.drop_column("plan_versions", "validation_status")
    op.drop_column("plan_versions", "parent_plan_version_id")
