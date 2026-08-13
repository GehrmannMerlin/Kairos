"""M-10: PageSnapshot fetch metadata + SiteFetchStrategy + URLResource fetch audit + 网站凭据列.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- PageSnapshot：immutable observation 的 fetch metadata（全部 expand 兼容）---
    op.add_column(
        "page_snapshots",
        sa.Column("spec_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "page_snapshots",
        sa.Column("tool", sa.String(length=30), nullable=False, server_default="http"),
    )
    op.add_column(
        "page_snapshots",
        sa.Column(
            "tool_version", sa.String(length=30), nullable=False, server_default="unknown"
        ),
    )
    op.add_column(
        "page_snapshots",
        sa.Column("final_url", sa.String(length=2048), nullable=False, server_default=""),
    )
    op.add_column("page_snapshots", sa.Column("http_status", sa.Integer(), nullable=True))
    op.add_column("page_snapshots", sa.Column("content_length", sa.Integer(), nullable=True))
    op.add_column("page_snapshots", sa.Column("download_bytes", sa.Integer(), nullable=True))
    op.add_column("page_snapshots", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column("page_snapshots", sa.Column("redirect_summary", sa.JSON(), nullable=True))
    op.add_column("page_snapshots", sa.Column("escalation_evidence", sa.JSON(), nullable=True))
    op.add_column(
        "page_snapshots",
        sa.Column("snapshot_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("page_snapshots", sa.Column("prior_snapshot_id", sa.BigInteger(), nullable=True))
    op.add_column("page_snapshots", sa.Column("credential_ref", sa.JSON(), nullable=True))
    op.add_column("page_snapshots", sa.Column("http_metadata", sa.JSON(), nullable=True))

    # --- URLResource：fetch 审计（M-10 同一状态机，migration 0008）---
    op.add_column(
        "url_resources", sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "url_resources",
        sa.Column("fetch_error_code", sa.String(length=50), nullable=True),
    )

    # --- site_fetch_strategies：站点级成功抓取策略（D-009 策略复用 / D-023 owner-safe）---
    op.create_table(
        "site_fetch_strategies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("site_host", sa.String(length=255), nullable=False),
        sa.Column(
            "preferred_tier", sa.String(length=20), nullable=False, server_default="static"
        ),
        sa.Column("tool", sa.String(length=30), nullable=False, server_default="http"),
        sa.Column(
            "tool_version", sa.String(length=30), nullable=False, server_default="unknown"
        ),
        sa.Column("structure_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "credential_required", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("credential_type", sa.String(length=30), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="probing"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("user_id", "site_host", name="uq_sfs_user_site"),
    )
    op.create_index("ix_site_fetch_strategies_user_id", "site_fetch_strategies", ["user_id"])

    # --- credentials：网站凭据元数据（Cookie / Username-Password；Secret 仍在 vault 密文）---
    op.add_column("credentials", sa.Column("domain", sa.String(length=255), nullable=True))
    op.add_column("credentials", sa.Column("scope", sa.String(length=20), nullable=True))
    op.add_column("credentials", sa.Column("task_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_credentials_domain", "credentials", ["domain"])


def downgrade() -> None:
    op.drop_index("ix_credentials_domain", table_name="credentials")
    op.drop_column("credentials", "task_id")
    op.drop_column("credentials", "scope")
    op.drop_column("credentials", "domain")
    op.drop_index("ix_site_fetch_strategies_user_id", table_name="site_fetch_strategies")
    op.drop_table("site_fetch_strategies")
    op.drop_column("url_resources", "fetch_error_code")
    op.drop_column("url_resources", "fetched_at")
    op.drop_column("page_snapshots", "http_metadata")
    op.drop_column("page_snapshots", "credential_ref")
    op.drop_column("page_snapshots", "prior_snapshot_id")
    op.drop_column("page_snapshots", "snapshot_version")
    op.drop_column("page_snapshots", "escalation_evidence")
    op.drop_column("page_snapshots", "redirect_summary")
    op.drop_column("page_snapshots", "duration_ms")
    op.drop_column("page_snapshots", "download_bytes")
    op.drop_column("page_snapshots", "content_length")
    op.drop_column("page_snapshots", "http_status")
    op.drop_column("page_snapshots", "final_url")
    op.drop_column("page_snapshots", "tool_version")
    op.drop_column("page_snapshots", "tool")
    op.drop_column("page_snapshots", "spec_version")
