"""Add Phase 3B discovery rounds and deterministic record identity metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0003_phase3b_discovery"
down_revision = "0002_phase3a_collection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "collection_spec_versions",
        sa.Column("scope_domains", sa.JSON(), nullable=True),
    )
    op.add_column(
        "collection_spec_versions",
        sa.Column("search_limits", sa.JSON(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE collection_spec_versions SET scope_domains = json_build_array() "
            "WHERE scope_domains IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE collection_spec_versions SET search_limits = json_build_object("
            "'max_search_rounds', 5, 'max_results_per_round', 10, "
            "'max_discovered_sources', 50, 'max_processed_sources', 30) "
            "WHERE search_limits IS NULL"
        )
    )
    op.alter_column("collection_spec_versions", "scope_domains", nullable=False)
    op.alter_column("collection_spec_versions", "search_limits", nullable=False)

    op.create_table(
        "search_rounds",
        sa.Column("search_round_id", sa.String(length=64), primary_key=True),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=64), sa.ForeignKey("tasks.task_id"), nullable=False),
        sa.Column(
            "task_run_id", sa.String(length=64), sa.ForeignKey("task_runs.task_run_id"), nullable=False
        ),
        sa.Column(
            "spec_version_id",
            sa.String(length=64),
            sa.ForeignKey("collection_spec_versions.spec_version_id"),
            nullable=False,
        ),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("requested_results", sa.Integer(), nullable=False),
        sa.Column("returned_results", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted_results", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_sources", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed_records_before", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed_records_after", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_passed_records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="RUNNING"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("task_run_id", "round_number", name="uq_search_round_run_number"),
    )
    for column in ("owner_id", "task_id", "task_run_id", "spec_version_id", "query_hash", "status"):
        op.create_index(f"ix_search_rounds_{column}", "search_rounds", [column])

    op.add_column(
        "collection_sources",
        sa.Column(
            "search_round_id",
            sa.String(length=64),
            sa.ForeignKey("search_rounds.search_round_id"),
            nullable=True,
        ),
    )
    op.add_column("collection_sources", sa.Column("discovered_query", sa.Text(), nullable=True))
    op.add_column("collection_sources", sa.Column("provider_rank", sa.Integer(), nullable=True))
    op.add_column("collection_sources", sa.Column("provider_score", sa.Float(), nullable=True))
    op.add_column("collection_sources", sa.Column("search_snippet", sa.Text(), nullable=True))
    op.add_column(
        "collection_sources",
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.add_column(
        "collection_sources", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "collection_sources", sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=True)
    )
    op.execute("UPDATE collection_sources SET first_seen_at = created_at WHERE first_seen_at IS NULL")
    op.execute("UPDATE collection_sources SET attempt_count = 0 WHERE attempt_count IS NULL")
    op.alter_column("collection_sources", "first_seen_at", nullable=False)
    op.alter_column("collection_sources", "attempt_count", nullable=False)
    op.create_index("ix_collection_sources_search_round_id", "collection_sources", ["search_round_id"])

    op.add_column("records", sa.Column("normalized_data_json", sa.JSON(), nullable=True))
    op.add_column("records", sa.Column("record_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("records", sa.Column("identity_key", sa.Text(), nullable=True))
    op.add_column(
        "records",
        sa.Column(
            "canonical_record_id", sa.String(length=64), sa.ForeignKey("records.record_id"), nullable=True
        ),
    )
    op.execute("UPDATE records SET normalized_data_json = data_json WHERE normalized_data_json IS NULL")
    op.execute("UPDATE records SET record_fingerprint = '' WHERE record_fingerprint IS NULL")
    op.execute("UPDATE records SET identity_key = '' WHERE identity_key IS NULL")
    op.alter_column("records", "normalized_data_json", nullable=False)
    op.alter_column("records", "record_fingerprint", nullable=False)
    op.alter_column("records", "identity_key", nullable=False)
    op.create_index("ix_records_record_fingerprint", "records", ["record_fingerprint"])
    op.create_index("ix_records_identity_key", "records", ["identity_key"])
    op.create_index("ix_records_canonical_record_id", "records", ["canonical_record_id"])


def downgrade() -> None:
    op.drop_index("ix_records_canonical_record_id", table_name="records")
    op.drop_index("ix_records_identity_key", table_name="records")
    op.drop_index("ix_records_record_fingerprint", table_name="records")
    op.drop_column("records", "canonical_record_id")
    op.drop_column("records", "identity_key")
    op.drop_column("records", "record_fingerprint")
    op.drop_column("records", "normalized_data_json")

    op.drop_index("ix_collection_sources_search_round_id", table_name="collection_sources")
    op.drop_column("collection_sources", "attempt_count")
    op.drop_column("collection_sources", "last_attempt_at")
    op.drop_column("collection_sources", "first_seen_at")
    op.drop_column("collection_sources", "search_snippet")
    op.drop_column("collection_sources", "provider_score")
    op.drop_column("collection_sources", "provider_rank")
    op.drop_column("collection_sources", "discovered_query")
    op.drop_column("collection_sources", "search_round_id")

    for column in ("status", "query_hash", "spec_version_id", "task_run_id", "task_id", "owner_id"):
        op.drop_index(f"ix_search_rounds_{column}", table_name="search_rounds")
    op.drop_table("search_rounds")
    op.drop_column("collection_spec_versions", "search_limits")
    op.drop_column("collection_spec_versions", "scope_domains")
