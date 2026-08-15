"""Persist execution preflight facts and frozen NodeRun identities.

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"


def upgrade() -> None:
    op.create_table(
        "execution_preflight_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("spec_version", sa.Integer(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("capability_manifest_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("search_config_id", sa.String(32), nullable=True),
        sa.Column("search_config_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "task_id",
            "plan_version",
            "capability_manifest_version",
            name="uq_execution_preflight_identity",
        ),
    )
    op.create_index("ix_execution_preflight_results_task_id", "execution_preflight_results", ["task_id"])
    op.create_index("ix_execution_preflight_results_user_id", "execution_preflight_results", ["user_id"])
    with op.batch_alter_table("node_runs") as batch:
        batch.add_column(sa.Column("node_id", sa.String(100), nullable=True))
        batch.create_unique_constraint("uq_node_runs_run_node_id", ["run_id", "node_id"])


def downgrade() -> None:
    with op.batch_alter_table("node_runs") as batch:
        batch.drop_constraint("uq_node_runs_run_node_id", type_="unique")
        batch.drop_column("node_id")
    op.drop_index("ix_execution_preflight_results_user_id", table_name="execution_preflight_results")
    op.drop_index("ix_execution_preflight_results_task_id", table_name="execution_preflight_results")
    op.drop_table("execution_preflight_results")
