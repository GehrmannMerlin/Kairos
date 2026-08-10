"""create chat, spec draft and template persistence

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-10

M-06 baseline: append-only ChatMessage, per-task CollectionSpecDraft, versioned
CollectionTemplate, plus Task template references. tasks.task_type becomes
nullable (a fresh Draft has no canonical type until Goal Understanding resolves
it to EXPLORATORY / SPECIFIED_SOURCE / HYBRID). All new user-business tables
carry user_id NOT NULL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("ref_type", sa.String(length=30), nullable=True),
        sa.Column("ref_id", sa.BigInteger(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.Index("ix_chat_messages_task_id", "task_id"),
        sa.Index("ix_chat_messages_user_id", "user_id"),
    )
    op.create_table(
        "collection_spec_drafts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("task_id", name="uq_csd_task"),
        sa.Index("ix_collection_spec_drafts_task_id", "task_id"),
        sa.Index("ix_collection_spec_drafts_user_id", "user_id"),
    )
    op.create_table(
        "collection_templates",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("template_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("task_type", sa.String(length=30), nullable=False),
        sa.Column("goal_template", sa.Text(), nullable=False),
        sa.Column("variables", sa.JSON(), nullable=False),
        sa.Column("field_schema", sa.JSON(), nullable=False),
        sa.Column("completion_conditions", sa.JSON(), nullable=False),
        sa.Column("advanced_settings", sa.JSON(), nullable=False),
        sa.Column("field_expansion", sa.JSON(), nullable=False),
        sa.Column("default_model_config_ref", sa.JSON(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("template_id", "version", name="uq_ct_template_version"),
        sa.Index("ix_collection_templates_template_id", "template_id"),
        sa.Index("ix_collection_templates_user_id", "user_id"),
    )
    op.add_column("tasks", sa.Column("template_id", sa.String(length=32), nullable=True))
    op.add_column("tasks", sa.Column("template_version", sa.Integer(), nullable=True))
    op.alter_column("tasks", "task_type", existing_type=sa.String(length=30), nullable=True)


def downgrade() -> None:
    op.alter_column("tasks", "task_type", existing_type=sa.String(length=30), nullable=False)
    op.drop_column("tasks", "template_version")
    op.drop_column("tasks", "template_id")
    op.drop_table("collection_templates")
    op.drop_table("collection_spec_drafts")
    op.drop_table("chat_messages")
