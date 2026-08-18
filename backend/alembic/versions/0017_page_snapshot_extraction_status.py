"""Add page_snapshots.extraction_status (M-11 extraction failure ledger).

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"


def upgrade() -> None:
    with op.batch_alter_table("page_snapshots") as batch:
        batch.add_column(sa.Column("extraction_status", sa.String(30), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("page_snapshots") as batch:
        batch.drop_column("extraction_status")
