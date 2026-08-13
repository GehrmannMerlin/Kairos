"""M-15 fix: widen artifacts.dataset_version to fit ds-<sha256> (67 chars).

Postgres enforces VARCHAR(50); M-15 dataset_version is "ds-" + 64-hex (>50),
discovered at staging export (StringDataRightTruncation). Expand-only alter.
"""

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "artifacts", "dataset_version", existing_type=sa.String(50), type_=sa.String(100), nullable=True
    )


def downgrade() -> None:
    op.alter_column(
        "artifacts", "dataset_version", existing_type=sa.String(100), type_=sa.String(50), nullable=True
    )
