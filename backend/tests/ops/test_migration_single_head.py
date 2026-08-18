"""Alembic revision graph must have the one expected Task 3 head."""

from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_has_exactly_one_execution_preflight_head() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_heads() == ["0017"]
