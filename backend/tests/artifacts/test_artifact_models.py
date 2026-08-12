"""M-15 模型扩展：Artifact M-15 列 + Task.restore_state（migration 0012）。"""

from __future__ import annotations

from app.domain.models import Artifact, Task


def test_artifact_has_m15_columns(db, user_a) -> None:
    db.flush()
    a = Artifact(
        user_id=user_a.id,
        task_id=1,
        artifact_type="csv",
        dataset_version="ds-abc",
        export_type="formal",
        filter_snapshot={"partition": "passed"},
        content_hash="h" * 64,
        storage_ref="artifacts/u1/csv/h.csv",
        request_fingerprint="fp-abc",
        schema_version="spec-v1/m06.1",
        row_count=2,
        size_bytes=10,
        filename="task_formal.csv",
        status="ready",
    )
    db.add(a)
    db.flush()
    assert a.request_fingerprint == "fp-abc"
    assert a.schema_version == "spec-v1/m06.1"
    assert a.row_count == 2
    assert a.status == "ready"


def test_task_has_restore_state(db, user_a) -> None:
    db.flush()
    t = Task(user_id=user_a.id, title="x", state="COMPLETED", restore_state="COMPLETED")
    db.add(t)
    db.flush()
    assert t.restore_state == "COMPLETED"
