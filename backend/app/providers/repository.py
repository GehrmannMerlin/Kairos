"""Versioned repositories for model/search configs (M-03).

Pattern: every edit appends a new row with (config_id, version+1) and flips
``is_current`` onto the new row; old rows are immutable history. ``config_id``
is the stable logical identity; M-06 freezes (config_id, version).
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession

from app.auth.errors import NotFoundError
from app.credentials.models import ModelConfig, SearchConfig


class ModelConfigRepository:
    def __init__(self, db: DbSession) -> None:
        self._db = db

    def create_version(
        self,
        *,
        user_id: int,
        name: str,
        provider_type: str,
        model_name: str,
        base_url: str | None,
        credential_version_id: int | None,
        is_default: bool,
    ) -> ModelConfig:
        row = ModelConfig(
            config_id=uuid4().hex,
            user_id=user_id,
            version=1,
            name=name,
            provider_type=provider_type,
            model_name=model_name,
            base_url=base_url,
            credential_version_id=credential_version_id,
            is_current=True,
            is_default=is_default,
            connection_status="untested",
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def append_version(
        self,
        *,
        config_id: str,
        user_id: int,
        name: str,
        provider_type: str,
        model_name: str,
        base_url: str | None,
        credential_version_id: int | None,
        is_default: bool,
    ) -> ModelConfig:
        self._unset_current(config_id, user_id)
        version = self.next_version(config_id)
        row = ModelConfig(
            config_id=config_id,
            user_id=user_id,
            version=version,
            name=name,
            provider_type=provider_type,
            model_name=model_name,
            base_url=base_url,
            credential_version_id=credential_version_id,
            is_current=True,
            is_default=is_default,
            connection_status="untested",
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def next_version(self, config_id: str) -> int:
        latest = self._db.scalar(
            select(ModelConfig.version)
            .where(ModelConfig.config_id == config_id)
            .order_by(ModelConfig.version.desc())
            .limit(1)
        )
        return (latest or 0) + 1

    def get_current(self, user_id: int, config_id: str) -> ModelConfig:
        row = self._db.scalar(
            select(ModelConfig).where(
                ModelConfig.config_id == config_id,
                ModelConfig.user_id == user_id,
                ModelConfig.is_current.is_(True),
            )
        )
        if row is None:
            raise NotFoundError("资源不存在")
        return row

    def get_version(self, user_id: int, config_id: str, version: int) -> ModelConfig:
        row = self._db.scalar(
            select(ModelConfig).where(
                ModelConfig.config_id == config_id,
                ModelConfig.user_id == user_id,
                ModelConfig.version == version,
            )
        )
        if row is None:
            raise NotFoundError("资源不存在")
        return row

    def list_current(self, user_id: int) -> list[ModelConfig]:
        return list(
            self._db.scalars(
                select(ModelConfig)
                .where(ModelConfig.user_id == user_id, ModelConfig.is_current.is_(True))
                .order_by(ModelConfig.created_at.desc())
            )
        )

    def get_default(self, user_id: int) -> ModelConfig | None:
        return self._db.scalar(
            select(ModelConfig).where(
                ModelConfig.user_id == user_id,
                ModelConfig.is_current.is_(True),
                ModelConfig.is_default.is_(True),
            )
        )

    def clear_defaults(self, user_id: int) -> None:
        self._db.execute(
            update(ModelConfig)
            .where(ModelConfig.user_id == user_id, ModelConfig.is_current.is_(True))
            .values(is_default=False)
        )
        self._db.commit()

    def set_default(self, user_id: int, config_id: str) -> ModelConfig:
        self.get_current(user_id, config_id)  # ownership check
        self.clear_defaults(user_id)
        self._db.execute(
            update(ModelConfig)
            .where(
                ModelConfig.user_id == user_id,
                ModelConfig.config_id == config_id,
                ModelConfig.is_current.is_(True),
            )
            .values(is_default=True)
        )
        self._db.commit()
        return self.get_current(user_id, config_id)

    def mark_connection(
        self, user_id: int, config_id: str, status: str, tested_at: datetime
    ) -> None:
        self.get_current(user_id, config_id)
        self._db.execute(
            update(ModelConfig)
            .where(
                ModelConfig.user_id == user_id,
                ModelConfig.config_id == config_id,
                ModelConfig.is_current.is_(True),
            )
            .values(connection_status=status, last_tested_at=tested_at)
        )
        self._db.commit()

    def delete(self, user_id: int, config_id: str) -> None:
        self.get_current(user_id, config_id)
        self._db.execute(
            update(ModelConfig)
            .where(
                ModelConfig.user_id == user_id,
                ModelConfig.config_id == config_id,
                ModelConfig.is_current.is_(True),
            )
            .values(is_current=False, connection_status="disabled")
        )
        self._db.commit()

    def _unset_current(self, config_id: str, user_id: int) -> None:
        self._db.execute(
            update(ModelConfig)
            .where(
                ModelConfig.user_id == user_id,
                ModelConfig.config_id == config_id,
                ModelConfig.is_current.is_(True),
            )
            .values(is_current=False)
        )
        self._db.commit()


class SearchConfigRepository:
    def __init__(self, db: DbSession) -> None:
        self._db = db

    def create_version(
        self,
        *,
        user_id: int,
        name: str,
        provider_type: str,
        base_url: str | None,
        credential_version_id: int | None,
    ) -> SearchConfig:
        row = SearchConfig(
            config_id=uuid4().hex,
            user_id=user_id,
            version=1,
            name=name,
            provider_type=provider_type,
            base_url=base_url,
            credential_version_id=credential_version_id,
            is_current=True,
            connection_status="untested",
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def append_version(
        self,
        *,
        config_id: str,
        user_id: int,
        name: str,
        provider_type: str,
        base_url: str | None,
        credential_version_id: int | None,
    ) -> SearchConfig:
        self._unset_current(config_id, user_id)
        version = self.next_version(config_id)
        row = SearchConfig(
            config_id=config_id,
            user_id=user_id,
            version=version,
            name=name,
            provider_type=provider_type,
            base_url=base_url,
            credential_version_id=credential_version_id,
            is_current=True,
            connection_status="untested",
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def next_version(self, config_id: str) -> int:
        latest = self._db.scalar(
            select(SearchConfig.version)
            .where(SearchConfig.config_id == config_id)
            .order_by(SearchConfig.version.desc())
            .limit(1)
        )
        return (latest or 0) + 1

    def get_current(self, user_id: int, config_id: str) -> SearchConfig:
        row = self._db.scalar(
            select(SearchConfig).where(
                SearchConfig.config_id == config_id,
                SearchConfig.user_id == user_id,
                SearchConfig.is_current.is_(True),
            )
        )
        if row is None:
            raise NotFoundError("资源不存在")
        return row

    def get_version(self, user_id: int, config_id: str, version: int) -> SearchConfig:
        row = self._db.scalar(
            select(SearchConfig).where(
                SearchConfig.config_id == config_id,
                SearchConfig.user_id == user_id,
                SearchConfig.version == version,
            )
        )
        if row is None:
            raise NotFoundError("资源不存在")
        return row

    def list_current(self, user_id: int) -> list[SearchConfig]:
        return list(
            self._db.scalars(
                select(SearchConfig)
                .where(SearchConfig.user_id == user_id, SearchConfig.is_current.is_(True))
                .order_by(SearchConfig.created_at.desc())
            )
        )

    def mark_connection(
        self, user_id: int, config_id: str, status: str, tested_at: datetime
    ) -> None:
        self.get_current(user_id, config_id)
        self._db.execute(
            update(SearchConfig)
            .where(
                SearchConfig.user_id == user_id,
                SearchConfig.config_id == config_id,
                SearchConfig.is_current.is_(True),
            )
            .values(connection_status=status, last_tested_at=tested_at)
        )
        self._db.commit()

    def delete(self, user_id: int, config_id: str) -> None:
        self.get_current(user_id, config_id)
        self._db.execute(
            update(SearchConfig)
            .where(
                SearchConfig.user_id == user_id,
                SearchConfig.config_id == config_id,
                SearchConfig.is_current.is_(True),
            )
            .values(is_current=False, connection_status="disabled")
        )
        self._db.commit()

    def _unset_current(self, config_id: str, user_id: int) -> None:
        self._db.execute(
            update(SearchConfig)
            .where(
                SearchConfig.user_id == user_id,
                SearchConfig.config_id == config_id,
                SearchConfig.is_current.is_(True),
            )
            .values(is_current=False)
        )
        self._db.commit()
