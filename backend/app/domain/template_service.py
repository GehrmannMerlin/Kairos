"""TemplateService — versioned CollectionTemplate lifecycle + task creation (M-06).

Editing a template appends a new immutable version; Tasks keep referencing the
version they were created from. ``use`` creates a Task Draft and a generated
Spec Draft (runtime facts come from the spec, never from the template).
"""

from __future__ import annotations

from typing import Any

from app.domain.models import Task
from app.domain.repository import (
    SpecDraftRepository,
    SpecVersionRepository,
    TaskRepository,
    TemplateRepository,
)
from app.domain.spec import SpecDraftPayload
from app.domain.task_types import TaskType
from app.domain.template import TemplateSpec, TemplateVariableSpec


def _variables_list(raw: Any) -> list[TemplateVariableSpec]:
    return [TemplateVariableSpec.model_validate(v) for v in raw or []]


def _resolve_goal(template: Any, variables: dict[str, str]) -> str:
    goal = template.goal_template
    for v in _variables_list(template.variables):
        token = "{" + v.name + "}"
        if token not in goal:
            continue
        value = variables.get(v.name) or v.default
        if value is None:
            if v.required:
                from app.domain.errors import DomainError

                raise DomainError(f"模板变量「{v.label or v.name}」为必填")
            continue
        goal = goal.replace(token, value)
    return goal


class TemplateService:
    def __init__(self, db: Any) -> None:
        self._db = db
        self._templates = TemplateRepository(db)
        self._tasks = TaskRepository(db)
        self._drafts = SpecDraftRepository(db)
        self._specs = SpecVersionRepository(db)

    # ---- CRUD ----

    def list(self, *, user_id: int):
        return self._templates.list_current(user_id)

    def get(self, *, user_id: int, template_id: str):
        return self._templates.get_current(user_id, template_id)

    def create(self, *, user_id: int, spec: TemplateSpec):
        return self._templates.create(user_id=user_id, **spec.model_dump(mode="json"))

    def update(self, *, user_id: int, template_id: str, spec: TemplateSpec):
        return self._templates.append_version(
            template_id=template_id, user_id=user_id, **spec.model_dump(mode="json")
        )

    def duplicate(self, *, user_id: int, template_id: str):
        current = self._templates.get_current(user_id, template_id)
        spec = TemplateSpec.model_validate(
            {
                "name": f"{current.name}（副本）",
                "task_type": current.task_type,
                "goal_template": current.goal_template,
                "variables": current.variables,
                "field_schema": current.field_schema,
                "completion_conditions": current.completion_conditions,
                "advanced_settings": current.advanced_settings,
                "field_expansion": current.field_expansion,
                "default_model_config_ref": current.default_model_config_ref,
            }
        )
        return self.create(user_id=user_id, spec=spec)

    def set_favorite(self, *, user_id: int, template_id: str, favorite: bool):
        return self._templates.set_favorite(user_id, template_id, favorite)

    def delete(self, *, user_id: int, template_id: str) -> None:
        self._templates.delete(user_id, template_id)

    # ---- use / from task ----

    def use(self, *, user_id: int, template_id: str, variables: dict[str, str]) -> Task:
        template = self._templates.get_current(user_id, template_id)  # owner gate
        resolved = _resolve_goal(template, variables)
        task = self._tasks.create(
            user_id=user_id,
            title=resolved[:50] or template.name,
            task_type=template.task_type,
            template_id=template.template_id,
            template_version=template.version,
        )
        spec = SpecDraftPayload.model_validate(
            {
                "goal": resolved,
                "task_type": template.task_type,
                "fields": template.field_schema,
                "auto_expand_fields": False,
                "completion_conditions": template.completion_conditions,
                "advanced_settings": template.advanced_settings,
                "field_expansion": template.field_expansion,
            }
        )
        self._drafts.upsert(user_id=user_id, task_id=task.id, payload=spec.model_dump(mode="json"))
        return task

    def create_from_task(self, *, user_id: int, task_id: int):
        from app.domain.errors import DomainError

        task = self._tasks.get_owned(user_id, task_id)
        if task.current_spec_version is None:
            raise DomainError("任务还没有已确认的采集方案")
        spec_row = self._specs.get_version(user_id, task_id, task.current_spec_version)
        spec = SpecDraftPayload.model_validate(spec_row.payload)

        goal_template = spec.goal
        variables: list[TemplateVariableSpec] = []
        for suggestion in spec.template_variables or []:
            if suggestion.value and suggestion.value in goal_template:
                goal_template = goal_template.replace(suggestion.value, "{" + suggestion.name + "}")
                variables.append(
                    TemplateVariableSpec(
                        name=suggestion.name, label=suggestion.label, required=True
                    )
                )
        template_spec = TemplateSpec(
            name=f"{task.title or '采集任务'} 模板",
            task_type=spec.task_type or TaskType.EXPLORATORY,
            goal_template=goal_template,
            variables=variables,
            field_schema=spec.fields,
            completion_conditions=spec.completion_conditions,
            advanced_settings=spec.advanced_settings,
            field_expansion=spec.field_expansion,
        )
        return self.create(user_id=user_id, spec=template_spec)
