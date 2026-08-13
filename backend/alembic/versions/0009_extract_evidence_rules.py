"""M-11: field_evidence evidence-chain columns + immutable extractor_rules.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- field_evidence：M-11 证据链扩展（全部 expand 兼容，M-11 总是写入）---
    op.add_column("field_evidence", sa.Column("task_id", sa.BigInteger(), nullable=True))
    op.add_column("field_evidence", sa.Column("run_id", sa.BigInteger(), nullable=True))
    op.add_column("field_evidence", sa.Column("spec_version", sa.Integer(), nullable=True))
    op.add_column("field_evidence", sa.Column("url_resource_id", sa.BigInteger(), nullable=True))
    op.add_column("field_evidence", sa.Column("normalized_value", sa.Text(), nullable=True))
    op.add_column("field_evidence", sa.Column("value_type", sa.String(length=30), nullable=True))
    op.add_column(
        "field_evidence", sa.Column("source_locator", sa.String(length=500), nullable=True)
    )
    op.add_column("field_evidence", sa.Column("raw_snippet", sa.Text(), nullable=True))
    op.add_column("field_evidence", sa.Column("rule_version_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "field_evidence", sa.Column("model_config_id", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "field_evidence", sa.Column("validation_status", sa.String(length=30), nullable=True)
    )
    op.add_column("field_evidence", sa.Column("issue_code", sa.String(length=50), nullable=True))
    op.add_column("field_evidence", sa.Column("evidence_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_field_evidence_snapshot_id", "field_evidence", ["snapshot_id"])
    op.create_index("ix_field_evidence_task_id", "field_evidence", ["task_id"])
    op.create_unique_constraint(
        "uq_fe_record_field_method", "field_evidence", ["record_id", "field_name", "extract_method"]
    )

    # --- extractor_rules：不可变规则版本（D-010 / 二十）---
    op.create_table(
        "extractor_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("site_host", sa.String(length=255), nullable=False),
        sa.Column("field_name", sa.String(length=255), nullable=False),
        sa.Column("schema_identity", sa.String(length=255), nullable=True),
        sa.Column("rule_type", sa.String(length=10), nullable=False, server_default="css"),
        sa.Column("selector", sa.String(length=1000), nullable=False),
        sa.Column(
            "value_transform", sa.String(length=50), nullable=False, server_default="identity"
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("quality", sa.JSON(), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_version_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "user_id", "site_host", "field_name", "version", name="uq_er_site_field_version"
        ),
    )
    op.create_index("ix_extractor_rules_user_id", "extractor_rules", ["user_id"])
    op.create_index("ix_extractor_rules_user_site", "extractor_rules", ["user_id", "site_host"])


def downgrade() -> None:
    op.drop_index("ix_extractor_rules_user_site", table_name="extractor_rules")
    op.drop_index("ix_extractor_rules_user_id", table_name="extractor_rules")
    op.drop_table("extractor_rules")
    op.drop_constraint("uq_fe_record_field_method", "field_evidence", type_="unique")
    op.drop_index("ix_field_evidence_task_id", table_name="field_evidence")
    op.drop_index("ix_field_evidence_snapshot_id", table_name="field_evidence")
    op.drop_column("field_evidence", "evidence_hash")
    op.drop_column("field_evidence", "issue_code")
    op.drop_column("field_evidence", "validation_status")
    op.drop_column("field_evidence", "model_config_id")
    op.drop_column("field_evidence", "rule_version_id")
    op.drop_column("field_evidence", "raw_snippet")
    op.drop_column("field_evidence", "source_locator")
    op.drop_column("field_evidence", "value_type")
    op.drop_column("field_evidence", "normalized_value")
    op.drop_column("field_evidence", "url_resource_id")
    op.drop_column("field_evidence", "spec_version")
    op.drop_column("field_evidence", "run_id")
    op.drop_column("field_evidence", "task_id")
