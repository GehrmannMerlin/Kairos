"""Phase 4 browser migration: real upgrade against local PostgreSQL."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


def test_browser_migration_schema_present() -> None:
    """The 0004 browser columns and table exist after `alembic upgrade head`."""

    async def _check() -> None:
        from app.db import SessionFactory

        async with SessionFactory() as db:
            tables_query = "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            tables = set((await db.execute(text(tables_query))).scalars())
            assert "browser_tasks" in tables

            cols = {
                (c.table, c.column)
                for c in (
                    await db.execute(
                        text(
                            "SELECT table_name AS table, column_name AS column "
                            "FROM information_schema.columns WHERE table_schema='public'"
                        )
                    )
                )
            }
            assert ("page_snapshots", "capture_method") in cols
            assert ("page_snapshots", "parent_snapshot_id") in cols
            assert ("page_snapshots", "screenshot_storage_key") in cols
            assert ("page_snapshots", "rendered_at") in cols
            assert ("collection_sources", "browser_required_reason") in cols
            assert ("collection_sources", "browser_task_id") in cols
            assert ("collection_spec_versions", "browser_limits") in cols
            assert ("collection_spec_versions", "browser_policy_version") in cols

            uq_query = "SELECT conname FROM pg_constraint WHERE conname='uq_browser_task_run_source'"
            uq = set((await db.execute(text(uq_query))).scalars())
            assert uq

    asyncio.run(_check())