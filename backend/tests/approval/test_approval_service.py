"""M-08 Task 5: approval lifecycle, fingerprint invalidation, expiry/revoke, owner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.approval.schemas import ApprovalScope, ApprovalState
from app.approval.service import ApprovalService
from app.domain.errors import DomainError
from app.domain.idempotency import stable_fingerprint
from app.domain.repository import TaskRepository
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'approval.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    TaskRepository(session).create(user_id=1, title="approval task")
    yield session
    session.close()


def _request(svc, *, user_id=1, task_id=1, parameters=None, expires_at=None, scope=None):
    return svc.request_approval(
        user_id=user_id,
        task_id=task_id,
        spec_version=1,
        plan_version=1,
        node_id="n1",
        node_type="fetch",
        action_type="fetch_non_public",
        target="https://example.com/private/{id}",
        parameters=parameters or {"non_public": True},
        scope=scope or ApprovalScope.THIS_ACTION,
        expires_at=expires_at,
    )


def test_high_risk_node_creates_pending_approval(db) -> None:
    svc = ApprovalService(db)
    approval = _request(svc)
    assert approval.state == ApprovalState.PENDING
    assert approval.parameter_fingerprint == stable_fingerprint(
        "approval", "fetch_non_public", {"non_public": True}
    )


def test_approve_and_consume(db) -> None:
    svc = ApprovalService(db)
    approval = _request(svc)
    resolved = svc.approve(user_id=1, approval_id=approval.id, actor_id=1)
    assert resolved.state == ApprovalState.APPROVED
    consumed = svc.consume(user_id=1, approval_id=approval.id, parameters={"non_public": True})
    assert consumed.state == ApprovalState.CONSUMED


def test_parameter_fingerprint_change_invalidates(db) -> None:
    svc = ApprovalService(db)
    approval = _request(svc)
    svc.approve(user_id=1, approval_id=approval.id, actor_id=1)
    with pytest.raises(DomainError):
        svc.consume(user_id=1, approval_id=approval.id, parameters={"non_public": False})


def test_expired_cannot_consume(db) -> None:
    svc = ApprovalService(db)
    approval = _request(svc, expires_at=datetime.now(UTC) - timedelta(minutes=1))
    with pytest.raises(DomainError):
        svc.consume(user_id=1, approval_id=approval.id, parameters={"non_public": True})


def test_revoked_cannot_consume(db) -> None:
    svc = ApprovalService(db)
    approval = _request(svc)
    svc.revoke(user_id=1, approval_id=approval.id)
    with pytest.raises(DomainError):
        svc.consume(user_id=1, approval_id=approval.id, parameters={"non_public": True})


def test_user_b_cannot_access_user_a_approval(db) -> None:
    from app.auth.errors import NotFoundError

    svc = ApprovalService(db)
    approval = _request(svc)
    with pytest.raises(NotFoundError):
        svc.get_owned(user_id=2, approval_id=approval.id)
    with pytest.raises(NotFoundError):
        svc.approve(user_id=2, approval_id=approval.id, actor_id=2)
