"""Persist search result ids and display metadata for idempotent discovery reads."""

import sqlalchemy as sa
from alembic import op

revision = "0004_phase3b_result_meta"
down_revision = "0003_phase3b_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "collection_sources",
        sa.Column("search_title", sa.String(length=1000), nullable=True),
    )
    op.add_column(
        "search_rounds",
        sa.Column("result_source_ids_json", sa.JSON(), nullable=True),
    )
    op.execute(
        "UPDATE search_rounds SET result_source_ids_json = json_build_array() "
        "WHERE result_source_ids_json IS NULL"
    )
    op.alter_column("search_rounds", "result_source_ids_json", nullable=False)


def downgrade() -> None:
    op.drop_column("search_rounds", "result_source_ids_json")
    op.drop_column("collection_sources", "search_title")
