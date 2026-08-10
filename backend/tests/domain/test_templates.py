"""CollectionTemplate version / use / from-task / owner isolation (TEST E)."""

from __future__ import annotations

import pytest
from app.auth.errors import NotFoundError
from app.auth.repository import UserRepository
from app.domain.errors import DomainError
from app.domain.repository import SpecDraftRepository, TaskRepository, TemplateRepository
from app.domain.spec import SpecDraftPayload
from app.domain.task_types import TaskType
from app.domain.template import TemplateSpec
from app.domain.template_service import TemplateService


def _spec(
    goal: str = "帮我搜集{city}的工业自动化设备供应商", name: str = "供应商模板"
) -> TemplateSpec:
    return TemplateSpec(
        name=name,
        task_type=TaskType.EXPLORATORY,
        goal_template=goal,
        variables=[{"name": "city", "label": "城市", "required": True}],
        field_schema=[{"name": "公司名", "type": "text", "required": True}],
        completion_conditions=[{"kind": "min_records", "target": 20}],
        advanced_settings={"max_pages": 100},
        field_expansion={},
    )


def _city_payload() -> dict:
    return {
        "schema_version": "m06.1",
        "task_type": "EXPLORATORY",
        "task_name": None,
        "goal": "帮我搜集深圳的工业自动化设备供应商",
        "fields": [{"name": "公司名", "type": "text", "required": True}],
        "auto_expand_fields": True,
        "source_scope": {"mode": "EXPLORATORY", "seed_urls": [], "source_hints": []},
        "completion_conditions": [{"kind": "min_records", "target": 20}],
        "advanced_settings": {},
        "field_expansion": {},
        "template_variables": [{"name": "city", "label": "城市", "value": "深圳"}],
    }


def test_use_creates_task_referencing_template_version(db, user) -> None:
    svc = TemplateService(db)
    tpl = svc.create(user_id=user.id, spec=_spec())
    assert tpl.version == 1

    task = svc.use(user_id=user.id, template_id=tpl.template_id, variables={"city": "深圳"})
    assert task.template_id == tpl.template_id
    assert task.template_version == 1
    assert task.task_type == "EXPLORATORY"

    draft = SpecDraftRepository(db).get_for_task(user.id, task.id)
    payload = SpecDraftPayload.model_validate(draft.payload)
    assert payload.goal == "帮我搜集深圳的工业自动化设备供应商"
    assert payload.fields[0].name == "公司名"


def test_edit_creates_v2_and_old_task_keeps_v1(db, user) -> None:
    svc = TemplateService(db)
    tpl = svc.create(user_id=user.id, spec=_spec(goal="帮我搜集{city}的 A"))
    task = svc.use(user_id=user.id, template_id=tpl.template_id, variables={"city": "深圳"})

    tpl2 = svc.update(
        user_id=user.id, template_id=tpl.template_id, spec=_spec(goal="帮我搜集{city}的 B")
    )
    assert tpl2.version == 2

    fresh = TaskRepository(db).get_owned(user.id, task.id)
    assert fresh.template_id == tpl.template_id
    assert fresh.template_version == 1  # 历史 Task 仍引用 v1

    v1 = TemplateRepository(db).get_version(user.id, tpl.template_id, 1)
    assert v1.goal_template == "帮我搜集{city}的 A"  # v1 不可变


def test_missing_required_variable_rejected(db, user) -> None:
    svc = TemplateService(db)
    tpl = svc.create(user_id=user.id, spec=_spec())
    with pytest.raises(DomainError):
        svc.use(user_id=user.id, template_id=tpl.template_id, variables={})


def test_cross_user_cannot_use_or_read(db, user) -> None:
    bob = UserRepository(db).create("bob@example.com", "hash", None)
    svc = TemplateService(db)
    tpl = svc.create(user_id=user.id, spec=_spec())

    with pytest.raises(NotFoundError):
        svc.get(user_id=bob.id, template_id=tpl.template_id)
    with pytest.raises(NotFoundError):
        svc.use(user_id=bob.id, template_id=tpl.template_id, variables={"city": "X"})


def test_create_from_task_variableizes_city(db, service, user, task) -> None:
    service.confirm_spec(
        user_id=user.id,
        task_id=task.id,
        expected_version=1,
        spec_payload=_city_payload(),
        actor_id=user.id,
    )
    svc = TemplateService(db)
    tpl = svc.create_from_task(user_id=user.id, task_id=task.id)

    assert "{city}" in tpl.goal_template
    assert "深圳" not in tpl.goal_template
    assert any(v["name"] == "city" for v in tpl.variables)
    assert tpl.field_schema[0]["name"] == "公司名"


def test_create_from_task_requires_confirmed_spec(db, user) -> None:
    from app.domain.repository import TaskRepository as TR

    task = TR(db).create(user_id=user.id, title="未确认")
    svc = TemplateService(db)
    with pytest.raises(DomainError):
        svc.create_from_task(user_id=user.id, task_id=task.id)


def test_delete_and_favorite(db, user) -> None:
    svc = TemplateService(db)
    tpl = svc.create(user_id=user.id, spec=_spec())
    fav = svc.set_favorite(user_id=user.id, template_id=tpl.template_id, favorite=True)
    assert fav.is_favorite is True

    svc.delete(user_id=user.id, template_id=tpl.template_id)
    with pytest.raises(NotFoundError):
        svc.get(user_id=user.id, template_id=tpl.template_id)
