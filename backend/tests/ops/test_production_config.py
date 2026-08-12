"""TEST A：production 配置校验（M-17 上线门禁）。

Dev defaults（Secure Cookie=false / localhost CORS / 无主密钥 / 本地 DB / staging bucket）
在 production 环境下必须被校验拒绝；合法 production 配置必须通过。
"""

from __future__ import annotations

import pytest
from app.config import Settings

_PROD = {
    "env": "production",
    "session_cookie_secure": True,
    "credential_master_key": "a" * 64,
    "cors_origins": ["https://app.kairos.ac.cn"],
    "database_url": "postgresql+psycopg://kairos:prod@pg.example:5432/kairos_prod",
    "s3_bucket": "kairos-prod",
    "temporal_namespace": "kairos-production",
}


def _prod(**overrides: object) -> Settings:
    base = Settings().model_dump()
    base.update(_PROD)
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_production_accepts_valid_config() -> None:
    assert _prod().production_validation_errors() == []


def test_production_rejects_dev_cookie() -> None:
    s = _prod(session_cookie_secure=False)
    assert any("SESSION_COOKIE_SECURE" in e for e in s.production_validation_errors())


def test_production_rejects_blank_master_key() -> None:
    s = _prod(credential_master_key=None)
    assert any("MASTER_KEY" in e for e in s.production_validation_errors())


def test_production_rejects_short_master_key() -> None:
    s = _prod(credential_master_key="short")
    assert any("MASTER_KEY" in e for e in s.production_validation_errors())


def test_production_rejects_dev_origin() -> None:
    s = _prod(cors_origins=["http://localhost:5173"])
    assert any("CORS_ORIGINS" in e for e in s.production_validation_errors())


def test_production_rejects_wildcard_origin() -> None:
    s = _prod(cors_origins=["*"])
    assert any("CORS_ORIGINS" in e for e in s.production_validation_errors())


def test_production_rejects_dev_db() -> None:
    s = _prod(database_url="postgresql+psycopg://kairos:dev@localhost:5434/kairos")
    assert any("DATABASE_URL" in e for e in s.production_validation_errors())


def test_production_rejects_staging_bucket() -> None:
    s = _prod(s3_bucket="kairos-staging")
    assert any("S3_BUCKET" in e for e in s.production_validation_errors())


def test_production_rejects_default_namespace() -> None:
    s = _prod(temporal_namespace="default")
    assert any("TEMPORAL_NAMESPACE" in e for e in s.production_validation_errors())


def test_non_production_env_skips_validation() -> None:
    s = Settings(_env_file=None, **{**Settings().model_dump(), "env": "staging"})
    assert s.production_validation_errors() == []


def test_validate_runtime_raises_on_violation() -> None:
    s = _prod(session_cookie_secure=False)
    with pytest.raises(RuntimeError, match="production config invalid"):
        s.validate_runtime()


def test_validate_runtime_passes_when_ok() -> None:
    _prod().validate_runtime()  # 不抛异常即通过
