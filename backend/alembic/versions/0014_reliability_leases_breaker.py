"""M-16 reliability: resource leases, domain circuit breaker, artifact ready fp index.

Additive only（expand）: 新增两张协调表 + artifacts 一个 partial unique index。
不触碰 M-07 Run / M-09 URLResource / M-13 Record / M-15 Artifact 既有列。
resource_leases 承载 D-071 三级调度跨进程协调；domain_circuit_breakers 承载
域名熔断持久状态；artifacts partial unique index 加固 M-15 并发导出幂等。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"


def upgrade() -> None:
    op.create_table(
        "resource_leases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope", sa.String(30), nullable=False),
        sa.Column("scope_key", sa.String(128), nullable=False),
        sa.Column("holder_type", sa.String(30), nullable=False),
        sa.Column("holder_id", sa.String(128), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("resource_class", sa.String(30), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="active"),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_leases_scope_key", "resource_leases", ["scope", "scope_key"])
    op.create_index("ix_leases_state_expires", "resource_leases", ["state", "expires_at"])

    op.create_table(
        "domain_circuit_breakers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="CLOSED"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_class", sa.String(50), nullable=True),
        sa.Column("open_reason", sa.String(500), nullable=True),
        sa.Column("open_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("half_open_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "half_open_probe_claimed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_unique_constraint("uq_dcb_domain", "domain_circuit_breakers", ["domain"])

    op.create_index(
        "ix_artifacts_user_task_fp_ready",
        "artifacts",
        ["user_id", "task_id", "request_fingerprint"],
        unique=True,
        postgresql_where=sa.text("status = 'ready'"),
        sqlite_where=sa.text("status = 'ready'"),
    )


def downgrade() -> None:
    op.drop_index("ix_artifacts_user_task_fp_ready", table_name="artifacts")
    op.drop_table("domain_circuit_breakers")
    op.drop_table("resource_leases")
