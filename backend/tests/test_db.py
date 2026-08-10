"""Database layer basics, exercised against SQLite so no live service is needed."""

from __future__ import annotations

import pytest
from app.infra.db import Base, ping
from app.storage.smoke_repo import create_smoke_probe, get_smoke_probe
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture()
def session_factory(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_ping_and_repo_roundtrip(session_factory: sessionmaker[Session]) -> None:
    session = session_factory()
    try:
        assert ping(session) is True

        probe = create_smoke_probe(session, workflow_id="wf-1", message="hello")
        fetched = get_smoke_probe(session, probe.id)
        assert fetched is not None
        assert fetched.workflow_id == "wf-1"
        assert fetched.message == "hello"
        assert get_smoke_probe(session, 999_999) is None
    finally:
        session.close()
