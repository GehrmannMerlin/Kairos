"""ORM models.

M-01 only introduces the minimal ``SmokeProbe`` table used to prove that
Migration → PostgreSQL → application access works. Real domain tables
(User, Task, Spec, ...) belong to M-02+.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class SmokeProbe(Base):
    """Row written by the M-01 integration smoke chain."""

    __tablename__ = "smoke_probe"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
