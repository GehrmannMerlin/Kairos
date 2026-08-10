"""create smoke_probe

Revision ID: 0001
Revises:
Create Date: 2026-08-10

M-01 baseline schema: a single minimal table proving Migration → PostgreSQL →
application access. Real domain schema arrives with M-02+.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "smoke_probe",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("workflow_id", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Index("ix_smoke_probe_workflow_id", "workflow_id"),
    )


def downgrade() -> None:
    op.drop_table("smoke_probe")
