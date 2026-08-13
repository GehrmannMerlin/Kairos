"""M-15: artifact lifecycle columns + task.restore_state.

Expand-only, additive. release_head migration for M-15.
"""

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("restore_state", sa.String(30), nullable=True))
    op.add_column("artifacts", sa.Column("request_fingerprint", sa.String(64), nullable=True))
    op.add_column("artifacts", sa.Column("schema_version", sa.String(50), nullable=True))
    op.add_column(
        "artifacts", sa.Column("row_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("artifacts", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("artifacts", sa.Column("filename", sa.String(255), nullable=True))
    op.add_column(
        "artifacts", sa.Column("status", sa.String(20), nullable=False, server_default="ready")
    )
    op.create_index(
        "ix_artifacts_user_task_fp", "artifacts", ["user_id", "task_id", "request_fingerprint"]
    )


def downgrade() -> None:
    op.drop_index("ix_artifacts_user_task_fp", table_name="artifacts")
    for col in (
        "status",
        "filename",
        "size_bytes",
        "row_count",
        "schema_version",
        "request_fingerprint",
    ):
        op.drop_column("artifacts", col)
    op.drop_column("tasks", "restore_state")
