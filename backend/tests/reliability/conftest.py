"""M-16 reliability scoped 测试基座：SQLite + 两用户。"""

from __future__ import annotations

import pytest
from app.auth.models import User  # noqa: F401  注册 users 表
from app.domain.models import DomainCircuitBreaker, ResourceLease  # noqa: F401  注册新表
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    yield session
    session.close()


@pytest.fixture()
def users(db: Session):
    a = User(email="a@kairos.test", password_hash="x")
    b = User(email="b@kairos.test", password_hash="x")
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)
    return a, b
