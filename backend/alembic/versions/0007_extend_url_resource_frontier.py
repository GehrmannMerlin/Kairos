"""Extend url_resources with M-09 discovery/frontier metadata.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "url_resources",
        sa.Column("spec_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "url_resources",
        sa.Column("discovery_source", sa.String(length=40), nullable=False, server_default="USER_SEED"),
    )
    op.add_column("url_resources", sa.Column("parent_url_hash", sa.String(length=64), nullable=True))
    op.add_column("url_resources", sa.Column("depth", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("url_resources", sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("url_resources", sa.Column("discovery_count", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("url_resources", sa.Column("discovery_evidence", sa.JSON(), nullable=True))
    op.add_column("url_resources", sa.Column("robots_allowed", sa.Boolean(), nullable=True))
    op.add_column("url_resources", sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=True))
    # frontier state 默认：DISCOVERED（M-09 是 url_resources 首个真实消费者，无历史行）
    op.alter_column("url_resources", "status", server_default="DISCOVERED")
    op.create_index("ix_url_resources_state", "url_resources", ["task_id", "status"])
    op.create_index("ix_url_resources_parent", "url_resources", ["parent_url_hash"])


def downgrade() -> None:
    op.drop_index("ix_url_resources_parent", table_name="url_resources")
    op.drop_index("ix_url_resources_state", table_name="url_resources")
    op.alter_column("url_resources", "status", server_default="pending")
    op.drop_column("url_resources", "accessed_at")
    op.drop_column("url_resources", "robots_allowed")
    op.drop_column("url_resources", "discovery_evidence")
    op.drop_column("url_resources", "discovery_count")
    op.drop_column("url_resources", "priority")
    op.drop_column("url_resources", "depth")
    op.drop_column("url_resources", "parent_url_hash")
    op.drop_column("url_resources", "discovery_source")
    op.drop_column("url_resources", "spec_version")
