"""Stable idempotency identities (M-04).

Keys are derived from semantic inputs via canonical JSON + SHA-256, never from
random UUIDs. The database unique constraint is the backstop; same key with a
different payload fingerprint is a conflict, never a silent reuse.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_default
    )


def _json_default(obj: Any) -> str:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"not JSON serializable: {type(obj)}")


def stable_fingerprint(*parts: Any) -> str:
    return hashlib.sha256(canonical_json(parts).encode("utf-8")).hexdigest()


def api_operation_key(operation: str, client_key: str) -> str:
    return stable_fingerprint("api", operation, client_key)


def idempotency_key_for_node(
    task_id: int, spec_version: int, node_type: str, input_fingerprint: str
) -> str:
    return stable_fingerprint("node", task_id, spec_version, node_type, input_fingerprint)


def idempotency_key_for_artifact(
    dataset_version: str, export_type: str, filter_snapshot: Any, content_hash: str
) -> str:
    return stable_fingerprint(
        "artifact", dataset_version, export_type, filter_snapshot, content_hash
    )


class IdempotencyService:
    def record(
        self,
        db,
        *,
        user_id: int,
        operation: str,
        client_key: str,
        payload: Any,
        result_ref: tuple[str, int],
    ) -> tuple[bool, int]:
        """Record a client idempotency key. Returns (was_replay, result_ref_id)."""
        from app.domain.repository import IdempotencyRepository

        key = api_operation_key(operation, client_key)
        fp = stable_fingerprint(payload)
        repo = IdempotencyRepository(db)
        existing = repo.find(user_id=user_id, operation=operation, key=key)
        if existing is not None:
            if existing.payload_fingerprint != fp:
                from app.domain.errors import IdempotencyConflictError

                raise IdempotencyConflictError("相同幂等键但请求内容不同")
            return True, existing.result_ref_id if existing.result_ref_id is not None else 0
        ref_type, ref_id = result_ref
        repo.create(
            user_id=user_id,
            operation=operation,
            key=key,
            payload_fingerprint=fp,
            result_ref_type=ref_type,
            result_ref_id=ref_id,
        )
        db.commit()
        return False, ref_id
