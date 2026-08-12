"""DEPLOY-GATE-3 回归：ExtractionModelResolver 必须从 PlanVersion 的
model_config_id **列**（而非 payload graph JSON）读取冻结模型，并以 User 对象
（而非 run.user_id int）调用 ProviderService。

根因（上海政府真实链暴露）：resolve_for_run 读 plan.payload["model_config_id"]
恒为 None → 落入 require_available_model_config(run.user_id)，该方法需要 user.id
而收到 int → AttributeError 被吞 → 返回 None → SemanticExtractionAgent 落到
placeholder provider → LLM 提取全部失败（"不支持的推理 Provider: placeholder"）。
"""

from __future__ import annotations

from app.domain.repository import PlanVersionRepository
from app.extraction.model_resolver import ExtractionModelResolver


class _FakeConfig:
    config_id = "cfg-model-1"
    version = 3
    provider_type = "deepseek"
    model_name = "deepseek-chat"
    base_url = None
    credential_version_id = 7  # 非 None → 走 vault 解密 api_key 路径
    connection_status = "available"


class _FakeProviderService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get_model_config_version(self, user, config_id, version):
        assert hasattr(user, "id"), "resolve_for_run 必须传 User 对象而非 run.user_id int"
        self.calls.append(("get_version", config_id, version))
        return _FakeConfig()

    def require_available_model_config(self, user):
        assert hasattr(user, "id"), "resolve_for_run 必须传 User 对象而非 run.user_id int"
        self.calls.append(("require_default",))
        return _FakeConfig()


class _FakeVault:
    def read_for_execution(self, user_id, credential_version_id):
        return "sk-test"


def _create_plan(db, user_id, task_id, *, model_config_id, model_config_version, payload):
    return PlanVersionRepository(db).create(
        user_id=user_id,
        task_id=task_id,
        spec_version=1,
        version=1,
        payload=payload,
        validation_status="VALID",
        plan_fingerprint="fp",
        model_config_id=model_config_id,
        model_config_version=model_config_version,
        registry_versions={},
    )


def test_resolve_for_run_reads_model_config_id_from_plan_column(ctx) -> None:
    """冻结 plan 的 model_config_id 存在**列**（persist_plan 只把 graph 放 payload）。
    读取 payload 恒为 None → 错误回退 require_default；必须读 plan.model_config_id 列。"""
    db, user, run = ctx["db"], ctx["user"], ctx["run"]
    _create_plan(
        db,
        user.id,
        run.task_id,
        model_config_id="cfg-model-1",
        model_config_version=3,
        payload={"graph": {"nodes": []}},  # payload 不含 model_config_id
    )
    provider = _FakeProviderService()
    resolver = ExtractionModelResolver(db, provider_service=provider, vault=_FakeVault())

    resolved, api_key, audit = resolver.resolve_for_run(run)

    assert resolved is not None
    assert resolved.provider_type == "deepseek"
    assert resolved.model_name == "deepseek-chat"
    assert api_key == "sk-test"
    assert provider.calls == [("get_version", "cfg-model-1", 3)]
    assert audit == {
        "model_config_id": "cfg-model-1",
        "model_config_version": 3,
        "provider": "deepseek",
        "model": "deepseek-chat",
    }


def test_resolve_for_run_falls_back_to_default_with_user_object(ctx) -> None:
    """无冻结 model_config_id 列时回退 require_available_model_config，且必须传 User 对象。"""
    db, user, run = ctx["db"], ctx["user"], ctx["run"]
    _create_plan(
        db,
        user.id,
        run.task_id,
        model_config_id=None,
        model_config_version=None,
        payload={"graph": {"nodes": []}},
    )
    provider = _FakeProviderService()
    resolver = ExtractionModelResolver(db, provider_service=provider, vault=_FakeVault())

    resolved, api_key, audit = resolver.resolve_for_run(run)

    assert resolved is not None
    assert provider.calls == [("require_default",)]
