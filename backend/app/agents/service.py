"""GoalUnderstandingService — orchestrate Task Draft -> Agent -> Spec Draft (M-06).

This is the bounded M-06 Agent entry point: it is a single LLM call, equivalent
in scope to the M-03 connection test, so it can run inside a FastAPI route. Real
multi-step planning / execution is M-07/M-08.

Secret handling: the API key is decrypted only at call time via CredentialVault
and never leaves this path; audit metadata stores references (config_id/version,
provider, model, duration) — never the key.

Server-side idempotency (request-lifecycle fix): each run is tracked in
``understanding_attempts`` keyed by (task_id, source_message_id,
input_fingerprint). Automatic triggers (page load / send / recovery) reuse an
existing SUCCEEDED result, report IN_PROGRESS while another attempt runs, and do
NOT auto-retry a FAILED attempt — only an explicit USER_REUNDERSTAND creates a
new attempt (and thus a new model call / possible charge).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from app.agents.goal_understanding import GoalInput, GoalUnderstandingAgent
from app.agents.schemas import GoalUnderstandingResult
from app.auth.models import User
from app.credentials.vault import CredentialVault
from app.domain.idempotency import stable_fingerprint
from app.domain.models import ChatMessage, Task
from app.domain.source_contract import normalize_source_contract
from app.domain.spec import SpecDraftPayload, validate_spec_payload
from app.domain.task_draft import TaskDraftService
from app.domain.understanding_attempts import UnderstandingAttemptRepository
from app.providers import errors as provider_errors
from app.providers.errors import (
    ProviderAuthFailedError,
    ProviderInferenceError,
    ProviderModelNotFoundError,
    ProviderNetworkError,
    ProviderRateLimitedError,
)
from app.providers.registry import build_model_provider
from app.providers.service import ProviderService

# 触发来源（前端/路由传入；区分自动与用户显式重跑，决定是否允许新 attempt）。
TRIGGER_AUTO_INITIAL = "AUTO_INITIAL"
TRIGGER_USER_SEND = "USER_SEND"
TRIGGER_USER_REUNDERSTAND = "USER_REUNDERSTAND"
TRIGGER_RECOVERY = "RECOVERY"

AUTO_TRIGGERS = {TRIGGER_AUTO_INITIAL, TRIGGER_USER_SEND, TRIGGER_RECOVERY}


def result_to_spec_payload(result: GoalUnderstandingResult) -> dict:
    return SpecDraftPayload(
        task_type=result.task_type,
        task_name=result.goal[:50],
        goal=result.goal,
        fields=result.fields,
        auto_expand_fields=result.auto_expand_fields,
        source_scope=result.source_scope,
        completion_conditions=result.completion_conditions,
        advanced_settings=result.advanced_runtime_limits,
        field_expansion={},
        template_variables=result.template_variables or [],
    ).model_dump(mode="json")


def _assistant_summary(result: GoalUnderstandingResult) -> str:
    lines = [f"任务类型：{result.task_type.value}", f"目标：{result.goal}"]
    if result.fields:
        names = "、".join(f.name for f in result.fields[:6])
        lines.append(f"字段：{names}")
    if result.source_scope.seed_urls:
        lines.append("指定网址：" + "、".join(result.source_scope.seed_urls))
    if result.clarification_required and result.clarification_question:
        lines.append(f"需要确认：{result.clarification_question}")
    return "\n".join(lines)


@dataclass
class UnderstandingOutcome:
    status: str  # SUCCEEDED | ALREADY_SUCCEEDED | IN_PROGRESS
    task: Task
    trigger_source: str
    result: GoalUnderstandingResult | None = None
    message: ChatMessage | None = None
    spec_draft: dict | None = None
    audit: dict | None = None
    attempt_id: int | None = None


def _provider_error_from_code(code: str) -> provider_errors.ProviderError:
    """把已记录的 attempt 失败重新映射为同分类 ProviderError（复用失败，不再调 Provider）。"""
    for cls in (
        ProviderAuthFailedError,
        ProviderModelNotFoundError,
        ProviderRateLimitedError,
        ProviderInferenceError,
        ProviderNetworkError,
    ):
        if cls.code == code:
            return cls(f"模型调用失败（{code}），你的输入已保留，可稍后重试。")
    return ProviderNetworkError(f"模型调用失败（{code}），你的输入已保留，可稍后重试。")


class GoalUnderstandingService:
    def __init__(
        self,
        db: Any,
        *,
        provider_service: ProviderService,
        vault: CredentialVault,
        agent: GoalUnderstandingAgent | None = None,
    ) -> None:
        self._db = db
        self._provider = provider_service
        self._vault = vault
        self._agent = agent or GoalUnderstandingAgent()

    async def understand_for_task(
        self,
        *,
        user: User,
        task_id: int,
        trigger_source: str = TRIGGER_AUTO_INITIAL,
    ) -> UnderstandingOutcome:
        drafts = TaskDraftService(self._db)
        task = drafts.get_task(user_id=user.id, task_id=task_id)
        attempts = UnderstandingAttemptRepository(self._db)

        spec_payload = drafts.get_spec_draft(user_id=user.id, task_id=task_id)
        spec = validate_spec_payload(spec_payload) if spec_payload else SpecDraftPayload(goal="")
        messages = drafts.list_messages(user_id=user.id, task_id=task_id)
        user_msgs = [m for m in messages if m.role == "user"]
        if not user_msgs:
            from app.domain.errors import DomainError

            raise DomainError("没有用户输入，无法进行目标理解")
        source_message = user_msgs[-1]
        source_message_id = source_message.id

        goal_input = GoalInput(
            goal_text=spec.goal or source_message.content,
            seed_urls=spec.source_scope.seed_urls,
            source_hints=spec.source_scope.source_hints,
        )
        # 幂等身份锚定到源用户消息（append-only，reload 后稳定），不锚定会随理解结果变化的 spec。
        input_fingerprint = stable_fingerprint("understand", source_message.content)

        # 自动触发：复用已有结果 / 报告在途 / 复用失败，都不再调 Provider。
        if trigger_source in AUTO_TRIGGERS:
            reused = self._reuse_existing(
                repo=attempts,
                user_id=user.id,
                task_id=task_id,
                source_message_id=source_message_id,
                input_fingerprint=input_fingerprint,
                task=task,
                trigger_source=trigger_source,
            )
            if reused is not None:
                return reused

        # 用户显式「重新理解」：允许新 attempt；但同一输入已在途时不得并行双跑。
        running = attempts.find_running(
            user_id=user.id,
            task_id=task_id,
            source_message_id=source_message_id,
            input_fingerprint=input_fingerprint,
        )
        if running is not None:
            return UnderstandingOutcome(
                status="IN_PROGRESS",
                task=task,
                trigger_source=trigger_source,
                attempt_id=running.id,
            )

        # 真正需要 Agent 时才要求可用 ModelConfig（D-066）。
        config = self._provider.require_available_model_config(user)
        provider = build_model_provider(config.provider_type)
        resolved = provider.resolve_model(
            model=config.model_name,
            base_url=config.base_url,
            credential_version_id=config.credential_version_id,
        )
        api_key = None
        if config.credential_version_id is not None:
            api_key = self._vault.read_for_execution(
                user_id=user.id, credential_version_id=config.credential_version_id
            )

        request_id = uuid.uuid4().hex[:16]
        attempt, already_running = attempts.begin_running(
            user_id=user.id,
            task_id=task_id,
            source_message_id=source_message_id,
            input_fingerprint=input_fingerprint,
            trigger_source=trigger_source,
            request_id=request_id,
            model_config_id=config.config_id,
            model_config_version=config.version,
            provider=config.provider_type,
            model=config.model_name,
        )
        if already_running:
            return UnderstandingOutcome(
                status="IN_PROGRESS",
                task=task,
                trigger_source=trigger_source,
                attempt_id=None,
            )
        assert attempt is not None

        user_texts = [m.content for m in user_msgs]
        started = perf_counter()
        try:
            result = await self._agent.understand(
                goal_input=goal_input,
                chat_context=user_texts[:-1],
                resolved=resolved,
                api_key=api_key,
            )
        except provider_errors.ProviderError as exc:
            duration_ms = int((perf_counter() - started) * 1000)
            # Recoverable Agent error: persist an error message so the Chat shows
            # what happened; the Task Draft and user input are already saved.
            drafts.append_message(
                user_id=user.id,
                task_id=task_id,
                role="assistant",
                content=f"模型调用失败（{exc.code}），你的输入已保留，可稍后重试。",
                ref_type="error",
                meta={"error_code": exc.code},
            )
            attempts.mark_failed(attempt, error_code=exc.code, duration_ms=duration_ms)
            raise
        except Exception:
            attempts.mark_failed(attempt, error_code="INTERNAL")
            raise
        duration_ms = int((perf_counter() - started) * 1000)

        contract = normalize_source_contract(
            task_type=result.task_type,
            source_scope=result.source_scope,
            search_available=self._provider.has_available_search_config(user),
            explicit_texts=tuple(user_texts),
        )
        result = result.model_copy(
            update={
                "task_type": contract.task_type,
                "source_scope": contract.source_scope,
                "clarification_required": not contract.ready,
                "clarification_question": contract.clarification_question,
            }
        )

        payload = result_to_spec_payload(result)
        drafts.save_spec_draft(user_id=user.id, task_id=task_id, payload=payload)
        task.task_type = result.task_type.value
        self._db.add(task)
        self._db.commit()

        audit = {
            "kind": "goal_result",
            "task_type": result.task_type.value,
            "model_config_id": config.config_id,
            "model_config_version": config.version,
            "provider": config.provider_type,
            "model": config.model_name,
            "duration_ms": duration_ms,
            "clarification_required": result.clarification_required,
        }
        message = drafts.append_message(
            user_id=user.id,
            task_id=task_id,
            role="assistant",
            content=_assistant_summary(result),
            ref_type="goal_result",
            meta=audit,
        )
        attempts.mark_succeeded(
            attempt,
            duration_ms=duration_ms,
            result_payload=result.model_dump(mode="json"),
            spec_draft_payload=payload,
            message_id=message.id,
        )
        return UnderstandingOutcome(
            status="SUCCEEDED",
            result=result,
            message=message,
            task=task,
            spec_draft=payload,
            audit=audit,
            attempt_id=attempt.id,
            trigger_source=trigger_source,
        )

    def _reuse_existing(
        self,
        *,
        repo: UnderstandingAttemptRepository,
        user_id: int,
        task_id: int,
        source_message_id: int,
        input_fingerprint: str,
        task: Task,
        trigger_source: str,
    ) -> UnderstandingOutcome | None:
        """自动触发下复用已有 attempt；返回 None 表示没有可复用记录，应新建。"""
        succeeded = repo.find_latest_succeeded(
            user_id=user_id,
            task_id=task_id,
            source_message_id=source_message_id,
            input_fingerprint=input_fingerprint,
        )
        if succeeded is not None:
            result = (
                GoalUnderstandingResult.model_validate(succeeded.result_payload)
                if succeeded.result_payload
                else None
            )
            message = (
                self._db.get(ChatMessage, succeeded.result_ref_message_id)
                if succeeded.result_ref_message_id
                else None
            )
            audit = (
                {
                    "kind": "goal_result",
                    "provider": succeeded.provider,
                    "model": succeeded.model,
                    "duration_ms": succeeded.duration_ms,
                    "model_config_id": succeeded.model_config_id,
                    "model_config_version": succeeded.model_config_version,
                }
                if result is not None
                else None
            )
            return UnderstandingOutcome(
                status="ALREADY_SUCCEEDED",
                result=result,
                message=message,
                task=task,
                spec_draft=succeeded.spec_draft_payload,
                audit=audit,
                attempt_id=succeeded.id,
                trigger_source=trigger_source,
            )

        running = repo.find_running(
            user_id=user_id,
            task_id=task_id,
            source_message_id=source_message_id,
            input_fingerprint=input_fingerprint,
        )
        if running is not None:
            return UnderstandingOutcome(
                status="IN_PROGRESS",
                task=task,
                trigger_source=trigger_source,
                attempt_id=running.id,
            )

        failed = repo.find_latest_failed(
            user_id=user_id,
            task_id=task_id,
            source_message_id=source_message_id,
            input_fingerprint=input_fingerprint,
        )
        if failed is not None and failed.error_code:
            # 自动 reload 不自动重试：复用既有失败分类，不再产生新模型请求。
            raise _provider_error_from_code(failed.error_code)
        return None
