"""Minimal repository for the M-01 smoke probe."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage.models import SmokeProbe


def create_smoke_probe(session: Session, workflow_id: str, message: str) -> SmokeProbe:
    probe = SmokeProbe(workflow_id=workflow_id, message=message)
    session.add(probe)
    session.commit()
    session.refresh(probe)
    return probe


def get_smoke_probe(session: Session, probe_id: int) -> SmokeProbe | None:
    return session.scalar(select(SmokeProbe).where(SmokeProbe.id == probe_id))
