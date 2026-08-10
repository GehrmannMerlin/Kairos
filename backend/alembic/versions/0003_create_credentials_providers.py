"""create credentials, credential_versions, model_configs, search_configs

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-10

M-03 baseline: envelope-encrypted credential vault + versioned provider configs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "credentials",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.Index("ix_credentials_user_id", "user_id"),
    )
    op.create_table(
        "credential_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("credential_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("algorithm", sa.String(length=20), nullable=False),
        sa.Column("key_version", sa.String(length=20), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=16), nullable=False),
        sa.Column("wrapped_dek_nonce", sa.LargeBinary(length=16), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("wrapped_dek", sa.LargeBinary(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("credential_id", "version", name="uq_cv_cred_version"),
        sa.Index("ix_credential_versions_credential_id", "credential_id"),
    )
    op.create_table(
        "model_configs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("config_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("provider_type", sa.String(length=50), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=True),
        sa.Column("credential_version_id", sa.BigInteger(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "connection_status", sa.String(length=30), nullable=False, server_default="untested"
        ),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["credential_version_id"], ["credential_versions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("config_id", "version", name="uq_mc_config_version"),
        sa.Index("ix_model_configs_config_id", "config_id"),
        sa.Index("ix_model_configs_user_id", "user_id"),
    )
    op.create_table(
        "search_configs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("config_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("provider_type", sa.String(length=50), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=True),
        sa.Column("credential_version_id", sa.BigInteger(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "connection_status", sa.String(length=30), nullable=False, server_default="untested"
        ),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["credential_version_id"], ["credential_versions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("config_id", "version", name="uq_sc_config_version"),
        sa.Index("ix_search_configs_config_id", "config_id"),
        sa.Index("ix_search_configs_user_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("search_configs")
    op.drop_table("model_configs")
    op.drop_table("credential_versions")
    op.drop_table("credentials")
