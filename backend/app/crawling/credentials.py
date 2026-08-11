"""网站凭据服务（M-10 / D-059 / D-023 / D-017）。

复用 M-03 CredentialVault（不建第二套 Secrets DB）。Task 只保存脱敏 credential_ref
（credential_id/type/domain/scope），明文只在 Activity 执行时临时解密（进程内存即用即弃）。
scope 约束：CURRENT_TASK 只能被本任务引用（三十七）；SAVED_DOMAIN 供同用户后续任务选择
（三十八），但使用前仍须本任务 Approval（三十九）。
"""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import urlsplit

from app.credentials.models import Credential
from app.credentials.repository import CredentialRepository
from app.credentials.vault import CredentialVault

WEBSITE_SCOPES = {"CURRENT_TASK", "SAVED_DOMAIN"}


class WebsiteCredentialError(Exception):
    pass


def _masked(credential_id: int) -> str:
    return f"cred-****{str(credential_id)[-4:]}"


def _metadata(cred: Credential) -> dict:
    """脱敏 metadata：永不回读明文。"""
    return {
        "credential_id": cred.id,
        "type": cred.kind,
        "domain": cred.domain,
        "scope": cred.scope,
        "task_id": cred.task_id,
        "masked": _masked(cred.id),
        "created_at": cred.created_at.isoformat() if cred.created_at else None,
    }


def _validate_payload(ctype: str, payload: dict) -> str:
    if ctype == "cookie":
        cookies = payload.get("cookies")
        if not isinstance(cookies, list) or not cookies:
            raise WebsiteCredentialError("cookie 凭据必须包含 cookies 列表")
        for c in cookies:
            if not c.get("name") or "value" not in c:
                raise WebsiteCredentialError("每个 cookie 必须包含 name 与 value")
        return json.dumps({"cookies": cookies}, ensure_ascii=False)
    if ctype == "username_password":
        username = payload.get("username")
        password = payload.get("password")
        if not username or password is None:
            raise WebsiteCredentialError("username_password 凭据必须包含 username 与 password")
        return json.dumps({"username": username, "password": password}, ensure_ascii=False)
    raise WebsiteCredentialError(f"不支持的凭据类型: {ctype}")


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


class WebsiteCredentialService:
    def __init__(self, db: Any, vault: CredentialVault) -> None:
        self._db = db
        self._vault = vault
        self._repo = CredentialRepository(db)

    # ---- 存储 / 列表 / 删除（API 层调用）----

    def store(
        self,
        *,
        user_id: int,
        task_id: int,
        ctype: str,
        payload: dict,
        scope: str,
        domain: str,
    ) -> dict:
        if scope not in WEBSITE_SCOPES:
            raise WebsiteCredentialError(f"无效 scope: {scope}")
        domain = domain.strip().lower().lstrip(".")
        if not domain:
            raise WebsiteCredentialError("必须指定 domain")
        secret_json = _validate_payload(ctype, payload)
        task_id_bound = task_id if scope == "CURRENT_TASK" else None
        info = self._vault.store_website_secret(
            user_id=user_id,
            kind=ctype,
            name=f"website-{domain}-{ctype}",
            secret_json=secret_json,
            domain=domain,
            scope=scope,
            task_id=task_id_bound,
        )
        cred = self._repo.get_owned(user_id, info.credential_id)
        return _metadata(cred)

    def store_from_saved(self, *, user_id: int, task_id: int, saved_credential_id: int) -> dict:
        """把已保存的 SAVED_DOMAIN 凭据复制为本任务 CURRENT_TASK 凭据（仍须 Approval）。

        复制在 vault 内完成（decrypt → encrypt），明文不离开后端执行路径。
        """
        saved = self._repo.get_owned(user_id, saved_credential_id)
        if saved.scope != "SAVED_DOMAIN":
            raise WebsiteCredentialError("只能从 SAVED_DOMAIN 凭据复制")
        active = self._vault.get_active(user_id=user_id, credential_id=saved.id)
        if active is None:
            raise WebsiteCredentialError("已保存凭据已失效")
        secret = self._vault.read_for_execution(user_id=user_id, credential_version_id=active.id)
        task_id_bound = task_id
        info = self._vault.store_website_secret(
            user_id=user_id,
            kind=saved.kind,
            name=f"website-{saved.domain}-{saved.kind}",
            secret_json=secret,
            domain=saved.domain or "",
            scope="CURRENT_TASK",
            task_id=task_id_bound,
        )
        cred = self._repo.get_owned(user_id, info.credential_id)
        return _metadata(cred)

    def list_for_task(self, *, user_id: int, task_id: int) -> list[dict]:
        return [_metadata(c) for c in self._repo.list_website_for_task(user_id, task_id)]

    def list_saved_for_user(self, *, user_id: int) -> list[dict]:
        return [_metadata(c) for c in self._repo.list_saved_for_user(user_id)]

    def delete(self, *, user_id: int, credential_id: int) -> None:
        self._repo.get_owned(user_id, credential_id)
        self._vault.revoke(user_id=user_id, credential_id=credential_id)
        self._repo.delete_owned(user_id, credential_id)

    # ---- 执行期（Activity 内，仅受控路径）----

    def resolve_ref(self, *, user_id: int, task_id: int, domain: str) -> dict | None:
        """返回本任务 CURRENT_TASK 凭据的脱敏引用（无明文）。

        可用性由 Frontier 状态机保证：WAITING_CREDENTIAL → (approval consumed) → READY_FOR_FETCH。
        """
        cred = self._repo.resolve_for_domain(user_id, task_id, domain)
        if cred is None:
            return None
        return {
            "credential_id": cred.id,
            "type": cred.kind,
            "domain": cred.domain,
            "scope": cred.scope,
        }

    def read_for_execution(self, *, user_id: int, credential_id: int) -> dict:
        """仅 Activity 内调用：解密 → dict；进程内存即用即弃，绝不持久化/日志。"""
        cred = self._repo.get_owned(user_id, credential_id)
        active = self._vault.get_active(user_id=user_id, credential_id=cred.id)
        if active is None:
            raise WebsiteCredentialError("凭据已失效")
        secret = self._vault.read_for_execution(
            user_id=user_id, credential_version_id=active.id
        )
        return json.loads(secret)

    def build_headers(
        self, *, user_id: int, credential_ref: dict, url: str
    ) -> dict[str, str] | None:
        """把凭据附着为请求头（Cookie / Basic Auth），带 domain 范围检查（四十一）。

        Cookie 只对匹配 host 的域名生效；不匹配 → 不发送，绝不把 a.com 凭据发给 b.com。
        """
        target_host = _host_of(url)
        if not target_host:
            return None
        ctype = credential_ref.get("type")
        secret = self.read_for_execution(
            user_id=user_id,
            credential_id=credential_ref["credential_id"],
        )
        if ctype == "cookie":
            pairs = []
            for c in secret.get("cookies") or []:
                cdomain = str(c.get("domain", "")).strip().lower().lstrip(".")
                if not cdomain or cdomain == target_host or target_host.endswith("." + cdomain):
                    pairs.append(f"{c['name']}={c['value']}")
            if not pairs:
                return None
            return {"cookie": "; ".join(pairs)}
        if ctype == "username_password":
            username = secret.get("username", "")
            password = secret.get("password", "")
            token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
            return {"authorization": f"Basic {token}"}
        return None
