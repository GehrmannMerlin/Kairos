"""Add Phase 4 browser escalation domain: BrowserTask table and browser snapshot/source fields."""

import sqlalchemy as sa
from alembic import op

revision = "0004_phase4_browser_escalation"
down_revision = "0003_phase3b_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CollectionSpecVersion: freeze resolved browser limits and a policy version at confirm time,
    # so a mid-run server config change cannot move the BrowserTask budget (phase4 constraint).
    op.add_column(
        "collection_spec_versions",
        sa.Column("browser_limits", sa.JSON(), nullable=True),
    )
    op.add_column(
        "collection_spec_versions",
        sa.Column(
            "browser_policy_version",
            sa.String(64),
            server_default="browser-policy-v1",
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE collection_spec_versions SET browser_limits = json_build_object("
            "'max_browser_tasks_per_run', 5, 'max_steps_per_task', 20, "
            "'task_timeout_seconds', 120, 'max_navigation_count', 5, 'max_action_events', 30) "
            "WHERE browser_limits IS NULL"
        )
    )
    op.alter_column("collection_spec_versions", "browser_limits", nullable=False)

    # CollectionSource: BROWSER_REQUIRED state support and the claiming BrowserTask pointer.
    op.add_column(
        "collection_sources",
        sa.Column("browser_required_reason", sa.String(32), nullable=True),
    )
    op.add_column(
        "collection_sources",
        sa.Column("browser_task_id", sa.String(64), nullable=True),
    )

    # PageSnapshot: capture provenance for HTTP vs Browser, optional parent HTTP snapshot,
    # MinIO screenshot key, and rendered-at timestamp for browser snapshots.
    op.add_column(
        "page_snapshots",
        sa.Column("capture_method", sa.String(16), server_default="HTTP", nullable=False),
    )
    op.add_column(
        "page_snapshots",
        sa.Column("parent_snapshot_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "page_snapshots",
        sa.Column("screenshot_storage_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "page_snapshots",
        sa.Column("rendered_at", sa.DateTime(timezone=True), nullable=True),
    )

    # BrowserTask: one durable row per (task_run_id, source_id). Stores only metadata —
    # never a live browser handle, CDP pointer, tab object, locator, or browser pid.
    op.create_table(
        "browser_tasks",
        sa.Column("browser_task_id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("task_id", sa.String(64), sa.ForeignKey("tasks.task_id"), nullable=False),
        sa.Column("task_run_id", sa.String(64), sa.ForeignKey("task_runs.task_run_id"), nullable=False),
        sa.Column(
            "spec_version_id",
            sa.String(64),
            sa.ForeignKey("collection_spec_versions.spec_version_id"),
            nullable=False,
        ),
        sa.Column(
            "source_id", sa.String(64), sa.ForeignKey("collection_sources.source_id"), nullable=False
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("snapshot_id", sa.String(64), nullable=True),
        sa.Column(
            "policy_version",
            sa.String(64),
            nullable=False,
            server_default="browser-policy-v1",
        ),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("failure_message", sa.String(500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("task_run_id", "source_id", name="uq_browser_task_run_source"),
    )
    for column in ("owner_id", "task_id", "task_run_id", "spec_version_id", "source_id", "status"):
        op.create_index(f"ix_browser_tasks_{column}", "browser_tasks", [column])


def downgrade() -> None:
    op.drop_table("browser_tasks")
    op.drop_column("page_snapshots", "rendered_at")
    op.drop_column("page_snapshots", "screenshot_storage_key")
    op.drop_column("page_snapshots", "parent_snapshot_id")
    op.drop_column("page_snapshots", "capture_method")
    op.drop_column("collection_sources", "browser_task_id")
    op.drop_column("collection_sources", "browser_required_reason")
    op.drop_column("collection_spec_versions", "browser_policy_version")
    op.drop_column("collection_spec_versions", "browser_limits")