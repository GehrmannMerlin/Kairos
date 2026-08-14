"""M-06 Goal Understanding idempotency: understanding_attempts.

Additive only（expand）: 新增一张 attempt 表，不触碰既有列。
身份 = (task_id, source_message_id, input_fingerprint)；partial unique index 仅对
status='running' 生效，作为跨 API 进程的并发兜底——相同输入同时两个 /understand，
只有一个能真正调用 Provider，第二个返回 IN_PROGRESS。审计只存安全 metadata。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"


def upgrade() -> None:
    op.create_table(
        "understanding_attempts",
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
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("trigger_source", sa.String(30), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("model_config_id", sa.String(32), nullable=True),
        sa.Column("model_config_version", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(30), nullable=True),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("result_ref_message_id", sa.BigInteger(), nullable=True),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("spec_draft_payload", sa.JSON(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_understanding_attempts_identity",
        "understanding_attempts",
        ["task_id", "source_message_id", "input_fingerprint"],
    )
    op.create_index(
        "ix_understanding_attempts_running",
        "understanding_attempts",
        ["task_id", "source_message_id", "input_fingerprint"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
        sqlite_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("ix_understanding_attempts_running", table_name="understanding_attempts")
    op.drop_index("ix_understanding_attempts_identity", table_name="understanding_attempts")
    op.drop_table("understanding_attempts")
