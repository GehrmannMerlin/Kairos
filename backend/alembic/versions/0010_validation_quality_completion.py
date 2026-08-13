"""M-12 validation/quality/completion tables + Record review fields.

验证、去重、冲突、质量快照、完成判定全部 owner-scoped（D-023）。Record 扩展
review_type/review_reason/validated_at（nullable，expand 兼容）。所有新表带
user_id 归属与幂等唯一约束兜底。
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("records", sa.Column("review_type", sa.String(50), nullable=True))
    op.add_column("records", sa.Column("review_reason", sa.String(50), nullable=True))
    op.add_column(
        "records", sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        "validation_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column(
            "record_id",
            sa.Integer(),
            sa.ForeignKey("records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("spec_version_id", sa.Integer(), nullable=False),
        sa.Column("validation_version", sa.String(30), nullable=False),
        sa.Column("structural_issues", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("required_field_issues", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("evidence_issues", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("business_rule_issues", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("dedupe_group_id", sa.BigInteger(), nullable=True),
        sa.Column("dedupe_result", sa.JSON(), nullable=True),
        sa.Column("conflict_result", sa.JSON(), nullable=True),
        sa.Column("partition", sa.String(30), nullable=False),
        sa.Column("review_type", sa.String(50), nullable=True),
        sa.Column("review_reason", sa.String(50), nullable=True),
        sa.Column("allowed_actions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("quality_contribution", sa.JSON(), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("record_id", "validation_version", name="uq_vr_record_version"),
    )
    op.create_index(
        "ix_vr_user_task_partition", "validation_results", ["user_id", "task_id", "partition"]
    )

    op.create_table(
        "dedupe_clusters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("spec_version", sa.Integer(), nullable=False),
        sa.Column("business_key", sa.String(500), nullable=False),
        sa.Column("business_key_fingerprint", sa.String(64), nullable=False),
        sa.Column("dedupe_policy_version", sa.String(30), nullable=False),
        sa.Column("approximate", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("record_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(30), nullable=False, server_default="grouped"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("task_id", "business_key_fingerprint", name="uq_dc_task_fp"),
    )
    op.create_index("ix_dc_user_task", "dedupe_clusters", ["user_id", "task_id"])

    op.create_table(
        "field_conflicts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "record_id",
            sa.Integer(),
            sa.ForeignKey("records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_name", sa.String(255), nullable=False),
        sa.Column("dedupe_group_id", sa.BigInteger(), nullable=True),
        sa.Column("candidate_values", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("resolution", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(30), nullable=False, server_default="unresolved"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("record_id", "field_name", "state", name="uq_fc_record_field_state"),
    )
    op.create_index("ix_fc_user_task", "field_conflicts", ["user_id", "task_id"])

    op.create_table(
        "quality_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("spec_version", sa.Integer(), nullable=False),
        sa.Column("validation_version", sa.String(30), nullable=False),
        sa.Column("dataset_version", sa.String(50), nullable=False),
        sa.Column("sampling_policy_version", sa.String(30), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("denominators", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("sample_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_qs_user_task", "quality_snapshots", ["user_id", "task_id"])

    op.create_table(
        "completion_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("spec_version", sa.Integer(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("is_partial", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("completion_type", sa.String(50), nullable=True),
        sa.Column("qualified_record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("saturation_evidence", sa.JSON(), nullable=True),
        sa.Column("runtime_limit_reason", sa.String(200), nullable=True),
        sa.Column("scope_completion_metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_cd_user_task", "completion_decisions", ["user_id", "task_id"])


def downgrade() -> None:
    op.drop_table("completion_decisions")
    op.drop_table("quality_snapshots")
    op.drop_table("field_conflicts")
    op.drop_table("dedupe_clusters")
    op.drop_table("validation_results")
    op.drop_column("records", "validated_at")
    op.drop_column("records", "review_reason")
    op.drop_column("records", "review_type")
