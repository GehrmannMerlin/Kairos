"""M-17 trace/correlation 诊断：给定 task_id 打印 Task → Run → Node → Event → Artifact 关联链。

用法（api 容器内）：python ops_trace.py <task_id>
按时间序打印 DomainEvent（含 trace_ref / run_id / node_run_id），并列出 Run / NodeRun / Artifact。
这是运维诊断能力（D-024 开发诊断追踪），不是新的用户页面。
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.domain.models import Artifact, DomainEvent, NodeRun, Run, Task
from app.infra.deps import get_session_factory


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: ops_trace.py <task_id>")
        sys.exit(2)
    try:
        task_id = int(sys.argv[1])
    except ValueError:
        print(f"task_id must be an integer, got {sys.argv[1]!r}")
        sys.exit(2)

    session = get_session_factory()()
    try:
        task = session.get(Task, task_id)
        print(f"task: {task_id} state={task.state if task else 'MISSING'} title={getattr(task, 'title', '')!r}")

        runs = session.scalars(select(Run).where(Run.task_id == task_id).order_by(Run.id)).all()
        for run in runs:
            print(f"  run: {run.id} status={run.status}")
        nodes = session.scalars(
            select(NodeRun).where(NodeRun.task_id == task_id).order_by(NodeRun.id)
        ).all()
        for node in nodes:
            print(f"  node: {node.id} run={node.run_id} status={getattr(node, 'status', '?')} node_type={getattr(node, 'node_type', '?')}")
        events = session.scalars(
            select(DomainEvent)
            .where(DomainEvent.aggregate_type == "task", DomainEvent.aggregate_id == task_id)
            .order_by(DomainEvent.occurred_at)
        ).all()
        for ev in events:
            trace = f" trace={ev.payload.get('trace_id')}" if ev.payload and ev.payload.get("trace_id") else ""
            print(
                f"  event: {ev.occurred_at.isoformat()} {ev.event_type} "
                f"run={ev.run_id or '-'} node={ev.node_run_id or '-'}{trace}"
            )
        artifacts = session.scalars(select(Artifact).where(Artifact.task_id == task_id)).all()
        for art in artifacts:
            print(
                f"  artifact: {art.id} export={getattr(art, 'export_type', '?')} "
                f"dataset_version={art.dataset_version} content_hash={art.content_hash}"
            )
    finally:
        session.close()


if __name__ == "__main__":
    main()
