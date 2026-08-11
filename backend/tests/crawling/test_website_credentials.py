"""WebsiteCredentialService 测试：脱敏 / owner 隔离 / scope 约束 / Secret 不进 DB / Basic Auth。"""
from __future__ import annotations

import json

import pytest
from app.auth.errors import NotFoundError
from app.crawling.credentials import WebsiteCredentialService
from app.credentials.models import Credential, CredentialVersion
from app.credentials.repository import CredentialRepository
from app.credentials.vault import CredentialVault


@pytest.fixture()
def vault(ctx):
    return CredentialVault(
        master_key=b"\x00" * 32,
        key_version="test",
        repository=CredentialRepository(ctx["db"]),
    )


def _service(ctx, vault):
    return WebsiteCredentialService(ctx["db"], vault)


SECRET = "SUPERSECRETCOOKIEVALUE_XYZ"


def test_store_and_list_redacted(ctx, vault) -> None:
    service = _service(ctx, vault)
    meta = service.store(
        user_id=ctx["user"].id,
        task_id=ctx["task"].id,
        ctype="cookie",
        payload={"cookies": [{"name": "session", "value": SECRET, "domain": "fixture.test"}]},
        scope="CURRENT_TASK",
        domain="fixture.test",
    )
    # 脱敏 metadata：无明文
    assert meta["credential_id"] > 0
    assert meta["type"] == "cookie"
    assert meta["domain"] == "fixture.test"
    assert meta["scope"] == "CURRENT_TASK"
    assert meta["masked"] == f"cred-****{str(meta['credential_id'])[-4:]}"
    assert SECRET not in json.dumps(meta)

    listed = service.list_for_task(user_id=ctx["user"].id, task_id=ctx["task"].id)
    assert len(listed) == 1
    assert SECRET not in json.dumps(listed)


def test_secret_never_in_dto_or_db_plaintext(ctx, vault) -> None:
    """固定 secret 值：DTO / credentials 表 / credential_versions 密文列都不含明文。"""
    db = ctx["db"]
    service = _service(ctx, vault)
    meta = service.store(
        user_id=ctx["user"].id,
        task_id=ctx["task"].id,
        ctype="cookie",
        payload={"cookies": [{"name": "session", "value": SECRET, "domain": "fixture.test"}]},
        scope="CURRENT_TASK",
        domain="fixture.test",
    )
    assert SECRET not in json.dumps(
        service.list_for_task(user_id=ctx["user"].id, task_id=ctx["task"].id)
    )
    cred = db.get(Credential, meta["credential_id"])
    assert cred is not None
    assert SECRET not in (cred.domain or "") and SECRET not in (cred.name or "")
    version = (
        db.query(CredentialVersion)
        .filter(CredentialVersion.credential_id == meta["credential_id"])
        .first()
    )
    assert version is not None
    assert SECRET.encode() not in (version.secret_ciphertext or b"")
    assert SECRET.encode() not in (version.wrapped_dek or b"")


def test_owner_isolation(ctx, vault) -> None:
    db = ctx["db"]
    service = _service(ctx, vault)
    meta = service.store(
        user_id=ctx["user"].id,
        task_id=ctx["task"].id,
        ctype="cookie",
        payload={"cookies": [{"name": "session", "value": SECRET, "domain": "fixture.test"}]},
        scope="CURRENT_TASK",
        domain="fixture.test",
    )
    other = WebsiteCredentialService(db, vault)
    with pytest.raises(NotFoundError):
        other.delete(user_id=99999, credential_id=meta["credential_id"])
    # user B list 不到 user A 的凭据（owner-safe 404 语义）
    assert other.list_saved_for_user(user_id=99999) == []


def test_current_task_scope_bound(ctx, vault) -> None:
    service = _service(ctx, vault)
    service.store(
        user_id=ctx["user"].id,
        task_id=ctx["task"].id,
        ctype="cookie",
        payload={"cookies": [{"name": "session", "value": SECRET, "domain": "fixture.test"}]},
        scope="CURRENT_TASK",
        domain="fixture.test",
    )
    # 本任务可 resolve；其他任务 resolve_for_domain → None（三十七）
    ref = service.resolve_ref(user_id=ctx["user"].id, task_id=ctx["task"].id, domain="fixture.test")
    assert ref is not None and ref["scope"] == "CURRENT_TASK"
    assert SECRET not in json.dumps(ref)
    other_ref = service.resolve_ref(user_id=ctx["user"].id, task_id=99999, domain="fixture.test")
    assert other_ref is None


def test_saved_domain_scope_reusable(ctx, vault) -> None:
    service = _service(ctx, vault)
    service.store(
        user_id=ctx["user"].id,
        task_id=ctx["task"].id,
        ctype="cookie",
        payload={"cookies": [{"name": "session", "value": SECRET, "domain": "fixture.test"}]},
        scope="SAVED_DOMAIN",
        domain="fixture.test",
    )
    saved = service.list_saved_for_user(user_id=ctx["user"].id)
    assert len(saved) == 1 and saved[0]["scope"] == "SAVED_DOMAIN"
    assert SECRET not in json.dumps(saved)


def test_username_password_contract(ctx, vault) -> None:
    service = _service(ctx, vault)
    meta = service.store(
        user_id=ctx["user"].id,
        task_id=ctx["task"].id,
        ctype="username_password",
        payload={"username": "kairos_user", "password": SECRET},
        scope="CURRENT_TASK",
        domain="fixture.test",
    )
    secret = service.read_for_execution(user_id=ctx["user"].id, credential_id=meta["credential_id"])
    assert secret["username"] == "kairos_user"
    assert secret["password"] == SECRET
    headers = service.build_headers(
        user_id=ctx["user"].id,
        credential_ref={"credential_id": meta["credential_id"], "type": "username_password"},
        url="http://fixture.test/basic",
    )
    assert headers is not None
    assert headers["authorization"].startswith("Basic ")
    import base64

    decoded = base64.b64decode(headers["authorization"].split(" ", 1)[1]).decode()
    assert decoded == f"kairos_user:{SECRET}"
