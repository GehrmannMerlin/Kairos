"""M-17 ops-health DB/业务指标。在 api 容器内运行，连接当前环境 DB。

输出 JSON：
{
  "waiting_resource_24h": int,
  "active_leases": int,
  "node_failures_24h": int,
  "generated_at": iso
}
机器可读，供 ops-health.sh / staging acceptance 消费。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.domain.models import DomainEvent, ResourceLease
from app.infra.deps import get_session_factory


def main() -> None:
    session = get_session_factory()()
    since = datetime.now(UTC) - timedelta(hours=24)
    try:
        waiting = session.execute(
            select(func.count())
            .select_from(DomainEvent)
            .where(
                DomainEvent.aggregate_type == "task",
                DomainEvent.event_type == "task.resource_waiting",
                DomainEvent.occurred_at >= since,
            )
        ).scalar_one()
        failures = session.execute(
            select(func.count())
            .select_from(DomainEvent)
            .where(
                DomainEvent.event_type.ilike("%failed"),
                DomainEvent.occurred_at >= since,
            )
        ).scalar_one()
        leases = session.execute(
            select(func.count()).select_from(ResourceLease)
        ).scalar_one()
        print(
            json.dumps(
                {
                    "waiting_resource_24h": waiting,
                    "node_failures_24h": failures,
                    "active_leases": leases,
                    "generated_at": datetime.now(UTC).isoformat(),
                }
            )
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
