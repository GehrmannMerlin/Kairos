"""CollectionTemplate API (M-06). Thin: DTO -> TemplateService -> DTO."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.api.schemas import (
    TemplateDto,
    TemplateFavoriteCommand,
    TemplateListResponse,
    UseTemplateCommand,
    UseTemplateResponse,
)
from app.auth.deps import require_user
from app.auth.models import User
from app.domain.models import CollectionTemplate
from app.domain.template import TemplateSpec
from app.domain.template_service import TemplateService
from app.infra.deps import get_db

router = APIRouter(prefix="/templates", tags=["templates"])


def get_template_service(db: DbSession = Depends(get_db)) -> TemplateService:
    return TemplateService(db)


def _dto(row: CollectionTemplate) -> TemplateDto:
    return TemplateDto(
        template_id=row.template_id,
        version=row.version,
        name=row.name,
        task_type=row.task_type,
        goal_template=row.goal_template,
        variables=row.variables,
        field_schema=row.field_schema,
        completion_conditions=row.completion_conditions,
        advanced_settings=row.advanced_settings,
        field_expansion=row.field_expansion,
        is_favorite=row.is_favorite,
        created_at=row.created_at,
    )


@router.get("", response_model=TemplateListResponse)
def list_templates(
    user: User = Depends(require_user),
    service: TemplateService = Depends(get_template_service),
) -> TemplateListResponse:
    return TemplateListResponse(templates=[_dto(t) for t in service.list(user_id=user.id)])


@router.post("", response_model=TemplateDto, status_code=201)
def create_template(
    cmd: TemplateSpec,
    user: User = Depends(require_user),
    service: TemplateService = Depends(get_template_service),
) -> TemplateDto:
    row = service.create(user_id=user.id, spec=cmd)
    return _dto(row)


@router.get("/{template_id}", response_model=TemplateDto)
def get_template(
    template_id: str,
    user: User = Depends(require_user),
    service: TemplateService = Depends(get_template_service),
) -> TemplateDto:
    return _dto(service.get(user_id=user.id, template_id=template_id))


@router.patch("/{template_id}", response_model=TemplateDto)
def update_template(
    template_id: str,
    cmd: TemplateSpec,
    user: User = Depends(require_user),
    service: TemplateService = Depends(get_template_service),
) -> TemplateDto:
    row = service.update(user_id=user.id, template_id=template_id, spec=cmd)
    return _dto(row)


@router.delete("/{template_id}", status_code=204)
def delete_template(
    template_id: str,
    user: User = Depends(require_user),
    service: TemplateService = Depends(get_template_service),
) -> None:
    service.delete(user_id=user.id, template_id=template_id)
    return None


@router.post("/{template_id}/duplicate", response_model=TemplateDto)
def duplicate_template(
    template_id: str,
    user: User = Depends(require_user),
    service: TemplateService = Depends(get_template_service),
) -> TemplateDto:
    return _dto(service.duplicate(user_id=user.id, template_id=template_id))


@router.post("/{template_id}/favorite", response_model=TemplateDto)
def set_template_favorite(
    template_id: str,
    cmd: TemplateFavoriteCommand,
    user: User = Depends(require_user),
    service: TemplateService = Depends(get_template_service),
) -> TemplateDto:
    return _dto(
        service.set_favorite(user_id=user.id, template_id=template_id, favorite=cmd.favorite)
    )


@router.post("/{template_id}/use", response_model=UseTemplateResponse)
def use_template(
    template_id: str,
    cmd: UseTemplateCommand,
    user: User = Depends(require_user),
    service: TemplateService = Depends(get_template_service),
) -> UseTemplateResponse:
    task = service.use(user_id=user.id, template_id=template_id, variables=cmd.variables)
    return UseTemplateResponse(task_id=task.id)
