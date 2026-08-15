"""TaskDraftService — business commands for the Task creation phase (M-06).

A Task IS the single persistent Agent conversation (D-033); there is no separate
ChatTask/AgentTask/DraftTask object. The Task stays in the M-04 DRAFT state until
a spec is confirmed. ``allowed_actions`` and state transitions keep coming from
the M-04 state machine — this service never bypasses them with a raw UPDATE.

Guarantees:
- create + first message persist even if a later Agent call fails (the agent
  call is a separate request; the Draft and User ChatMessage are already saved).
- message sends are idempotent via the M-04 IdempotencyService + DB backstop.
- every read is owner-scoped; cross-user access raises 404 (no existence leak).
"""

from __future__ import annotations

from typing import Any

from app.discovery.errors import DiscoveryValidationError
from app.discovery.url import canonical_url
from app.domain.idempotency import IdempotencyService
from app.domain.models import ChatMessage, Task
from app.domain.repository import (
    ChatMessageRepository,
    SpecDraftRepository,
    TaskRepository,
)
from app.domain.spec import SpecDraftPayload, validate_spec_payload


def _draft_title(content: str) -> str:
    title = " ".join(content.split())
    return title[:50] or "新任务"


def _validate_or_raise(payload: dict) -> SpecDraftPayload:
    try:
        return validate_spec_payload(payload)
    except Exception as exc:  # pydantic ValidationError -> stable 422
        from app.domain.errors import SpecValidationError

        raise SpecValidationError("采集方案参数不合法") from exc


class TaskDraftService:
    def __init__(self, db: Any) -> None:
        self._db = db
        self._tasks = TaskRepository(db)
        self._chat = ChatMessageRepository(db)
        self._drafts = SpecDraftRepository(db)
        self._idempotency = IdempotencyService()

    # ---- create ----

    def create_empty_draft(self, *, user_id: int) -> Task:
        return self._tasks.create(user_id=user_id, title="新任务", task_type=None)

    def create_draft_with_message(
        self,
        *,
        user_id: int,
        content: str,
        seed_urls: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[Task, ChatMessage]:
        if idempotency_key:
            replay = self._idempotency.find_replay(
                self._db,
                user_id=user_id,
                operation="task.create_draft",
                client_key=idempotency_key,
                payload={"content": content, "seed_urls": seed_urls},
            )
            if replay is not None:
                task = self._tasks.get_owned(user_id, replay)
                messages = self._chat.list_by_task(user_id, task.id)
                first_user = next((m for m in messages if m.role == "user"), None)
                if first_user is None:
                    raise RuntimeError("replay task has no user message")
                return task, first_user

        task = Task(
            user_id=user_id, title=_draft_title(content), task_type=None, state="DRAFT", version=1
        )
        self._db.add(task)
        self._db.flush()  # materialize task.id for the message FK
        message = ChatMessage(user_id=user_id, task_id=task.id, role="user", content=content)
        self._db.add(message)
        if seed_urls:
            # '添加网址' writes into Draft Context immediately (D-034); no fetch.
            from app.domain.spec import SourceScope
            from app.domain.task_types import TaskType

            spec = SpecDraftPayload(
                goal="",
                source_scope=SourceScope(mode=TaskType.EXPLORATORY, seed_urls=list(seed_urls)),
            )
            self._drafts.upsert(
                user_id=user_id, task_id=task.id, payload=spec.model_dump(mode="json")
            )
        self._db.flush()
        task_id = task.id

        self._idempotency.record(
            self._db,
            user_id=user_id,
            operation="task.create_draft",
            client_key=idempotency_key or _auto_key("create_draft", user_id, content, seed_urls),
            payload={"content": content, "seed_urls": seed_urls},
            result_ref=("task", task_id),
        )
        self._db.refresh(task)
        self._db.refresh(message)
        return task, message

    # ---- append ----

    def append_user_message(
        self,
        *,
        user_id: int,
        task_id: int,
        content: str,
        idempotency_key: str | None = None,
    ) -> ChatMessage:
        task = self._tasks.get_owned(user_id, task_id)  # owner + existence gate
        if idempotency_key:
            replay_id = self._idempotency.find_replay(
                self._db,
                user_id=user_id,
                operation="chat.message",
                client_key=idempotency_key,
                payload={"content": content},
            )
            if replay_id is not None:
                row = self._db.get(ChatMessage, replay_id)
                if row is None or row.user_id != user_id or row.task_id != task_id:
                    raise RuntimeError("replay message mismatch")
                return row

        message = ChatMessage(user_id=user_id, task_id=task.id, role="user", content=content)
        self._db.add(message)
        self._db.flush()
        self._idempotency.record(
            self._db,
            user_id=user_id,
            operation="chat.message",
            client_key=idempotency_key or _auto_key("message", user_id, task_id, content),
            payload={"content": content},
            result_ref=("chat_message", message.id),
        )
        self._db.refresh(message)
        return message

    # ---- read ----

    def get_task(self, *, user_id: int, task_id: int) -> Task:
        return self._tasks.get_owned(user_id, task_id)  # owner + existence gate

    def list_messages(self, *, user_id: int, task_id: int) -> list[ChatMessage]:
        self.get_task(user_id=user_id, task_id=task_id)
        return self._chat.list_by_task(user_id, task_id)

    def append_message(
        self,
        *,
        user_id: int,
        task_id: int,
        role: str,
        content: str,
        ref_type: str | None = None,
        ref_id: int | None = None,
        meta: dict | None = None,
    ) -> ChatMessage:
        self.get_task(user_id=user_id, task_id=task_id)
        return self._chat.create(
            user_id=user_id,
            task_id=task_id,
            role=role,
            content=content,
            ref_type=ref_type,
            ref_id=ref_id,
            meta=meta,
        )

    # ---- spec draft ----

    def get_spec_draft(self, *, user_id: int, task_id: int) -> dict | None:
        self._tasks.get_owned(user_id, task_id)
        draft = self._drafts.get_for_task(user_id, task_id)
        return draft.payload if draft is not None else None

    def save_spec_draft(self, *, user_id: int, task_id: int, payload: dict) -> dict:
        self._tasks.get_owned(user_id, task_id)
        validated = _validate_or_raise(payload)  # server-side typed validation
        draft = self._drafts.upsert(
            user_id=user_id, task_id=task_id, payload=validated.model_dump(mode="json")
        )
        return draft.payload

    def add_seed_url(self, *, user_id: int, task_id: int, url: str) -> dict:
        """'添加网址' only writes into Draft Context (D-034); no fetch happens here."""
        try:
            url = canonical_url(url)
        except DiscoveryValidationError as exc:
            from app.domain.errors import DomainError

            raise DomainError(str(exc)) from exc
        self._tasks.get_owned(user_id, task_id)
        current = self.get_spec_draft(user_id=user_id, task_id=task_id)
        payload = current or SpecDraftPayload(goal="").model_dump(mode="json")
        spec = _validate_or_raise(payload)
        if url not in spec.source_scope.seed_urls:
            spec.source_scope.seed_urls.append(url)
        draft = self._drafts.upsert(
            user_id=user_id, task_id=task_id, payload=spec.model_dump(mode="json")
        )
        return draft.payload


def _auto_key(kind: str, *parts: Any) -> str:
    """Fallback client key when the caller did not supply one.

    The M-04 rule still applies: keys are derived from semantic inputs via
    canonical JSON + SHA-256, never from random UUIDs, so identical retries
    produce the same key.
    """
    from app.domain.idempotency import stable_fingerprint

    return stable_fingerprint("auto", kind, *parts)
