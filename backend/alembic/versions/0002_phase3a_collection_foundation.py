"""Add Phase 3A specified-source collection persistence."""

from alembic import op
import sqlalchemy as sa

revision = "0002_phase3a_collection"
down_revision = "0001_phase02_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collection_spec_versions",
        sa.Column("spec_version_id", sa.String(length=64), primary_key=True),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=64), sa.ForeignKey("tasks.task_id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("seed_urls", sa.JSON(), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("task_id", "version", name="uq_collection_spec_task_version"),
    )
    op.create_index("ix_collection_spec_versions_owner_id", "collection_spec_versions", ["owner_id"])
    op.create_index("ix_collection_spec_versions_task_id", "collection_spec_versions", ["task_id"])

    op.add_column(
        "tasks",
        sa.Column(
            "spec_version_id",
            sa.String(length=64),
            sa.ForeignKey("collection_spec_versions.spec_version_id"),
            nullable=True,
        ),
    )
    op.create_index("ix_tasks_spec_version_id", "tasks", ["spec_version_id"])

    op.create_table(
        "collection_sources",
        sa.Column("source_id", sa.String(length=64), primary_key=True),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=64), sa.ForeignKey("tasks.task_id"), nullable=False),
        sa.Column(
            "spec_version_id",
            sa.String(length=64),
            sa.ForeignKey("collection_spec_versions.spec_version_id"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False, server_default="SEED"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "spec_version_id", "canonical_url", name="uq_collection_source_spec_canonical_url"
        ),
    )
    op.create_index("ix_collection_sources_owner_id", "collection_sources", ["owner_id"])
    op.create_index("ix_collection_sources_task_id", "collection_sources", ["task_id"])
    op.create_index("ix_collection_sources_spec_version_id", "collection_sources", ["spec_version_id"])
    op.create_index("ix_collection_sources_canonical_url", "collection_sources", ["canonical_url"])
    op.create_index("ix_collection_sources_status", "collection_sources", ["status"])

    op.create_table(
        "page_snapshots",
        sa.Column("snapshot_id", sa.String(length=64), primary_key=True),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=64), sa.ForeignKey("tasks.task_id"), nullable=False),
        sa.Column("task_run_id", sa.String(length=64), sa.ForeignKey("task_runs.task_run_id"), nullable=False),
        sa.Column("source_id", sa.String(length=64), sa.ForeignKey("collection_sources.source_id"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=1000), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_storage_key", sa.Text(), nullable=False),
        sa.Column("text_storage_key", sa.Text(), nullable=False),
        sa.Column("bytes_read", sa.Integer(), nullable=False),
        sa.Column("text_chars", sa.Integer(), nullable=False),
        sa.Column("text_preview", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_page_snapshots_owner_id", "page_snapshots", ["owner_id"])
    op.create_index("ix_page_snapshots_task_id", "page_snapshots", ["task_id"])
    op.create_index("ix_page_snapshots_task_run_id", "page_snapshots", ["task_run_id"])
    op.create_index("ix_page_snapshots_source_id", "page_snapshots", ["source_id"])
    op.create_index("ix_page_snapshots_canonical_url", "page_snapshots", ["canonical_url"])
    op.create_index("ix_page_snapshots_content_hash", "page_snapshots", ["content_hash"])
    op.create_foreign_key(
        "fk_collection_sources_snapshot_id",
        "collection_sources",
        "page_snapshots",
        ["snapshot_id"],
        ["snapshot_id"],
    )

    op.create_table(
        "extraction_commits",
        sa.Column("extraction_commit_id", sa.String(length=64), primary_key=True),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=64), sa.ForeignKey("tasks.task_id"), nullable=False),
        sa.Column("task_run_id", sa.String(length=64), sa.ForeignKey("task_runs.task_run_id"), nullable=False),
        sa.Column(
            "spec_version_id",
            sa.String(length=64),
            sa.ForeignKey("collection_spec_versions.spec_version_id"),
            nullable=False,
        ),
        sa.Column("snapshot_id", sa.String(length=64), sa.ForeignKey("page_snapshots.snapshot_id"), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("task_run_id", "snapshot_id", name="uq_extraction_commit_run_snapshot"),
    )
    op.create_index("ix_extraction_commits_owner_id", "extraction_commits", ["owner_id"])
    op.create_index("ix_extraction_commits_task_id", "extraction_commits", ["task_id"])
    op.create_index("ix_extraction_commits_task_run_id", "extraction_commits", ["task_run_id"])
    op.create_index("ix_extraction_commits_spec_version_id", "extraction_commits", ["spec_version_id"])
    op.create_index("ix_extraction_commits_snapshot_id", "extraction_commits", ["snapshot_id"])

    op.create_table(
        "records",
        sa.Column("record_id", sa.String(length=64), primary_key=True),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=64), sa.ForeignKey("tasks.task_id"), nullable=False),
        sa.Column("task_run_id", sa.String(length=64), sa.ForeignKey("task_runs.task_run_id"), nullable=False),
        sa.Column(
            "spec_version_id",
            sa.String(length=64),
            sa.ForeignKey("collection_spec_versions.spec_version_id"),
            nullable=False,
        ),
        sa.Column("snapshot_id", sa.String(length=64), sa.ForeignKey("page_snapshots.snapshot_id"), nullable=False),
        sa.Column(
            "extraction_commit_id",
            sa.String(length=64),
            sa.ForeignKey("extraction_commits.extraction_commit_id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("data_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("validation_issues", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_records_owner_id", "records", ["owner_id"])
    op.create_index("ix_records_task_id", "records", ["task_id"])
    op.create_index("ix_records_task_run_id", "records", ["task_run_id"])
    op.create_index("ix_records_spec_version_id", "records", ["spec_version_id"])
    op.create_index("ix_records_snapshot_id", "records", ["snapshot_id"])
    op.create_index("ix_records_extraction_commit_id", "records", ["extraction_commit_id"])
    op.create_index("ix_records_status", "records", ["status"])

    op.create_table(
        "field_evidence",
        sa.Column("evidence_id", sa.String(length=64), primary_key=True),
        sa.Column("record_id", sa.String(length=64), sa.ForeignKey("records.record_id"), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("snapshot_id", sa.String(length=64), sa.ForeignKey("page_snapshots.snapshot_id"), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("locator_type", sa.String(length=32), nullable=False),
        sa.Column("locator_json", sa.JSON(), nullable=False),
        sa.Column("extraction_method", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_field_evidence_record_id", "field_evidence", ["record_id"])
    op.create_index("ix_field_evidence_field_name", "field_evidence", ["field_name"])
    op.create_index("ix_field_evidence_snapshot_id", "field_evidence", ["snapshot_id"])


def downgrade() -> None:
    op.drop_table("field_evidence")
    op.drop_table("records")
    op.drop_table("extraction_commits")
    op.drop_constraint("fk_collection_sources_snapshot_id", "collection_sources", type_="foreignkey")
    op.drop_table("page_snapshots")
    op.drop_table("collection_sources")
    op.drop_index("ix_tasks_spec_version_id", table_name="tasks")
    op.drop_column("tasks", "spec_version_id")
    op.drop_table("collection_spec_versions")
