"""M-17 Restore Drill 只验证 5 项（不重新 Search/Crawl/LLM/Workflow）。

在恢复后的 drill api 容器内运行，连接恢复后的 PG + MinIO：
1) Task 可查询
2) Record count 与 backup manifest 记录一致（EXPECTED_RECORDS 环境变量）
3) 一条 FieldEvidence 可读取
4) 一个 PageSnapshot 内容 sha256 与 content_hash 一致
5) 一个 formal CSV Artifact 可下载且 row count / content_hash 正确
"""

from __future__ import annotations

import hashlib
import os
import sys

from sqlalchemy import func, select

from app.domain.models import Artifact, FieldEvidence, PageSnapshot, Record, Task
from app.infra.deps import get_object_storage, get_session_factory


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        sys.exit(1)


def main() -> None:
    session = get_session_factory()()
    storage = get_object_storage()
    try:
        task = session.scalars(select(Task).order_by(Task.id).limit(1)).first()
        check("1 task queryable", task is not None, str(task.id if task else "none"))

        actual_records = session.execute(select(func.count()).select_from(Record)).scalar_one()
        expected = int(os.environ.get("EXPECTED_RECORDS", "0"))
        check(
            "2 record count matches backup source",
            actual_records > 0 and actual_records == expected,
            f"restored={actual_records} expected={expected}",
        )

        fe = session.scalars(select(FieldEvidence).limit(1)).first()
        check("3 field evidence readable", fe is not None, str(fe.id if fe else "none"))

        snap = session.scalars(select(PageSnapshot).order_by(PageSnapshot.id).limit(1)).first()
        if snap is not None and snap.storage_ref:
            body = storage.get(snap.storage_ref)
            digest = hashlib.sha256(body).hexdigest()
            check(
                "4 snapshot content hash matches",
                digest == snap.content_hash,
                f"key={snap.storage_ref} sha256={digest[:16]}...",
            )
        else:
            check("4 snapshot content hash matches", False, "no snapshot fixture")

        art = session.scalars(
            select(Artifact).where(Artifact.export_type == "formal").order_by(Artifact.id).limit(1)
        ).first()
        if art is not None and art.storage_ref:
            csv = storage.get(art.storage_ref).decode("utf-8")
            rows = sum(1 for _ in csv.splitlines()) - 1  # header row
            digest = hashlib.sha256(csv.encode("utf-8")).hexdigest()
            check(
                "5 csv artifact rows + hash",
                rows >= 1 and digest == art.content_hash,
                f"key={art.storage_ref} rows={rows} sha256={digest[:16]}...",
            )
        else:
            check("5 csv artifact rows + hash", False, "no formal csv artifact fixture")
    finally:
        session.close()


if __name__ == "__main__":
    main()
