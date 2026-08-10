"""GoalUnderstandingService — orchestrate Task Draft -> Agent -> Spec Draft (M-06).

This is the bounded M-06 Agent entry point: it is a single LLM call, equivalent
in scope to the M-03 connection test, so it can run inside a FastAPI route. Real
multi-step planning / execution is M-07/M-08.

Secret handling: the API key is decrypted only at call time via CredentialVault
and never leaves this path; audit metadata stores references (config_id/version,
provider, model, duration) — never the key.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any

from app.agents.goal_understanding import GoalInput, GoalUnderstandingAgent
from app.agents.schemas import GoalUnderstandingResult
from app.auth.models import User
from app.credentials.vault import CredentialVault
from app.domain.models import ChatMessage, Task
from app.domain.spec import SpecDraftPayload, validate_spec_payload
from app.domain.task_draft import TaskDraftService
from app.providers import errors as provider_errors
from app.providers.registry import build_model_provider
from app.providers.service import ProviderService


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
    result: GoalUnderstandingResult
    message: ChatMessage
    task: Task
    spec_draft: dict
    audit: dict


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

    async def understand_for_task(self, *, user: User, task_id: int) -> UnderstandingOutcome:
        drafts = TaskDraftService(self._db)
        task = drafts.get_task(user_id=user.id, task_id=task_id)

        # D-066: only gate on a usable model when we actually need the Agent.
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

        spec_payload = drafts.get_spec_draft(user_id=user.id, task_id=task_id)
        spec = validate_spec_payload(spec_payload) if spec_payload else SpecDraftPayload(goal="")
        messages = drafts.list_messages(user_id=user.id, task_id=task_id)
        user_texts = [m.content for m in messages if m.role == "user"]
        goal_input = GoalInput(
            goal_text=spec.goal or (user_texts[-1] if user_texts else ""),
            seed_urls=spec.source_scope.seed_urls,
            source_hints=spec.source_scope.source_hints,
        )

        started = perf_counter()
        try:
            result = await self._agent.understand(
                goal_input=goal_input,
                chat_context=user_texts[:-1],
                resolved=resolved,
                api_key=api_key,
            )
        except provider_errors.ProviderError as exc:
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
            raise
        duration_ms = int((perf_counter() - started) * 1000)

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
        return UnderstandingOutcome(
            result=result, message=message, task=task, spec_draft=payload, audit=audit
        )
