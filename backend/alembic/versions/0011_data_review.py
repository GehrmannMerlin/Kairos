"""M-13 data review: records.data_version + field overrides + review audit.

Part of DEPLOY-GATE-3 fast development path — additive, expand-only.
"""

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "records",
        sa.Column("data_version", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "record_field_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_name", sa.String(255), nullable=False),
        sa.Column("original_value", sa.Text(), nullable=True),
        sa.Column("final_value", sa.Text(), nullable=True),
        sa.Column("value_source", sa.String(30), nullable=False, server_default="USER_OVERRIDE"),
        sa.Column("modified_by", sa.Integer(), nullable=False),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("record_id", "field_name", name="uq_rfo_record_field"),
    )
    op.create_index("ix_rfo_user_record", "record_field_overrides", ["user_id", "record_id"])

    op.create_table(
        "record_review_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_type", sa.String(30), nullable=False),
        sa.Column("review_type", sa.String(50), nullable=True),
        sa.Column("review_reason", sa.String(50), nullable=True),
        sa.Column("batch_operation_id", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("detail", sa.JSON(), nullable=True),
    )
    op.create_index("ix_rra_user_record", "record_review_actions", ["user_id", "record_id"])


def downgrade() -> None:
    op.drop_index("ix_rra_user_record", table_name="record_review_actions")
    op.drop_table("record_review_actions")
    op.drop_index("ix_rfo_user_record", table_name="record_field_overrides")
    op.drop_table("record_field_overrides")
    op.drop_column("records", "data_version")
