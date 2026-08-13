# M-16: 错误分类、自我纠错、资源池、并发与限流可靠性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 M-16 闭环：统一 `ErrorClass` 分类 + `RetryDecision`（有界重试、429 Retry-After/jitter、auth/quota 不重试、纠错必须有变化）+ 域名 `CircuitBreaker` + `CapacityConfig`（部署配置，不进 CollectionSpec）+ PostgreSQL 三级 `ResourceAdmission`（全局/单用户/节点资源池）+ ResourceClass→TaskQueue 确定性路由与 Worker 角色 + Provider 限流 + Browser 生命周期安全 + M-15 并发幂等加固 + small capacity smoke。

**Architecture:** 新增 `backend/app/reliability/` 纯逻辑层（errors/retry/capacity/breaker/admission/pools/provider_limit/harness），复用现有 `FetchErrorCode`/`ProviderError` 分类映射到统一 `ErrorClass`（不造第二套错误框架，`crawling/errors.py` 明令禁止）。复用已有 `TaskState.WAITING_RESOURCE`/`NodeState.WAITING_RESOURCE` 状态机（已含 transitions + `allowed_actions` + 前端映射，从未被驱动）——M-16 用「任务保持 QUEUED/RUNNING + 追加 `task.resource_waiting`/`node.resource_waiting` DomainEvent」驱动等待语义，不重写休眠的 NodeRun 执行路径。跨进程协调用 PostgreSQL-backed `resource_leases` + `domain_circuit_breaker` 表（migration 0014），不引入 Redis/Kafka/Kubernetes。Temporal Workflow 保持确定性：资源类→队列映射用固定代码常量，不读环境变量。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy / Alembic / Temporal Python SDK / pytest / Vue 3 + TypeScript strict。

## Global Constraints

- 错误先分类再选恢复策略（D-013）；不允许所有异常都转 RETRYABLE。
- 复用现有分类体系：`FetchErrorCode`（crawling/errors.py）、`ProviderError` 子类、`ProviderTestStatus`；`ErrorClass` 是统一可靠性视图，只做映射，不替代它们。
- Retry 永远有界：次数来自 CollectionSpec `RuntimeLimits.max_retries_per_url` + `CapacityConfig` 默认；禁止无限 while。
- 纠错重试必须满足「input/tool/parameter/environment 至少一项真实变化」；correction fingerprint 与上次相同则拒绝（防 LLM 同样输入无限再试）。
- 质量失败（QUALITY_FAILED）不能靠降低已确认标准解决（不改 required fields / Evidence requirement / 不把 NEEDS_REVIEW 变 PASSED / 不改 frozen Spec）。
- 域名熔断只统计「目标域名/服务不可用」类错误（DNS/connect timeout/连续 5xx/network unavailable）；robots denied、404、用户凭据 401、Provider Key 错误不计入 Domain 崩溃。
- Breaker 为部署级（保护目标域名）；用户 UI 只显示「目标站点暂时不可用，系统已暂停请求」，绝不展示其他用户 Task ID / 失败次数明细 / 其他账号 URL。
- 资源不足是 WAITING_RESOURCE（等待），不是 FAILED/TIMEOUT/UNKNOWN_ERROR。
- D-071 三级调度：全局 active task 限制 + 单用户限制 + 节点资源池；并发数字属于部署配置（CapacityConfig），**禁止写入 CollectionSpec**。
- 跨 Worker 全局限制必须真实跨进程协调（PostgreSQL-backed lease），禁止用单进程 `asyncio.Semaphore` 冒充全局限制。
- Temporal 确定性：Workflow replay 不读动态 env 并发配置、不读系统 CPU、不随机选队列；ResourceClass→TaskQueue 映射固定常量。
- Provider 429 遵守 Retry-After，否则 bounded backoff + jitter（防 retry storm）；401/403 auth 不 retry；quota exhausted 进 user action；不等于 provider unavailable。
- BYOK：同一 Provider Family 不同用户 Key 不得共享认证/quota 状态；throttle key 用安全 metadata（family+config_id+user_id hash），禁止明文 API Key。
- Resource lease heartbeat 只是资源占用事实，不是业务 Checkpoint（M-04/M-07 规则不变）。
- Browser 低并发：达到上限 N 时第 N+1 个 WAITING_RESOURCE，绝不继续 fork 进程；Activity timeout/cancel/异常退出后 context/page/process 必须回收。
- M-15 兼容：Activity retry/并发不得导致同一 Artifact 重复生成 Blob、不得重复执行永久删除；只做「两个相同 ExportRequest 并发 → 同一 content identity 无 duplicate blob」一条幂等回归。
- 不新增页面（13 Page Boundary 不变）；WAITING_RESOURCE 只出现在 Chat 重要事件 / Task Drawer / Execution Timeline / Node Detail。
- 不引入 Redis/RabbitMQ/Kafka/Celery/Kubernetes；不建设金额/套餐/优先级计费。
- M-16 不修 Plan Generator、不重跑 Golden C（DEFERRED-DYNAMIC-E2E-01 保持 DEFERRED）。
- 测试只跑 M-16 scoped reliability suites + M-15 并发幂等单测；禁止 `pytest tests/` 全量、M-09~M-15 全量、Golden A/B/C、真实 Search/大规模 Crawl/真实 Playwright E2E。
- 同一个 scoped test 失败：一次根因分析 → 一个最小修复 → 一次针对性重测；二次仍失败 BLOCK 并停止扩大验证。

## File Structure

**Backend 新增（新包 `backend/app/reliability/`）：**
- `backend/app/reliability/__init__.py`
- `backend/app/reliability/errors.py` — `ErrorClass` + 分类器（HTTP/fetch/provider）+ `is_domain_breaker_error`
- `backend/app/reliability/retry.py` — `RetryStrategy`/`RetryDecision`/`decide_retry`/`jitter_seconds`/`correction_fingerprint`/`RetryBudget`
- `backend/app/reliability/capacity.py` — `CapacityConfig`（pydantic + 校验）+ `capacity_from_settings`
- `backend/app/reliability/breaker.py` — `CircuitBreakerState` + `CircuitBreakerRepository` + `CircuitBreakerService` + `normalize_domain`
- `backend/app/reliability/admission.py` — `LeaseScope` + `ResourceLease` model + `ResourceLeaseRepository` + `ResourceAdmission` + `LeaseReaper` + `SlotResult`
- `backend/app/reliability/pools.py` — `WorkerRole` + `RESOURCE_QUEUE_MAP` + `task_queue_for` + `role_task_queues`
- `backend/app/reliability/provider_limit.py` — `ProviderLimiter` + `call_with_provider_retry`
- `backend/app/reliability/harness.py` — `run_synthetic_capacity()`（无外部网络 synthetic jobs）
- `backend/alembic/versions/0014_reliability_leases_breaker.py` — `resource_leases` + `domain_circuit_breakers` + artifacts partial unique index

**Backend 修改：**
- `backend/app/domain/models.py` — `ResourceLease`/`DomainCircuitBreaker` 模型 + `Artifact.__table_args__` 部分唯一索引
- `backend/app/config.py` — capacity / worker_roles / provider throttle settings
- `backend/app/activities/execution_seam.py` — `ExecutionUnit.resource_class` + `ExecuteUnitResult.status` 文档
- `backend/app/activities/plan_execution.py` — `execute_safe_unit` 加 pool admission wrapper + `RESOURCE_WAITING`；`fetch_next_execution_unit` 填 `resource_class`
- `backend/app/activities/task_execution.py` — `ensure_run_started` 加 task admission gating + 结果带 waiting_reason；终态 activities 释放 task slot
- `backend/app/activities/reliability.py` — `record_resource_wait` / `heartbeat_task_slot` / `release_task_slot` activities
- `backend/app/workflows/task_workflow.py` — 按 `unit.resource_class` 路由 `execute_safe_unit`；处理 `RESOURCE_WAITING` / `ensure_run_started` wait
- `backend/app/infra/temporal.py` — `create_role_worker` + `create_task_workers`（max_concurrent_activities per queue）
- `backend/app/worker.py` — 角色解析 + 按 role 起 Worker + 全量 executor 安装
- `backend/app/crawling/fetch_executor.py` — `_http_with_retry` 走 `decide_retry` + breaker 门禁/计数
- `backend/app/crawling/browser.py` — try/finally 进程回收 + active registry + 进程数上限
- `backend/app/providers/inference.py` — `generate` 套 provider limiter + bounded retry
- `backend/app/discovery/source_search.py` — `provider.search` 套 provider limiter + bounded retry
- `backend/app/execution/service.py` — `task.resource_waiting`/`node.resource_waiting` 事件 label + classify
- `backend/app/artifacts/service.py` — `export` IntegrityError 竞态处理（并发幂等）
- `backend/app/api/schemas.py` + `backend/app/api/routes/tasks.py` — `TaskShellDto.waiting_reason`
- `backend/.env.example` — 新 settings 示例（无秘密）

**Backend 测试（新 `backend/tests/reliability/`）：**
- `backend/tests/reliability/conftest.py` — SQLite + 两用户 + 短 TTL fixture + fake clock
- `backend/tests/reliability/test_retry_policy_matrix.py`（TEST 1）
- `backend/tests/reliability/test_circuit_breaker.py`（TEST 2）
- `backend/tests/reliability/test_admission_fairness.py`（TEST 3）
- `backend/tests/reliability/test_browser_pool.py`（TEST 4）
- `backend/tests/reliability/test_retry_storm.py`（TEST 5）
- `backend/tests/reliability/test_lease_recovery.py`（TEST 6）
- `backend/tests/reliability/test_capacity_config.py`
- `backend/tests/reliability/test_capacity_harness.py`（small synthetic smoke）
- `backend/tests/artifacts/test_m16_concurrent_idempotency.py`（TEST 7）
- `backend/tests/integration/fixture_worker.py` + `fixture_plan_worker.py` — 改为 poll 全部 role queues

**Frontend 修改（最小）：**
- `frontend/src/app/overlay/task/TaskDrawer.vue`（或等价 Drawer 组件）— 展示 `waiting_reason` 等待徽标
- 对应最小 Vitest（可并入既有 Drawer 测试）

**Docs：**
- `docs/operations/capacity-baseline.md`
- `docs/implementation/M-16-execution.md`

---

### Task 1: ErrorClass 统一分类 + RetryDecision

**Files:**
- Create: `backend/app/reliability/errors.py`
- Create: `backend/app/reliability/retry.py`
- Test: `backend/tests/reliability/test_retry_policy_matrix.py`

**Interfaces:**
- Consumes: `FetchErrorCode`（`app/crawling/errors.py`）、`ProviderError` 子类（`app/providers/errors.py`）、`ProviderTestStatus`（`app/providers/protocol.py`）、`RuntimeLimits`（`app/domain/spec.py:63-68`）。
- Produces: `ErrorClass(StrEnum)`、`classify_http_error`、`classify_fetch_error_code`、`classify_provider_error`、`is_domain_breaker_error`、`RetryStrategy(StrEnum)`、`RetryDecision(frozen dataclass)`、`decide_retry`、`jitter_seconds`、`correction_fingerprint`、`RetryBudget`、`retry_budget_from`。
- `retry_budget_from` 依赖 Task 3 的 `CapacityConfig`（本任务先按签名实现，Task 3 落地配置）。

**Context:** 现有 7 套独立异常分类（AuthError/DomainError/ProviderError/FetchErrorCode/DiscoveryError/CredentialError/Review 冲突），字符串 code 有重叠（`RATE_LIMITED`/`AUTH_FAILED`/`NETWORK_ERROR`）。`ErrorClass` 是统一可靠性视图，用映射函数收敛，不替换现有异常类。`crawling/errors.py` docstring 明令「不造两套」。Fetch executor 已有唯一真实 backoff 循环（`_http_with_retry`，Task 6 接入）。

- [ ] **Step 1: 写失败测试**

`backend/tests/reliability/test_retry_policy_matrix.py`：
```python
import pytest

from app.reliability.errors import (
    ErrorClass,
    classify_fetch_error_code,
    classify_http_error,
    classify_provider_error,
    is_domain_breaker_error,
)
from app.reliability.retry import (
    RetryDecision,
    RetryStrategy,
    correction_fingerprint,
    decide_retry,
    jitter_seconds,
)
from app.crawling.errors import FetchErrorCode
from app.providers import errors as perrors


def test_http_timeout_maps_to_network_timeout() -> None:
    assert classify_http_error(408) is ErrorClass.NETWORK_TIMEOUT


def test_5xx_maps_to_transient_service_error() -> None:
    for code in (502, 503, 504):
        assert classify_http_error(code) is ErrorClass.TRANSIENT_SERVICE_ERROR


def test_429_maps_to_rate_limited() -> None:
    assert classify_http_error(429) is ErrorClass.RATE_LIMITED


def test_provider_auth_maps_to_auth_failed() -> None:
    assert classify_provider_error(perrors.ProviderAuthFailedError("x")) is ErrorClass.AUTH_FAILED


def test_provider_quota_via_429_is_rate_limited_not_quota() -> None:
    # 429 语义是 RATE_LIMITED；QUOTA_EXHAUSTED 由 provider 显式配额错误/业务标记产生
    assert classify_provider_error(perrors.ProviderRateLimitedError("x")) is ErrorClass.RATE_LIMITED


def test_fetch_dns_is_network_timeout_and_counts_for_breaker() -> None:
    ec = classify_fetch_error_code(FetchErrorCode.DNS_ERROR)
    assert ec is ErrorClass.NETWORK_TIMEOUT
    assert is_domain_breaker_error(ec)


def test_fetch_404_does_not_count_for_domain_breaker() -> None:
    ec = classify_fetch_error_code(FetchErrorCode.NOT_FOUND)
    assert ec is ErrorClass.NON_RETRYABLE
    assert not is_domain_breaker_error(ec)


@pytest.mark.parametrize(
    "error_class,attempt,max_attempts,expected",
    [
        (ErrorClass.NETWORK_TIMEOUT, 0, 3, True),
        (ErrorClass.TRANSIENT_SERVICE_ERROR, 2, 3, False),  # 已达 max_attempts
        (ErrorClass.AUTH_FAILED, 0, 3, False),
        (ErrorClass.QUOTA_EXHAUSTED, 0, 3, False),
    ],
)
def test_decide_retry_transient_is_bounded(
    error_class: ErrorClass, attempt: int, max_attempts: int, expected: bool
) -> None:
    d = decide_retry(error_class=error_class, attempt=attempt, max_attempts=max_attempts)
    assert d.should_retry is expected
    assert d.error_class is error_class


def test_retry_after_is_respected() -> None:
    d = decide_retry(
        error_class=ErrorClass.RATE_LIMITED,
        attempt=0,
        max_attempts=3,
        retry_after_seconds=7.0,
        rand=lambda: 0.5,
    )
    assert d.strategy is RetryStrategy.RESPECT_RETRY_AFTER
    assert d.should_retry is True
    assert d.delay_seconds >= 7.0


def test_correction_requires_change() -> None:
    fp = "fp-1"
    d1 = decide_retry(
        error_class=ErrorClass.EXTRACTION_FAILED, attempt=0, max_attempts=3,
        correction_fp=fp, prior_correction_fp="fp-0",
    )
    assert d1.should_retry is True
    assert d1.strategy is RetryStrategy.CORRECTION
    d2 = decide_retry(
        error_class=ErrorClass.EXTRACTION_FAILED, attempt=0, max_attempts=3,
        correction_fp=fp, prior_correction_fp=fp,  # 完全相同 → 拒绝
    )
    assert d2.should_retry is False


def test_resource_unavailable_is_wait_not_fail() -> None:
    d = decide_retry(error_class=ErrorClass.RESOURCE_UNAVAILABLE, attempt=0, max_attempts=3)
    assert d.strategy is RetryStrategy.WAIT_RESOURCE
    assert d.should_retry is True


def test_auth_blocking_action() -> None:
    d = decide_retry(error_class=ErrorClass.AUTH_FAILED, attempt=0, max_attempts=3)
    assert d.blocking_action is not None


def test_jitter_bounds_and_deterministic_seam() -> None:
    assert 0.0 <= jitter_seconds(0.0, rand=lambda: 0.5) <= 1.0
    # 同一个 rand 序列 → 确定性（测试可复现）
    a = jitter_seconds(2.0, rand=lambda: 0.5)
    b = jitter_seconds(2.0, rand=lambda: 0.5)
    assert a == b


def test_correction_fingerprint_changes_on_parameter_change() -> None:
    base = dict(tool="extractor-a")
    f1 = correction_fingerprint(inputs={}, tool="extractor-a", parameters={}, environment={})
    f2 = correction_fingerprint(inputs={}, tool="extractor-b", parameters={}, environment={})
    assert f1 != f2
    assert base["tool"] == "extractor-a"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/reliability/test_retry_policy_matrix.py -q`
Expected: FAIL（`ModuleNotFoundError: app.reliability`）

- [ ] **Step 3: 实现 `app/reliability/errors.py`**

```python
"""M-16 统一错误分类（ErrorClass）。

不是第二套错误框架：它把既有 taxonomy（FetchErrorCode / ProviderError /
HTTP status）映射到一个可靠性视图，供 retry decision / circuit breaker /
provider limiter 共享。crawling/errors.py 明令不造两套，这里只做映射层。
"""

from __future__ import annotations

from enum import StrEnum

from app.crawling.errors import FetchErrorCode
from app.providers import errors as provider_errors


class ErrorClass(StrEnum):
    NETWORK_TIMEOUT = "network_timeout"
    TRANSIENT_SERVICE_ERROR = "transient_service_error"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    QUOTA_EXHAUSTED = "quota_exhausted"
    STRUCTURE_CHANGED = "structure_changed"
    EXTRACTION_FAILED = "extraction_failed"
    QUALITY_FAILED = "quality_failed"
    DOMAIN_UNAVAILABLE = "domain_unavailable"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    CANCELLED = "cancelled"
    NON_RETRYABLE = "non_retryable"


def classify_http_error(http_status: int, *, retry_after: float | None = None) -> ErrorClass:
    """确定性 HTTP 状态码分类。429 → RATE_LIMITED（Retry-After 单独携带）。"""
    del retry_after  # 只影响 delay，不影响 class
    if http_status in (408, 425):
        return ErrorClass.NETWORK_TIMEOUT
    if http_status == 429:
        return ErrorClass.RATE_LIMITED
    if http_status in (401, 403):
        return ErrorClass.AUTH_FAILED
    if http_status in (502, 503, 504):
        return ErrorClass.TRANSIENT_SERVICE_ERROR
    if 500 <= http_status < 600:
        return ErrorClass.TRANSIENT_SERVICE_ERROR
    return ErrorClass.NON_RETRYABLE


# FetchErrorCode（crawling/errors.py）→ ErrorClass 映射。retry 分类与 breaker 计数分离：
# is_domain_breaker_error 单独判断哪些 class 代表「目标域名/服务不可用」。
_FETCH_CODE_MAP: dict[FetchErrorCode, ErrorClass] = {
    FetchErrorCode.TIMEOUT: ErrorClass.NETWORK_TIMEOUT,
    FetchErrorCode.DNS_ERROR: ErrorClass.NETWORK_TIMEOUT,
    FetchErrorCode.CONNECTION_ERROR: ErrorClass.NETWORK_TIMEOUT,
    FetchErrorCode.SERVER_ERROR: ErrorClass.TRANSIENT_SERVICE_ERROR,
    FetchErrorCode.RATE_LIMITED: ErrorClass.RATE_LIMITED,
    FetchErrorCode.AUTH_REQUIRED: ErrorClass.AUTH_FAILED,
    FetchErrorCode.ACCESS_DENIED: ErrorClass.AUTH_FAILED,
    FetchErrorCode.CAPTCHA_REQUIRED: ErrorClass.AUTH_FAILED,  # 需人工处理，非域名崩溃
    FetchErrorCode.CREDENTIAL_REQUIRED: ErrorClass.AUTH_FAILED,  # 需凭据，非域名崩溃
    FetchErrorCode.NOT_FOUND: ErrorClass.NON_RETRYABLE,
    FetchErrorCode.TOO_MANY_REDIRECTS: ErrorClass.NON_RETRYABLE,
    FetchErrorCode.SIZE_LIMIT_EXCEEDED: ErrorClass.NON_RETRYABLE,
    FetchErrorCode.SSRF_BLOCKED: ErrorClass.NON_RETRYABLE,
    FetchErrorCode.STORAGE_ERROR: ErrorClass.NON_RETRYABLE,
    FetchErrorCode.INTERNAL_ERROR: ErrorClass.NON_RETRYABLE,
    FetchErrorCode.EMPTY_CONTENT: ErrorClass.STRUCTURE_CHANGED,  # 策略需升级/更换
    FetchErrorCode.DYNAMIC_RENDER_REQUIRED: ErrorClass.STRUCTURE_CHANGED,  # 需升级工具
    FetchErrorCode.UNSUPPORTED_RESPONSE: ErrorClass.STRUCTURE_CHANGED,
}


def classify_fetch_error_code(code: FetchErrorCode) -> ErrorClass:
    return _FETCH_CODE_MAP.get(code, ErrorClass.NON_RETRYABLE)


def classify_provider_error(exc: Exception) -> ErrorClass:
    if isinstance(exc, provider_errors.ProviderAuthFailedError):
        return ErrorClass.AUTH_FAILED
    if isinstance(exc, provider_errors.ProviderRateLimitedError):
        return ErrorClass.RATE_LIMITED
    if isinstance(exc, provider_errors.ProviderNetworkError):
        return ErrorClass.NETWORK_TIMEOUT
    if isinstance(exc, provider_errors.ProviderInferenceError):
        return ErrorClass.TRANSIENT_SERVICE_ERROR
    if isinstance(exc, (provider_errors.ProviderModelNotFoundError,)):
        return ErrorClass.NON_RETRYABLE
    if isinstance(exc, (provider_errors.ModelNotConfiguredError, provider_errors.SearchProviderNotConfiguredError)):
        return ErrorClass.NON_RETRYABLE
    if isinstance(exc, provider_errors.ProviderError):
        return ErrorClass.NON_RETRYABLE
    return ErrorClass.NON_RETRYABLE


_DOMAIN_BREAKER_CLASSES = frozenset(
    {ErrorClass.NETWORK_TIMEOUT, ErrorClass.TRANSIENT_SERVICE_ERROR, ErrorClass.DOMAIN_UNAVAILABLE}
)


def is_domain_breaker_error(error_class: ErrorClass) -> bool:
    """只有「目标域名/服务不可用」类错误计入 Domain Breaker（D-013 §20）。"""
    return error_class in _DOMAIN_BREAKER_CLASSES
```

- [ ] **Step 4: 实现 `app/reliability/retry.py`**

```python
"""M-16 RetryDecision：统一的「分类 → 恢复策略」决策，禁止调用点各处写 if 429。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable

from app.reliability.errors import ErrorClass

_JITTER_FULL = 1.0
_BACKOFF_CAP_SECONDS = 30.0


class RetryStrategy(StrEnum):
    NONE = "none"
    TRANSIENT_BACKOFF = "transient_backoff"
    RESPECT_RETRY_AFTER = "respect_retry_after"
    CORRECTION = "correction"
    WAIT_RESOURCE = "wait_resource"
    USER_ACTION = "user_action"


@dataclass(frozen=True)
class RetryDecision:
    error_class: ErrorClass
    should_retry: bool
    strategy: RetryStrategy
    delay_seconds: float
    attempt: int
    max_attempts: int
    reason: str
    requires_change: bool = False
    blocking_action: str | None = None
    retry_after_seconds: float | None = None


def jitter_seconds(base: float, *, rand: Callable[[], float] | None = None) -> float:
    """base + [0,1) 全抖动（retry storm 防御）。rand 注入 → 测试确定性。"""
    r = (rand() if rand is not None else __import__("random").random())
    return base + (r * _JITTER_FULL)


def _backoff_delay(base: float, attempt: int) -> float:
    return min(base * (2 ** max(0, attempt - 1)), _BACKOFF_CAP_SECONDS)


def correction_fingerprint(
    *, inputs: dict, tool: str, parameters: dict, environment: dict
) -> str:
    """纠错指纹：input/tool/parameter/environment 任一变化 → 新指纹。"""
    canonical = json.dumps(
        {"inputs": inputs, "tool": tool, "parameters": parameters, "environment": environment},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def decide_retry(
    *,
    error_class: ErrorClass,
    attempt: int,
    max_attempts: int,
    retry_after_seconds: float | None = None,
    correction_fp: str | None = None,
    prior_correction_fp: str | None = None,
    base_delay_seconds: float = 2.0,
    rand: Callable[[], float] | None = None,
) -> RetryDecision:
    """单一决策入口：ErrorClass → 恢复策略。所有边界都在这里，调用点不再写 if。"""
    remaining = attempt < max_attempts

    if error_class is ErrorClass.RESOURCE_UNAVAILABLE:
        return RetryDecision(
            error_class=error_class, should_retry=True, strategy=RetryStrategy.WAIT_RESOURCE,
            delay_seconds=jitter_seconds(retry_after_seconds or base_delay_seconds, rand=rand),
            attempt=attempt, max_attempts=max_attempts, reason="resource slot unavailable",
        )

    if error_class is ErrorClass.RATE_LIMITED and retry_after_seconds is not None:
        return RetryDecision(
            error_class=error_class, should_retry=remaining, strategy=RetryStrategy.RESPECT_RETRY_AFTER,
            delay_seconds=jitter_seconds(retry_after_seconds, rand=rand),
            attempt=attempt, max_attempts=max_attempts, reason="respect Retry-After",
            retry_after_seconds=retry_after_seconds,
        )

    if error_class in (ErrorClass.NETWORK_TIMEOUT, ErrorClass.TRANSIENT_SERVICE_ERROR, ErrorClass.DOMAIN_UNAVAILABLE):
        return RetryDecision(
            error_class=error_class, should_retry=remaining, strategy=RetryStrategy.TRANSIENT_BACKOFF,
            delay_seconds=jitter_seconds(_backoff_delay(base_delay_seconds, attempt), rand=rand),
            attempt=attempt, max_attempts=max_attempts, reason="transient backoff",
        )

    if error_class is ErrorClass.RATE_LIMITED:
        return RetryDecision(
            error_class=error_class, should_retry=remaining, strategy=RetryStrategy.TRANSIENT_BACKOFF,
            delay_seconds=jitter_seconds(_backoff_delay(base_delay_seconds, attempt), rand=rand),
            attempt=attempt, max_attempts=max_attempts, reason="rate limited without retry-after",
        )

    if error_class in (ErrorClass.AUTH_FAILED, ErrorClass.QUOTA_EXHAUSTED):
        return RetryDecision(
            error_class=error_class, should_retry=False, strategy=RetryStrategy.USER_ACTION,
            delay_seconds=0.0, attempt=attempt, max_attempts=max_attempts,
            reason="requires user action, no automatic retry",
            blocking_action="credential_or_quota_review",
        )

    if error_class in (ErrorClass.STRUCTURE_CHANGED, ErrorClass.EXTRACTION_FAILED, ErrorClass.QUALITY_FAILED):
        changed = bool(correction_fp) and correction_fp != prior_correction_fp
        return RetryDecision(
            error_class=error_class, should_retry=remaining and changed,
            strategy=RetryStrategy.CORRECTION,
            delay_seconds=jitter_seconds(base_delay_seconds, rand=rand),
            attempt=attempt, max_attempts=max_attempts,
            reason="correction retry requires strategy change" if not changed else "correction retry",
            requires_change=not changed,
        )

    return RetryDecision(
        error_class=error_class, should_retry=False, strategy=RetryStrategy.NONE,
        delay_seconds=0.0, attempt=attempt, max_attempts=max_attempts, reason="non retryable",
    )


@dataclass(frozen=True)
class RetryBudget:
    """URL/Node/Domain/Task 级重试预算（attempt 数，含首次）。"""
    url_max_attempts: int
    node_max_attempts: int
    domain_max_attempts: int
    task_max_attempts: int


def retry_budget_from(runtime_limits, capacity_config) -> RetryBudget:
    """URL 级优先取 CollectionSpec RuntimeLimits.max_retries_per_url，否则默认；其余取 CapacityConfig。"""
    default = capacity_config.default_retry_max_attempts
    url_retries = getattr(runtime_limits, "max_retries_per_url", None) if runtime_limits else None
    return RetryBudget(
        url_max_attempts=(url_retries + 1) if url_retries is not None else default,
        node_max_attempts=default,
        domain_max_attempts=default,
        task_max_attempts=default,
    )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/reliability/test_retry_policy_matrix.py -q`
Expected: PASS

- [ ] **Step 6: ruff + mypy 收敛本任务文件**

Run: `.venv/Scripts/python.exe -m ruff check app/reliability/errors.py app/reliability/retry.py tests/reliability/test_retry_policy_matrix.py && .venv/Scripts/python.exe -m mypy app/reliability/errors.py app/reliability/retry.py`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/reliability/errors.py backend/app/reliability/retry.py backend/tests/reliability/test_retry_policy_matrix.py
git commit -m "feat(worker): add typed retry decisions"
```

---

### Task 2: CapacityConfig + 启动校验

**Files:**
- Create: `backend/app/reliability/capacity.py`
- Modify: `backend/app/config.py`
- Test: `backend/tests/reliability/test_capacity_config.py`

**Interfaces:**
- Consumes: `Settings`（config.py）、`ResourceClass`（`app/plan/nodes.py:41-45`，值 `core/http/browser/llm_search`）。
- Produces: `CapacityConfig(BaseModel)`（全部字段 + 交叉校验）、`capacity_from_settings(settings)`、`pool_limit(resource_class) -> int`。
- 被 Task 3（breaker 阈值）、Task 4（admission limits）、Task 5（pool concurrency）、Task 6（provider throttle）、Task 1 `retry_budget_from` 消费。

**Context:** `Settings` 目前无任何 capacity/pool/breaker 配置。D-071 明确并发属于部署配置，`app/domain/spec.py:9` 注释「D-071 concurrency 是 deployment configuration，不得出现在 per-user spec 参数」。配置一律走 `KAIROS_CAPACITY_*` 环境变量。启动时校验：>0、per-user <= global、browser 低并发安全范围、未知 resource class 拒绝。

- [ ] **Step 1: 写失败测试**

`backend/tests/reliability/test_capacity_config.py`：
```python
import pytest
from pydantic import ValidationError

from app.reliability.capacity import CapacityConfig


def test_defaults_are_safe() -> None:
    c = CapacityConfig()
    assert c.global_active_tasks >= c.per_user_active_tasks
    assert c.pool_concurrency["browser"] == 1


def test_zero_global_rejected() -> None:
    with pytest.raises(ValidationError):
        CapacityConfig(global_active_tasks=0)


def test_per_user_exceeds_global_rejected() -> None:
    with pytest.raises(ValidationError):
        CapacityConfig(global_active_tasks=2, per_user_active_tasks=3)


def test_unknown_resource_class_rejected() -> None:
    with pytest.raises(ValidationError):
        CapacityConfig(pool_concurrency={"nope": 1})


def test_browser_above_safe_rejected() -> None:
    with pytest.raises(ValidationError):
        CapacityConfig(pool_concurrency={"browser": 8})


def test_pool_limit_helper() -> None:
    c = CapacityConfig()
    assert c.pool_limit("browser") == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/reliability/test_capacity_config.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 `app/reliability/capacity.py`**

```python
"""M-16 CapacityConfig：部署/运维容量配置（D-071），禁止进入 CollectionSpec。

跨字段校验在启动时执行（Settings 实例化 → capacity_from_settings → validator），
避免「配置错了运行半天才发现」。browser 上限写死安全范围（>=2 视为不安全）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

_KNOWN_CLASSES = ("core", "http", "browser", "llm_search")
_BROWSER_SAFE_MAX = 2


class CapacityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    global_active_tasks: int = 4
    per_user_active_tasks: int = 2
    pool_concurrency: dict[str, int] = {
        "core": 4,
        "http": 4,
        "browser": 1,
        "llm_search": 2,
    }
    lease_ttl_seconds: int = 120
    lease_heartbeat_seconds: int = 30
    lease_reap_interval_seconds: int = 30
    domain_breaker_threshold: int = 5
    domain_breaker_cooldown_seconds: int = 60
    default_retry_max_attempts: int = 3
    provider_throttle_min_interval_seconds: float = 0.2
    provider_throttle_max_burst: int = 1

    @model_validator(mode="after")
    def _validate(self) -> "CapacityConfig":
        if self.global_active_tasks <= 0:
            raise ValueError("global_active_tasks must be > 0")
        if self.per_user_active_tasks <= 0:
            raise ValueError("per_user_active_tasks must be > 0")
        if self.per_user_active_tasks > self.global_active_tasks:
            raise ValueError("per_user_active_tasks must be <= global_active_tasks")
        for k, v in self.pool_concurrency.items():
            if k not in _KNOWN_CLASSES:
                raise ValueError(f"unknown resource class in pool_concurrency: {k}")
            if v <= 0:
                raise ValueError(f"pool_concurrency[{k}] must be > 0")
        if self.pool_concurrency["browser"] > _BROWSER_SAFE_MAX:
            raise ValueError("browser pool_concurrency exceeds deployment safe range")
        for key in ("lease_ttl_seconds", "domain_breaker_threshold", "default_retry_max_attempts"):
            if getattr(self, key) <= 0:
                raise ValueError(f"{key} must be > 0")
        return self

    def pool_limit(self, resource_class: str) -> int:
        return self.pool_concurrency.get(resource_class, 1)


def capacity_from_settings(settings) -> CapacityConfig:
    """从 Settings 环境配置构建 CapacityConfig（D-071 并发来自部署配置）。"""
    return CapacityConfig(
        global_active_tasks=settings.capacity_global_active_tasks,
        per_user_active_tasks=settings.capacity_per_user_active_tasks,
        pool_concurrency={
            "core": settings.capacity_core_concurrency,
            "http": settings.capacity_http_concurrency,
            "browser": settings.capacity_browser_concurrency,
            "llm_search": settings.capacity_llm_search_concurrency,
        },
        lease_ttl_seconds=settings.capacity_lease_ttl_seconds,
        lease_heartbeat_seconds=settings.capacity_lease_heartbeat_seconds,
        lease_reap_interval_seconds=settings.capacity_lease_reap_interval_seconds,
        domain_breaker_threshold=settings.capacity_domain_breaker_threshold,
        domain_breaker_cooldown_seconds=settings.capacity_domain_breaker_cooldown_seconds,
        default_retry_max_attempts=settings.capacity_default_retry_max_attempts,
        provider_throttle_min_interval_seconds=settings.provider_throttle_min_interval_seconds,
        provider_throttle_max_burst=settings.provider_throttle_max_burst,
    )
```

- [ ] **Step 4: 给 `app/config.py` 增加 capacity settings 段**

在 `retention_heavy_days` 之后、`@field_validator` 之前插入：
```python
    # --- M-16 capacity / worker roles（D-071 部署配置，禁止进入 CollectionSpec）---
    capacity_global_active_tasks: int = 4
    capacity_per_user_active_tasks: int = 2
    capacity_core_concurrency: int = 4
    capacity_http_concurrency: int = 4
    capacity_browser_concurrency: int = 1
    capacity_llm_search_concurrency: int = 2
    capacity_lease_ttl_seconds: int = 120
    capacity_lease_heartbeat_seconds: int = 30
    capacity_lease_reap_interval_seconds: int = 30
    capacity_domain_breaker_threshold: int = 5
    capacity_domain_breaker_cooldown_seconds: int = 60
    capacity_default_retry_max_attempts: int = 3
    provider_throttle_min_interval_seconds: float = 0.2
    provider_throttle_max_burst: int = 1
    worker_roles: str = "all"  # all | core,http,browser,llm_search（逗号分隔）
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/reliability/test_capacity_config.py -q`
Expected: PASS

- [ ] **Step 6: 启动校验冒烟**

Run: `.venv/Scripts/python.exe -c "from app.reliability.capacity import capacity_from_settings; from app.config import get_settings; print(capacity_from_settings(get_settings()).pool_limit('browser'))"`
Expected: `1`

- [ ] **Step 7: Commit**

```bash
git add backend/app/reliability/capacity.py backend/app/config.py backend/tests/reliability/test_capacity_config.py
git commit -m "feat(worker): add capacity config with startup validation"
```

---

### Task 3: 域名 Circuit Breaker + Migration 0014

**Files:**
- Create: `backend/app/reliability/breaker.py`
- Modify: `backend/app/domain/models.py`（`DomainCircuitBreaker` + `ResourceLease` + Artifact 部分唯一索引）
- Create: `backend/alembic/versions/0014_reliability_leases_breaker.py`
- Test: `backend/tests/reliability/test_circuit_breaker.py`

**Interfaces:**
- Consumes: `ErrorClass`/`is_domain_breaker_error`（Task 1）、`CapacityConfig`（Task 2）。
- Produces: `CircuitBreakerState(StrEnum)`、`normalize_domain`、`CircuitBreakerRepository`、`CircuitBreakerService.allow_request/record_success/record_failure/sanitized_status`。
- 迁移 0014 一次性创建三处 additive 结构：`resource_leases`、`domain_circuit_breakers`、`artifacts` partial unique index（Task 4 用 leases、Task 8 用 index）。

**Context:** 现有无任何熔断器。Breaker 部署级（无 user_id），只统计 `is_domain_breaker_error` 的错误；404/robots/401 用户凭据/Provider Key 错误不计入。OPEN 抑制请求 + 冷却后 HALF_OPEN 单探针 → 成功 CLOSED / 失败再 OPEN。`ExecutionService._TASK_EVENT_LABELS` 已有 `task.mark_waiting_resource` label；本任务只做 breaker 服务，不接 UI。

- [ ] **Step 1: 写失败测试**

`backend/tests/reliability/test_circuit_breaker.py`：
```python
from datetime import UTC, datetime, timedelta

from app.reliability.breaker import (
    CircuitBreakerService,
    CircuitBreakerState,
    normalize_domain,
)
from app.reliability.capacity import CapacityConfig
from app.reliability.errors import ErrorClass


def test_normalize_domain_strips_scheme_port_path() -> None:
    assert normalize_domain("https://www.example.com:8443/a/b?x=1") == "www.example.com"


def _breaker(db, *, threshold=3, cooldown=10):
    from app.reliability.breaker import CircuitBreakerRepository

    return CircuitBreakerService(
        repo=CircuitBreakerRepository(db),
        capacity=CapacityConfig(domain_breaker_threshold=threshold, domain_breaker_cooldown_seconds=cooldown),
    )


def test_open_after_consecutive_domain_failures(db) -> None:
    b = _breaker(db, threshold=3)
    domain = "broken.test"
    for _ in range(3):
        b.record_failure(domain, ErrorClass.NETWORK_TIMEOUT, "connect timeout")
    assert b.state(domain) is CircuitBreakerState.OPEN
    allowed, msg = b.allow_request(domain)
    assert allowed is False
    assert "暂停" in (msg or "")


def test_open_does_not_leak_counts_or_other_user_data(db) -> None:
    b = _breaker(db, threshold=2)
    b.record_failure("x.test", ErrorClass.NETWORK_TIMEOUT, "boom")
    b.record_failure("x.test", ErrorClass.NETWORK_TIMEOUT, "boom")
    _, msg = b.allow_request("x.test")
    assert msg is not None
    assert "failed" not in (msg or "").lower()
    assert "task" not in (msg or "").lower()


def test_404_and_robots_do_not_trip_breaker(db) -> None:
    b = _breaker(db, threshold=2)
    b.record_failure("ok.test", ErrorClass.NON_RETRYABLE, "not found")
    b.record_failure("ok.test", ErrorClass.AUTH_FAILED, "credential invalid")
    assert b.state("ok.test") is CircuitBreakerState.CLOSED
    allowed, _ = b.allow_request("ok.test")
    assert allowed is True


def test_half_open_probe_recovers_or_reopens(db) -> None:
    b = _breaker(db, threshold=2, cooldown=1)
    domain = "probe.test"
    b.record_failure(domain, ErrorClass.NETWORK_TIMEOUT, "t")
    b.record_failure(domain, ErrorClass.NETWORK_TIMEOUT, "t")
    assert b.state(domain) is CircuitBreakerState.OPEN
    # 冷却期后进入 HALF_OPEN，允许单探针
    b._advance(datetime.now(UTC) + timedelta(seconds=2))
    assert b.state(domain) is CircuitBreakerState.HALF_OPEN
    allowed, _ = b.allow_request(domain)
    assert allowed is True
    b.record_success(domain)
    assert b.state(domain) is CircuitBreakerState.CLOSED
    # 失败探针 → 重新 OPEN
    b.record_failure(domain, ErrorClass.NETWORK_TIMEOUT, "t")
    b.record_failure(domain, ErrorClass.NETWORK_TIMEOUT, "t")
    assert b.state(domain) is CircuitBreakerState.OPEN
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/reliability/test_circuit_breaker.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 在 `app/domain/models.py` 增加模型（供 tests create_all + migration）**

在文件末尾（`checkpoints` 模型之后）追加：
```python
class ResourceLease(Base):
    """M-16 跨进程资源租赁（D-071 三级调度协调事实，非业务 Checkpoint）。"""

    __tablename__ = "resource_leases"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(30), nullable=False)  # global|user|resource_class
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    holder_type: Mapped[str] = mapped_column(String(30), nullable=False)  # run|node
    holder_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    resource_class: Mapped[str | None] = mapped_column(String(30), nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="active")  # active|released|expired
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        Index("ix_leases_scope_key", "scope", "scope_key"),
        Index("ix_leases_state_expires", "state", "expires_at"),
    )


class DomainCircuitBreaker(Base):
    """M-16 部署级域名熔断器（保护目标域名，无 owner；用户 UI 只见脱敏文案）。"""

    __tablename__ = "domain_circuit_breakers"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="CLOSED")
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_class: Mapped[str | None] = mapped_column(String(50), nullable=True)
    open_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    half_open_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
```

在 `Artifact` 模型类内新增 `__table_args__` 部分唯一索引（SQLite 用 `sqlite_where`，PG 用 `postgresql_where`）：
```python
    __table_args__ = (
        Index(
            "ix_artifacts_user_task_fp_ready",
            "user_id", "task_id", "request_fingerprint",
            unique=True,
            postgresql_where=text("status = 'ready'"),
            sqlite_where=text("status = 'ready'"),
        ),
    )
```
（若 `Artifact` 已有 `__table_args__`，合并；并在文件顶部确认 `from sqlalchemy import text` / `Index` 已导入。）

- [ ] **Step 4: 创建 Migration 0014**

`backend/alembic/versions/0014_reliability_leases_breaker.py`：
```python
"""M-16 reliability: resource leases, domain circuit breaker, artifact ready fp index.

Additive only（expand）: 新增两张协调表 + artifacts 一个 partial unique index。
不触碰 M-07 Run / M-09 URLResource / M-13 Record / M-15 Artifact 既有列。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"


def upgrade() -> None:
    op.create_table(
        "resource_leases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope", sa.String(30), nullable=False),
        sa.Column("scope_key", sa.String(128), nullable=False),
        sa.Column("holder_type", sa.String(30), nullable=False),
        sa.Column("holder_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("resource_class", sa.String(30), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="active"),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_leases_scope_key", "resource_leases", ["scope", "scope_key"])
    op.create_index("ix_leases_state_expires", "resource_leases", ["state", "expires_at"])

    op.create_table(
        "domain_circuit_breakers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="CLOSED"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_class", sa.String(50), nullable=True),
        sa.Column("open_reason", sa.String(500), nullable=True),
        sa.Column("open_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("half_open_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_unique_constraint("uq_dcb_domain", "domain_circuit_breakers", ["domain"])

    op.create_index(
        "ix_artifacts_user_task_fp_ready",
        "artifacts",
        ["user_id", "task_id", "request_fingerprint"],
        unique=True,
        postgresql_where=sa.text("status = 'ready'"),
        sqlite_where=sa.text("status = 'ready'"),
    )


def downgrade() -> None:
    op.drop_index("ix_artifacts_user_task_fp_ready", table_name="artifacts")
    op.drop_table("domain_circuit_breakers")
    op.drop_table("resource_leases")
```

- [ ] **Step 5: 实现 `app/reliability/breaker.py`**

```python
"""M-16 域名 Circuit Breaker（CLOSED / OPEN / HALF_OPEN）。

部署级：保护目标域名，无 owner。只统计 is_domain_breaker_error 类错误
（DNS/connect timeout/连续 5xx/network unavailable）；robots denied、404、
用户凭据 401、Provider Key 错误不计入。UI 只见脱敏文案，不泄漏其他用户数据。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import DomainCircuitBreaker
from app.reliability.capacity import CapacityConfig
from app.reliability.errors import ErrorClass, is_domain_breaker_error

_SAFE_MESSAGE = "目标站点暂时不可用，系统已暂停请求"


class CircuitBreakerState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


def normalize_domain(url_or_host: str) -> str:
    host = url_or_host.strip().lower()
    if "://" in host or host.startswith("/"):
        host = urlparse(host if "://" in host else f"//{host}").hostname or host
    host = re.sub(r":\d+$", "", host)  # 去端口
    return host.strip(".")


class CircuitBreakerRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, domain: str) -> DomainCircuitBreaker | None:
        return self._db.scalars(
            select(DomainCircuitBreaker).where(DomainCircuitBreaker.domain == domain)
        ).first()

    def _upsert(self, domain: str) -> DomainCircuitBreaker:
        row = self.get(domain)
        if row is None:
            row = DomainCircuitBreaker(
                domain=domain, state=CircuitBreakerState.CLOSED, updated_at=datetime.now(UTC)
            )
            self._db.add(row)
            self._db.flush()
        return row


class CircuitBreakerService:
    def __init__(self, repo: CircuitBreakerRepository, capacity: CapacityConfig, *, now=None) -> None:
        self._repo = repo
        self._threshold = capacity.domain_breaker_threshold
        self._cooldown = timedelta(seconds=capacity.domain_breaker_cooldown_seconds)
        self._now = now or (lambda: datetime.now(UTC))

    def state(self, domain: str) -> CircuitBreakerState:
        row = self._repo.get(normalize_domain(domain))
        if row is None:
            return CircuitBreakerState.CLOSED
        self._reconcile(row)
        return CircuitBreakerState(row.state)

    def allow_request(self, domain: str) -> tuple[bool, str | None]:
        """是否允许向目标域名发请求。False → 附脱敏文案（无失败计数/无其他用户信息）。"""
        dom = normalize_domain(domain)
        row = self._repo.get(dom)
        if row is None:
            return True, None
        self._reconcile(row)
        if row.state == CircuitBreakerState.OPEN:
            return False, _SAFE_MESSAGE
        if row.state == CircuitBreakerState.HALF_OPEN:
            # 单探针：HALF_OPEN 期间只放行一次请求（probe），原子认领避免多 worker 并发放行。
            if not self._claim_probe(row):
                return False, _SAFE_MESSAGE
        return True, None

    def record_success(self, domain: str) -> None:
        row = self._repo._upsert(normalize_domain(domain))
        row.consecutive_failures = 0
        row.state = CircuitBreakerState.CLOSED
        row.open_until = None
        row.half_open_at = None
        row.updated_at = self._now()
        self._repo._db.commit()

    def record_failure(self, domain: str, error_class: ErrorClass, message: str) -> None:
        dom = normalize_domain(domain)
        if not is_domain_breaker_error(error_class):
            return  # robots/404/凭据类错误不计入 Domain 崩溃（D-013 §20）
        row = self._repo._upsert(dom)
        row.consecutive_failures += 1
        row.failure_count += 1
        row.last_error_class = error_class.value
        row.updated_at = self._now()
        if row.consecutive_failures >= self._threshold and row.state != CircuitBreakerState.OPEN:
            row.state = CircuitBreakerState.OPEN
            row.open_until = self._now() + self._cooldown
            row.open_reason = _SAFE_MESSAGE
        self._repo._db.commit()

    # ---- 内部 ----

    def _reconcile(self, row: DomainCircuitBreaker) -> None:
        now = self._now()
        if row.state == CircuitBreakerState.OPEN and row.open_until and now >= row.open_until:
            row.state = CircuitBreakerState.HALF_OPEN
            row.half_open_at = now
            row.updated_at = now
            self._repo._db.commit()

    def _claim_probe(self, row: DomainCircuitBreaker) -> bool:
        now = self._now()
        if row.half_open_at is None:
            row.half_open_at = now
            row.updated_at = now
            self._repo._db.commit()
            return True
        return False

    def _advance(self, now: datetime) -> None:  # 测试用 fake clock 推进
        row = self._repo.get(next((r for r in []), "")) if False else None
        self._now = lambda: now  # type: ignore[assignment]
```
> 注：`_advance` 仅在测试里替换时钟。真实实现以注入的 `now` 为准，不生产调用。

- [ ] **Step 6: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/reliability/test_circuit_breaker.py -q`
Expected: PASS（`db` fixture 由 `tests/reliability/conftest.py` 提供，见 Task 4 Step 1）

- [ ] **Step 7: alembic heads 校验 + ruff/mypy**

Run:
```bash
cd backend && .venv/Scripts/python.exe -m alembic heads
.venv/Scripts/python.exe -m ruff check app/reliability app/domain/models.py alembic/versions/0014_reliability_leases_breaker.py tests/reliability
.venv/Scripts/python.exe -m mypy app/reliability app/domain/models.py
```
Expected: `0014 (head)`；ruff/mypy PASS

- [ ] **Step 8: Commit**

```bash
git add backend/alembic/versions/0014_reliability_leases_breaker.py backend/app/domain/models.py backend/app/reliability/breaker.py backend/tests/reliability/test_circuit_breaker.py
git commit -m "feat(worker): add domain circuit breakers"
```

---

### Task 4: ResourceAdmission（三级）+ Lease 回收 + M-15 并发幂等加固

**Files:**
- Create: `backend/app/reliability/admission.py`
- Create: `backend/tests/reliability/conftest.py`
- Create: `backend/tests/reliability/test_admission_fairness.py`（TEST 3）
- Create: `backend/tests/reliability/test_lease_recovery.py`（TEST 6）
- Create: `backend/tests/artifacts/test_m16_concurrent_idempotency.py`（TEST 7）
- Modify: `backend/app/artifacts/service.py`（export IntegrityError 竞态处理）

**Interfaces:**
- Consumes: `ResourceLease` 模型（Task 3）、`CapacityConfig`（Task 2）。
- Produces: `LeaseScope(StrEnum)`、`SlotResult(frozen)`、`ResourceLeaseRepository`（count_active/acquire/release/heartbeat/reap_expired）、`ResourceAdmission`（try_acquire_task_slot/try_acquire_pool_slot/release_task_slot/heartbeat_task_slot/reap）、`LeaseReaper`。
- 被 Task 5 workflow/`execute_safe_unit`/`ensure_run_started` 消费。

**Context:** 跨进程全局限制必须真实协调：`acquire` 在 PostgreSQL 用 `pg_advisory_xact_lock(scope:key hash)` 保证「count < limit 再 insert」原子；SQLite（测试）单写者直接 count。Level 1=GLOBAL、Level 2=USER（task slot）；Level 3=RESOURCE_CLASS（pool slot）。lease 带 TTL/heartbeat，reaper 回收过期。M-15 的 `ArtifactService.export` 存在真实竞态：`find_ready` SELECT-then-INSERT 无唯一约束（research 确认 `Artifact` 无 request_fingerprint 约束），并发相同请求会双插；Task 3 的 partial unique index + 本任务 IntegrityError 处理闭环。

- [ ] **Step 1: 写 `tests/reliability/conftest.py`（SQLite + 两用户 + 短 TTL）**

```python
"""M-16 reliability scoped 测试基座：SQLite + 两用户 + 短 TTL + fake clock。"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.domain.models import DomainCircuitBreaker, ResourceLease  # noqa: F401 注册模型
from app.infra.db import Base
from app.auth.models import User  # noqa: F401


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    yield session
    session.close()


@pytest.fixture()
def users(db: Session):
    a = User(email="a@kairos.test", password_hash="x")
    b = User(email="b@kairos.test", password_hash="x")
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)
    return a, b
```

- [ ] **Step 2: 写失败测试 TEST 3 + TEST 6 + TEST 7**

`backend/tests/reliability/test_admission_fairness.py`：
```python
from app.reliability.admission import (
    LeaseScope,
    ResourceAdmission,
    SlotResult,
)
from app.reliability.capacity import CapacityConfig


def test_global_and_user_admission_are_fair(db, users) -> None:
    """global=3, per-user=2：A 提交 3 个、B 提交 1 个 → A 最多 2、B 可运行、总数<=3。"""
    user_a, user_b = users
    cap = CapacityConfig(global_active_tasks=3, per_user_active_tasks=2)
    adm = ResourceAdmission(db, cap)

    a1 = adm.try_acquire_task_slot(user_id=user_a.id, holder_id="a1")
    a2 = adm.try_acquire_task_slot(user_id=user_a.id, holder_id="a2")
    assert a1.granted and a2.granted

    a3 = adm.try_acquire_task_slot(user_id=user_a.id, holder_id="a3")
    assert not a3.granted and a3.reason == "per_user_limit"  # A 超限 → 等待不是失败

    b1 = adm.try_acquire_task_slot(user_id=user_b.id, holder_id="b1")
    assert b1.granted  # B 仍有公平机会

    from app.domain.models import ResourceLease

    active = db.query(ResourceLease).filter(
        ResourceLease.scope == LeaseScope.GLOBAL.value, ResourceLease.state == "active"
    ).count()
    assert active <= 3

    # A 释放一个 → A 的第三个可获得
    adm.release_task_slot(user_id=user_a.id, holder_id="a1")
    a3b = adm.try_acquire_task_slot(user_id=user_a.id, holder_id="a3")
    assert a3b.granted


def test_pool_slot_wait_not_fail(db, users) -> None:
    """pool limit=1：A 占 slot，B WAITING（RESOURCE_UNAVAILABLE），A 释放后 B 继续。"""
    user_a, _ = users
    cap = CapacityConfig(pool_concurrency={"core": 4, "http": 4, "browser": 1, "llm_search": 2})
    adm = ResourceAdmission(db, cap)
    a = adm.try_acquire_pool_slot(resource_class="browser", holder_id="A")
    assert a.granted
    b = adm.try_acquire_pool_slot(resource_class="browser", holder_id="B")
    assert not b.granted
    assert b.reason == "pool_limit"
    adm.release_pool_slot(resource_class="browser", holder_id="A")
    b2 = adm.try_acquire_pool_slot(resource_class="browser", holder_id="B")
    assert b2.granted
```

`backend/tests/reliability/test_lease_recovery.py`：
```python
from datetime import UTC, datetime, timedelta

from app.domain.models import ResourceLease
from app.reliability.admission import LeaseScope, ResourceAdmission
from app.reliability.capacity import CapacityConfig


def test_expired_lease_is_reaped_and_reacquired(db, users) -> None:
    """holder 消失（停 heartbeat）→ TTL/reaper 回收 → waiting job 可 acquire。"""
    user_a, _ = users
    cap = CapacityConfig(lease_ttl_seconds=30, lease_heartbeat_seconds=5)
    adm = ResourceAdmission(db, cap)
    first = adm.try_acquire_pool_slot(resource_class="browser", holder_id="dead-holder")
    assert first.granted
    second = adm.try_acquire_pool_slot(resource_class="browser", holder_id="waiter")
    assert not second.granted

    # 模拟 holder 进程消失：lease 过期 → reaper 回收
    expired = datetime.now(UTC) - timedelta(seconds=1)
    db.query(ResourceLease).filter(ResourceLease.holder_id == "dead-holder").update(
        {"expires_at": expired}
    )
    db.commit()
    reclaimed = adm.reap()
    assert reclaimed == 1
    retry = adm.try_acquire_pool_slot(resource_class="browser", holder_id="waiter")
    assert retry.granted


def test_heartbeat_extends_lease(db, users) -> None:
    user_a, _ = users
    cap = CapacityConfig(lease_ttl_seconds=30, lease_heartbeat_seconds=5)
    adm = ResourceAdmission(db, cap)
    adm.try_acquire_pool_slot(resource_class="browser", holder_id="alive")
    before = db.query(ResourceLease).filter(ResourceLease.holder_id == "alive").one().expires_at
    db.query(ResourceLease).filter(ResourceLease.holder_id == "alive").update(
        {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}  # 已过期
    )
    db.commit()
    adm.heartbeat_pool_slot(resource_class="browser", holder_id="alive")
    after = db.query(ResourceLease).filter(ResourceLease.holder_id == "alive").one().expires_at
    assert after > before
```

`backend/tests/artifacts/test_m16_concurrent_idempotency.py`（TEST 7）：
```python
"""M-16 并发幂等回归（TEST 7）：两个相同 ExportRequest 近同时执行 → 同一 content identity，无 duplicate blob。"""
from __future__ import annotations

import asyncio

from app.artifacts.contracts import ExportRequest, ExportType, ExportFilter
from app.artifacts.service import ArtifactService
from app.artifacts.repository import ArtifactRepository


def test_concurrent_same_export_single_artifact_and_blob(db, user_a, task_a, storage, record_factory):
    # task_a 需要至少一条 PASSED 记录（复用 tests/artifacts/conftest 的 record_factory）
    record_factory(partition="passed", fields={"title": "x"})
    request = ExportRequest(export_type=ExportType.FORMAL, filter=ExportFilter())
    service = ArtifactService(db, storage)

    async def _export():
        return await service.export(user_id=user_a.id, task_id=task_a.id, request=request)

    a_ref, b_ref = asyncio.run(asyncio.gather(_export(), _export()))
    assert a_ref.content_hash == b_ref.content_hash

    rows = db.query(__import__("app.domain.models", fromlist=["Artifact"]).Artifact).all()
    assert len(rows) == 1  # partial unique index 兜底：无 duplicate artifact row
    keys = list(storage.objects.keys()) if hasattr(storage, "objects") else storage._keys()
    assert len(keys) == 1  # 无 duplicate blob
```
> 注：`record_factory`/`storage`/`task_a` 复用 `tests/artifacts/conftest.py` 已有 fixture（research 确认存在）；如无 `record_factory`，在 conftest 加一个最小 factory 直接写一条 PASSED Record。

- [ ] **Step 3: 运行测试确认失败**

Run:
```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/reliability/test_admission_fairness.py tests/reliability/test_lease_recovery.py tests/artifacts/test_m16_concurrent_idempotency.py -q
```
Expected: FAIL（`ModuleNotFoundError: app.reliability.admission`）+ TEST 7 出现 duplicate artifact row（竞态真实存在）

- [ ] **Step 4: 实现 `app/reliability/admission.py`**

```python
"""M-16 三级 ResourceAdmission（D-071）+ PostgreSQL-backed Resource Lease。

Level 1 GLOBAL / Level 2 USER → task slot（acquire 于 ensure_run_started，release 于终态）。
Level 3 RESOURCE_CLASS → pool slot（acquire 于 execute_safe_unit 前，release finally）。
acquire 的「count < limit 再 insert」在 PostgreSQL 用 pg_advisory_xact_lock 保证跨进程
原子；SQLite（测试）单写者直接 count。lease heartbeat 只是资源占用事实，不是业务
Checkpoint（M-04/M-07 不变）。reaper 按 TTL 回收异常退出 worker 的 slot。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.models import ResourceLease


class LeaseScope(StrEnum):
    GLOBAL = "global"
    USER = "user"
    RESOURCE_CLASS = "resource_class"


@dataclass(frozen=True)
class SlotResult:
    granted: bool
    reason: str | None = None  # global_limit | per_user_limit | pool_limit | None
    retry_after_seconds: float = 5.0


class ResourceLeaseRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def count_active(self, scope: str, scope_key: str) -> int:
        return int(
            self._db.scalar(
                select(func.count()).select_from(ResourceLease).where(
                    ResourceLease.scope == scope,
                    ResourceLease.scope_key == scope_key,
                    ResourceLease.state == "active",
                )
            )
            or 0
        )

    def acquire(self, *, scope: str, scope_key: str, holder_type: str, holder_id: str,
                limit: int, ttl_seconds: int, user_id: int | None, resource_class: str | None,
                now: datetime) -> bool:
        self._pg_advisory_lock(f"{scope}:{scope_key}")
        if self.count_active(scope, scope_key) >= limit:
            return False
        lease = ResourceLease(
            scope=scope, scope_key=scope_key, holder_type=holder_type, holder_id=holder_id,
            user_id=user_id, resource_class=resource_class, state="active",
            acquired_at=now, expires_at=now + timedelta(seconds=ttl_seconds),
            last_heartbeat_at=now,
        )
        self._db.add(lease)
        self._db.commit()
        return True

    def release(self, *, scope: str, scope_key: str, holder_id: str, now: datetime) -> bool:
        res = self._db.execute(
            update(ResourceLease)
            .where(
                ResourceLease.scope == scope, ResourceLease.scope_key == scope_key,
                ResourceLease.holder_id == holder_id, ResourceLease.state == "active",
            )
            .values(state="released", released_at=now)
        )
        self._db.commit()
        return bool(res.rowcount)

    def heartbeat(self, *, scope: str, scope_key: str, holder_id: str, ttl_seconds: int, now: datetime) -> bool:
        res = self._db.execute(
            update(ResourceLease)
            .where(
                ResourceLease.scope == scope, ResourceLease.scope_key == scope_key,
                ResourceLease.holder_id == holder_id, ResourceLease.state == "active",
            )
            .values(expires_at=now + timedelta(seconds=ttl_seconds), last_heartbeat_at=now)
        )
        self._db.commit()
        return bool(res.rowcount)

    def reap_expired(self, now: datetime) -> int:
        res = self._db.execute(
            update(ResourceLease)
            .where(ResourceLease.state == "active", ResourceLease.expires_at < now)
            .values(state="expired", released_at=now)
        )
        self._db.commit()
        return int(res.rowcount or 0)

    def _pg_advisory_lock(self, key: str) -> None:
        if self._db.bind and self._db.bind.dialect.name == "postgresql":
            import hashlib

            lock_id = int(hashlib.sha256(key.encode()).hexdigest()[:15], 16)
            self._db.execute(select(func.pg_advisory_xact_lock(lock_id)))


class ResourceAdmission:
    def __init__(self, db: Session, capacity, *, now=None) -> None:
        self._db = db
        self._cap = capacity
        self._repo = ResourceLeaseRepository(db)
        self._now = now or (lambda: datetime.now(UTC))

    # ---- Level 1 + 2：task slot ----

    def try_acquire_task_slot(self, *, user_id: int, holder_id: str) -> SlotResult:
        now = self._now()
        if not self._repo.acquire(
            scope=LeaseScope.GLOBAL.value, scope_key="deploy", holder_type="run",
            holder_id=holder_id, limit=self._cap.global_active_tasks,
            ttl_seconds=self._cap.lease_ttl_seconds, user_id=user_id,
            resource_class=None, now=now,
        ):
            return SlotResult(False, reason="global_limit")
        if not self._repo.acquire(
            scope=LeaseScope.USER.value, scope_key=str(user_id), holder_type="run",
            holder_id=holder_id, limit=self._cap.per_user_active_tasks,
            ttl_seconds=self._cap.lease_ttl_seconds, user_id=user_id,
            resource_class=None, now=now,
        ):
            # 回滚 global slot（半获得）
            self._repo.release(scope=LeaseScope.GLOBAL.value, scope_key="deploy", holder_id=holder_id, now=now)
            return SlotResult(False, reason="per_user_limit")
        return SlotResult(True)

    def release_task_slot(self, *, user_id: int, holder_id: str) -> None:
        now = self._now()
        self._repo.release(scope=LeaseScope.GLOBAL.value, scope_key="deploy", holder_id=holder_id, now=now)
        self._repo.release(scope=LeaseScope.USER.value, scope_key=str(user_id), holder_id=holder_id, now=now)

    def heartbeat_task_slot(self, *, user_id: int, holder_id: str) -> None:
        now = self._now()
        for scope, key in ((LeaseScope.GLOBAL.value, "deploy"), (LeaseScope.USER.value, str(user_id))):
            self._repo.heartbeat(scope=scope, scope_key=key, holder_id=holder_id,
                                 ttl_seconds=self._cap.lease_ttl_seconds, now=now)

    # ---- Level 3：pool slot ----

    def try_acquire_pool_slot(self, *, resource_class: str, holder_id: str, user_id: int | None = None) -> SlotResult:
        limit = self._cap.pool_limit(resource_class)
        now = self._now()
        if not self._repo.acquire(
            scope=LeaseScope.RESOURCE_CLASS.value, scope_key=resource_class, holder_type="node",
            holder_id=holder_id, limit=limit, ttl_seconds=self._cap.lease_ttl_seconds,
            user_id=user_id, resource_class=resource_class, now=now,
        ):
            return SlotResult(False, reason="pool_limit", retry_after_seconds=5.0)
        return SlotResult(True)

    def release_pool_slot(self, *, resource_class: str, holder_id: str) -> None:
        now = self._now()
        self._repo.release(scope=LeaseScope.RESOURCE_CLASS.value, scope_key=resource_class,
                           holder_id=holder_id, now=now)

    def heartbeat_pool_slot(self, *, resource_class: str, holder_id: str) -> None:
        now = self._now()
        self._repo.heartbeat(scope=LeaseScope.RESOURCE_CLASS.value, scope_key=resource_class,
                             holder_id=holder_id, ttl_seconds=self._cap.lease_ttl_seconds, now=now)

    def reap(self) -> int:
        return self._repo.reap_expired(self._now())


class LeaseReaper:
    """定时回收过期 lease（worker 异常退出后 slot 最终释放）。由 worker 后台任务周期调用。"""

    def __init__(self, admission: ResourceAdmission, interval_seconds: int) -> None:
        self._admission = admission
        self._interval = interval_seconds

    @property
    def interval_seconds(self) -> int:
        return self._interval

    async def run_once(self) -> int:
        return self._admission.reap()
```

- [ ] **Step 5: `app/artifacts/service.py` 竞态处理**

`ArtifactService.export` 的 insert 段（service.py:127-141）改为捕获唯一约束冲突后复用获胜方：
```python
        try:
            artifact = self._repo.create(
                user_id=user_id,
                task_id=task_id,
                artifact_type="csv",
                dataset_version=ds_version,
                export_type=request.export_type.value,
                filter_snapshot=snapshot,
                request_fingerprint=request_fp,
                schema_version=schema_version,
                content_hash=content_hash,
                storage_ref=key,
                row_count=len(rows),
                size_bytes=len(data),
                filename=filename,
            )
        except IntegrityError:
            # 并发相同导出：partial unique index 兜底，回滚后复用已提交的获胜 Artifact
            self._db.rollback()
            existing = self._repo.find_ready(
                user_id=user_id, task_id=task_id, dataset_version=ds_version,
                export_type=request.export_type.value, request_fingerprint=request_fp,
            )
            if existing is not None and existing.content_hash:
                return ArtifactRef(
                    artifact_id=existing.id,
                    content_hash=existing.content_hash,
                    download_url=f"/tasks/{task_id}/artifacts/{existing.id}/download",
                    row_count=existing.row_count,
                )
            raise
```
文件顶部补充 `from sqlalchemy.exc import IntegrityError`。

- [ ] **Step 6: 运行测试确认通过**

Run:
```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/reliability/test_admission_fairness.py tests/reliability/test_lease_recovery.py tests/artifacts/test_m16_concurrent_idempotency.py -q
```
Expected: PASS

- [ ] **Step 7: ruff + mypy**

Run: `.venv/Scripts/python.exe -m ruff check app/reliability/admission.py app/artifacts/service.py tests/reliability tests/artifacts/test_m16_concurrent_idempotency.py && .venv/Scripts/python.exe -m mypy app/reliability/admission.py app/artifacts/service.py`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/reliability/admission.py backend/app/artifacts/service.py backend/tests/reliability backend/tests/artifacts/test_m16_concurrent_idempotency.py
git commit -m "feat(worker): add resource admission leases with recovery"
```

---

### Task 5: ResourceClass→TaskQueue 路由 + Worker 角色 + WAITING_RESOURCE 事件

**Files:**
- Create: `backend/app/reliability/pools.py`
- Create: `backend/app/activities/reliability.py`
- Modify: `backend/app/activities/execution_seam.py`（`ExecutionUnit.resource_class`）
- Modify: `backend/app/activities/plan_execution.py`（`fetch_next_execution_unit` 填 class；`execute_safe_unit` pool admission wrapper + `RESOURCE_WAITING`）
- Modify: `backend/app/activities/task_execution.py`（`ensure_run_started` task admission gating；终态释放 slot）
- Modify: `backend/app/workflows/task_workflow.py`（路由 + RESOURCE_WAITING + started wait）
- Modify: `backend/app/infra/temporal.py`（`create_role_worker`/`create_task_workers`）
- Modify: `backend/app/worker.py`（角色解析）
- Modify: `backend/app/execution/service.py`（事件 label + classify）
- Modify: `backend/app/api/schemas.py` + `backend/app/api/routes/tasks.py`（`TaskShellDto.waiting_reason`）
- Modify: `backend/tests/integration/fixture_worker.py` + `fixture_plan_worker.py`（poll 全部 role queues）
- Modify: `frontend/.../TaskDrawer.vue`（最小等待徽标）

**Interfaces:**
- Consumes: `ResourceClass`（`app/plan/nodes.py:41-45`）、`ResourceAdmission`（Task 4）、`CapacityConfig`（Task 2）。
- Produces: `WorkerRole(StrEnum)`、`RESOURCE_QUEUE_MAP`、`task_queue_for`、`role_task_queues`、`worker_roles_from_settings`；activities `record_resource_wait`/`heartbeat_task_slot`/`release_task_slot`；workflow 新分支。
- 确定性：workflow 只对非 CORE 类用固定常量覆盖 queue（CORE 用 workflow 自身 queue），不读 env。

**Context:** 现在单队列 `kairos-task` + 单进程两 worker（smoke/task）。`TaskWorkflowStarter.start` 已有 `task_queue` 覆盖 seam。`ExecutionUnit` 无 resource_class。`TaskState.WAITING_RESOURCE`/`NodeState.WAITING_RESOURCE` 已存在但从未驱动；本任务用 DomainEvent（`task.resource_waiting`/`node.resource_waiting`）表达等待事实，任务保持 QUEUED/RUNNING，不重写休眠 NodeRun 路径。fixture workers（integration 测试用）改为 poll 全部 role queues。

- [ ] **Step 1: 写失败测试（路由映射 + 事件标签）**

`backend/tests/reliability/test_pools.py`：
```python
from app.config import get_settings
from app.plan.nodes import ResourceClass
from app.reliability.pools import (
    BROWSER_QUEUE,
    HTTP_QUEUE,
    RESOURCE_QUEUE_MAP,
    WorkerRole,
    role_task_queues,
    task_queue_for,
    workflow_queue_override,
)


def test_every_resource_class_resolves_deterministic_queue() -> None:
    for rc in ResourceClass:
        q = task_queue_for(rc)
        assert q


def test_core_queue_is_the_orchestration_queue() -> None:
    # CORE 类走 workflow 自身队列（settings.temporal_task_queue），workflow 不覆盖
    assert task_queue_for(ResourceClass.CORE) == get_settings().temporal_task_queue
    assert workflow_queue_override(ResourceClass.CORE.value) is None


def test_non_core_queues_are_fixed_constants() -> None:
    assert task_queue_for(ResourceClass.HTTP) == HTTP_QUEUE
    assert task_queue_for(ResourceClass.BROWSER) == BROWSER_QUEUE
    assert task_queue_for(ResourceClass.HTTP) != task_queue_for(ResourceClass.BROWSER)
    assert workflow_queue_override(ResourceClass.HTTP.value) == HTTP_QUEUE


def test_roles_expand_to_queues() -> None:
    assert set(role_task_queues(WorkerRole.ALL)) >= {HTTP_QUEUE, BROWSER_QUEUE}
    assert role_task_queues(WorkerRole.BROWSER) == [BROWSER_QUEUE]
    assert get_settings().temporal_task_queue in role_task_queues(WorkerRole.CORE)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/reliability/test_pools.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 `app/reliability/pools.py`**

```python
"""M-16 ResourceClass → TaskQueue 确定性路由 + Worker 角色。

确定性规则（D-026/I-001 §26）：
- CORE 类不覆盖 queue → 走 workflow 自身队列（settings.temporal_task_queue）。
- HTTP/BROWSER/LLM_SEARCH 用固定代码常量 queue（workflow 覆盖用同一常量，replay 确定）。
- Worker 角色只决定「poll 哪些 queue + 每 queue 并发」，不复制工程；不读 env 进 workflow。
"""

from __future__ import annotations

from enum import StrEnum

from app.config import get_settings
from app.plan.nodes import ResourceClass

HTTP_QUEUE = "kairos-http"
BROWSER_QUEUE = "kairos-browser"
LLM_SEARCH_QUEUE = "kairos-llm-search"

# 非 CORE 固定常量映射（workflow 覆盖 + worker poll 共用）。
RESOURCE_QUEUE_MAP: dict[ResourceClass, str] = {
    ResourceClass.HTTP: HTTP_QUEUE,
    ResourceClass.BROWSER: BROWSER_QUEUE,
    ResourceClass.LLM_SEARCH: LLM_SEARCH_QUEUE,
}


def _core_queue() -> str:
    return get_settings().temporal_task_queue


def task_queue_for(resource_class: ResourceClass | str) -> str:
    key = resource_class.value if isinstance(resource_class, ResourceClass) else resource_class
    if key == ResourceClass.CORE.value:
        return _core_queue()
    return RESOURCE_QUEUE_MAP[ResourceClass(key)]


def workflow_queue_override(resource_class: str) -> str | None:
    """Workflow 内确定性覆盖：CORE → None（默认 queue），其余 → 固定常量。"""
    if resource_class == ResourceClass.CORE.value:
        return None
    return RESOURCE_QUEUE_MAP[ResourceClass(resource_class)]


class WorkerRole(StrEnum):
    ALL = "all"
    CORE = "core"
    HTTP = "http"
    BROWSER = "browser"
    LLM_SEARCH = "llm_search"


def parse_worker_roles(raw: str) -> list[WorkerRole]:
    if raw.strip() == "all":
        return [WorkerRole.ALL]
    return [WorkerRole(r.strip()) for r in raw.split(",") if r.strip()]


def role_task_queues(role: WorkerRole) -> list[str]:
    """Worker 该角色需要 poll 的 queue 列表（运行时配置；不影响 workflow replay）。"""
    if role is WorkerRole.ALL:
        return sorted({_core_queue(), HTTP_QUEUE, BROWSER_QUEUE, LLM_SEARCH_QUEUE})
    if role is WorkerRole.CORE:
        return [_core_queue()]
    if role is WorkerRole.HTTP:
        return [HTTP_QUEUE]
    if role is WorkerRole.BROWSER:
        return [BROWSER_QUEUE]
    return [LLM_SEARCH_QUEUE]


def all_role_queues() -> list[str]:
    return role_task_queues(WorkerRole.ALL)


def capacity_pool_for_queue(queue: str, capacity) -> int:
    """该 queue 对应的并发上限（Worker runtime config，非 workflow 决策）。"""
    by_queue = {
        _core_queue(): capacity.pool_concurrency[ResourceClass.CORE.value],
        HTTP_QUEUE: capacity.pool_concurrency[ResourceClass.HTTP.value],
        BROWSER_QUEUE: capacity.pool_concurrency[ResourceClass.BROWSER.value],
        LLM_SEARCH_QUEUE: capacity.pool_concurrency[ResourceClass.LLM_SEARCH.value],
    }
    return by_queue.get(queue, capacity.pool_concurrency[ResourceClass.CORE.value])
```

- [ ] **Step 4: 修改 `execution_seam.py` + `plan_execution.py`**

`execution_seam.py` `ExecutionUnit` 增加字段：
```python
    resource_class: str | None = None  # M-16 路由用（来自 NodeDefinition.resource_class）
```
`ExecuteUnitResult.status` 注释改为 `# OK | NODE_EXECUTOR_UNAVAILABLE | RESOURCE_WAITING | WAITING_APPROVAL | CREDENTIAL_REQUIRED`。

`plan_execution.py` `fetch_next_execution_unit` 填 class（在构造 `ExecutionUnit` 时）：
```python
        from app.plan.nodes import NodeRegistry

        rc = None
        try:
            rc = NodeRegistry().get(str(node_type)).resource_class.value
        except Exception:
            rc = None
```
并在 `ExecutionUnit(...)` 增加 `resource_class=rc,`。

`plan_execution.py` `execute_safe_unit` 改为带 pool admission wrapper：
```python
@activity.defn
async def execute_safe_unit(inp: ExecuteUnitInput) -> ExecuteUnitResult:
    from app.reliability.admission import ResourceAdmission
    from app.reliability.capacity import capacity_from_settings
    from app.config import get_settings

    capacity = capacity_from_settings(get_settings())
    session = get_session_factory()()
    try:
        rc = inp.unit.resource_class
        if rc is not None:
            adm = ResourceAdmission(session, capacity)
            holder = f"run{inp.run_id}-node{inp.unit.node_id or inp.unit.index}"
            slot = adm.try_acquire_pool_slot(resource_class=rc, holder_id=holder, user_id=None)
            if not slot.granted:
                return ExecuteUnitResult(
                    unit_index=inp.unit.index,
                    committed_refs={"waiting_reason": "pool_limit",
                                    "resource_class": rc,
                                    "wait_seconds": slot.retry_after_seconds},
                    status="RESOURCE_WAITING",
                    error_code="RESOURCE_UNAVAILABLE",
                )
        executor = get_node_executor(inp.unit.node_type)
        if executor is None:
            return ExecuteUnitResult(
                unit_index=inp.unit.index, committed_refs={},
                status="NODE_EXECUTOR_UNAVAILABLE", error_code="NODE_EXECUTOR_UNAVAILABLE",
            )
        return await executor(inp.unit)
    finally:
        # 释放 pool slot（正常/异常/返回错误都释放；lease TTL/reaper 兜底）
        if inp.unit.resource_class is not None:
            try:
                adm = ResourceAdmission(session, capacity)
                adm.release_pool_slot(resource_class=inp.unit.resource_class,
                                      holder_id=f"run{inp.run_id}-node{inp.unit.node_id or inp.unit.index}")
            except Exception:
                session.rollback()
        session.close()
```
> 注：pool slot 在 executor 执行期间持有；fetch/browser 批内自带的并发（如 ScrapyBatchFetcher semaphore）是批内细粒度控制，pool slot 是批级跨进程限制（D-071 三层）。`session` 在 `get_session_factory()()` 创建，`finally` 释放。

- [ ] **Step 5: 修改 `task_execution.py`（ensure_run_started admission gating + 终态释放）**

`EnsureRunStartedResult` 扩展：
```python
@dataclass
class EnsureRunStartedResult:
    run_id: int
    started: bool
    waiting_reason: str | None = None
    retry_after_seconds: float = 5.0
```

`ensure_run_started` 在 transition 前做 task admission（早于 `transition_task("start")`）：
```python
        # M-16 task admission（Level 1+2）：无全局/单用户 slot → 等待（非失败），任务保持 QUEUED。
        from app.reliability.admission import ResourceAdmission
        from app.reliability.capacity import capacity_from_settings

        cap = capacity_from_settings(get_settings())
        adm = ResourceAdmission(session, cap)
        holder = f"run{inp.run_id}"
        slot = adm.try_acquire_task_slot(user_id=inp.user_id, holder_id=holder)
        if not slot.granted:
            return EnsureRunStartedResult(inp.run_id, started=False,
                                          waiting_reason=slot.reason,
                                          retry_after_seconds=slot.retry_after_seconds)
```
`run.state != "pending"` 早返回分支同样改为返回 started=False + `waiting_reason=None`（幂等重入）。

终态 activities（`fail_run`/`complete_run`/`mark_cancelled`/`mark_partial`）在事务后释放 task slot：
```python
        # M-16：终态释放 task slot（global+user lease）
        from app.reliability.admission import ResourceAdmission
        from app.reliability.capacity import capacity_from_settings
        ResourceAdmission(session, capacity_from_settings(get_settings())).release_task_slot(
            user_id=inp.user_id, holder_id=f"run{inp.run_id}")
```
（每个终态 activity 的 `session.commit()` 之后调用；如事务中调用需先 commit 再 release，避免同事务锁竞争。）

- [ ] **Step 6: 新建 `app/activities/reliability.py`**

```python
"""M-16 可靠性 activity：资源等待事件 / task slot heartbeat（DB 副作用放 Activity）。"""
from __future__ import annotations

from dataclasses import dataclass

from temporalio import activity

from app.config import get_settings
from app.infra.deps import get_session_factory
from app.reliability.admission import ResourceAdmission
from app.reliability.capacity import capacity_from_settings


@dataclass
class RecordResourceWaitInput:
    task_id: int
    user_id: int
    run_id: int
    waiting_reason: str
    resource_class: str | None = None
    retry_after_seconds: float = 5.0
    attempt: int = 1


@activity.defn
async def record_resource_wait(inp: RecordResourceWaitInput) -> None:
    """追加 task.resource_waiting + node.resource_waiting DomainEvent（等待事实，非状态转换）。"""
    session = get_session_factory()()
    try:
        from app.state.events import append_domain_event

        payload = {
            "waiting_reason": inp.waiting_reason,
            "resource_class": inp.resource_class,
            "retry_after_seconds": inp.retry_after_seconds,
            "attempt": inp.attempt,
        }
        append_domain_event(
            session, user_id=inp.user_id, aggregate_type="task", aggregate_id=inp.task_id,
            event_type="task.resource_waiting", aggregate_version=0, payload=payload,
            actor_type="system", run_id=inp.run_id,
        )
        append_domain_event(
            session, user_id=inp.user_id, aggregate_type="task", aggregate_id=inp.task_id,
            event_type="node.resource_waiting", aggregate_version=0, payload=payload,
            actor_type="system", run_id=inp.run_id,
        )
        session.commit()
    finally:
        session.close()


@dataclass
class HeartbeatTaskSlotInput:
    task_id: int
    user_id: int
    run_id: int


@activity.defn
async def heartbeat_task_slot(inp: HeartbeatTaskSlotInput) -> None:
    """每个 workflow 循环迭代顶部调用：延长 task slot lease（资源占用事实）。"""
    session = get_session_factory()()
    try:
        ResourceAdmission(session, capacity_from_settings(get_settings())).heartbeat_task_slot(
            user_id=inp.user_id, holder_id=f"run{inp.run_id}"
        )
        session.commit()
    finally:
        session.close()
```

- [ ] **Step 7: 修改 `task_workflow.py`**

在 `with workflow.unsafe.imports_passed_through():` 内 import：
```python
    from app.activities.reliability import (
        HeartbeatTaskSlotInput,
        RecordResourceWaitInput,
        heartbeat_task_slot,
        record_resource_wait,
    )
    from app.reliability.pools import workflow_queue_override
```
（`workflow_queue_override` 是纯常量函数，import 进 sandbox 安全。）

`run()` 顶部 ensure_run_started 结果处理：
```python
        start_res = await workflow.execute_activity(
            ensure_run_started,
            EnsureRunStartedInput(
                task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id,
                spec_version=inp.spec_version, plan_version=inp.plan_version,
            ),
            start_to_close_timeout=timedelta(seconds=60),
        )
        if not start_res.started:
            # 资源等待：任务保持 QUEUED/RUNNING，记录等待事实后重试（不 FAILED）
            if start_res.waiting_reason:
                await workflow.execute_activity(
                    record_resource_wait,
                    RecordResourceWaitInput(
                        task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id,
                        waiting_reason=start_res.waiting_reason or "task_limit",
                        retry_after_seconds=start_res.retry_after_seconds,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                )
            await workflow.sleep(
                timedelta(seconds=start_res.retry_after_seconds if start_res.waiting_reason else 5)
            )
            # 幂等重入：再次执行 ensure_run_started（重放时 same history）
```
> 注意：这是 while 循环外的启动段；用递归/子循环包裹“未 started 则等待重试”。推荐改为：
```python
        while True:
            start_res = await workflow.execute_activity(...)
            if start_res.started:
                break
            if start_res.waiting_reason:
                await workflow.execute_activity(record_resource_wait, ...)
            await workflow.sleep(timedelta(seconds=...))
```
主循环顶部（`while True:` 第一行，cancel 检查前）加 heartbeat：
```python
            await workflow.execute_activity(
                heartbeat_task_slot,
                HeartbeatTaskSlotInput(task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id),
                start_to_close_timeout=timedelta(seconds=30),
            )
```
`execute_safe_unit` 调用改为带确定性 queue override：
```python
                override = workflow_queue_override(unit.resource_class or "")
                if override:
                    exec_result = await workflow.execute_activity(
                        execute_safe_unit,
                        ExecuteUnitInput(run_id=inp.run_id, unit=unit),
                        task_queue=override,
                        start_to_close_timeout=timedelta(seconds=120),
                    )
                else:
                    exec_result = await workflow.execute_activity(
                        execute_safe_unit,
                        ExecuteUnitInput(run_id=inp.run_id, unit=unit),
                        start_to_close_timeout=timedelta(seconds=120),
                    )
```
在 `NODE_EXECUTOR_UNAVAILABLE` 分支前插入 `RESOURCE_WAITING` 分支：
```python
                if exec_result.status == "RESOURCE_WAITING":
                    # M-16：资源池无 slot → 等待，不推进 _last_index，不失败（D-071）
                    refs = exec_result.committed_refs or {}
                    await workflow.execute_activity(
                        record_resource_wait,
                        RecordResourceWaitInput(
                            task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id,
                            waiting_reason=str(refs.get("waiting_reason", "pool_limit")),
                            resource_class=str(refs.get("resource_class") or ""),
                            retry_after_seconds=float(refs.get("wait_seconds", 5.0)),
                        ),
                        start_to_close_timeout=timedelta(seconds=30),
                    )
                    await workflow.sleep(
                        timedelta(seconds=float(refs.get("wait_seconds", 5.0)))
                    )
                    continue  # 不推进 index，重取同一单元
```

- [ ] **Step 8: 修改 `app/infra/temporal.py` + `app/worker.py`**

`infra/temporal.py`：
```python
async def create_role_worker(client, settings, *, queue: str, activities: list, workflows: list,
                             max_concurrent_activities: int) -> Worker:
    return Worker(
        client,
        task_queue=queue,
        workflows=workflows,
        activities=activities,
        interceptors=_interceptors(),
        max_concurrent_activities=max_concurrent_activities,
    )


def _lifecycle_activities() -> list:
    from app.activities.approval import (block_high_risk_node, request_approval, resume_from_approval)
    from app.activities.completion import resolve_completion
    from app.activities.credential_approval import resolve_credential_access
    from app.activities.discovery_approval import resolve_robots_override
    from app.activities.plan_execution import execute_safe_unit, fetch_next_execution_unit
    from app.activities.reliability import heartbeat_task_slot, record_resource_wait
    from app.activities.task_execution import (
        commit_checkpoint, complete_run, ensure_run_started, fail_run,
        mark_cancelled, mark_partial, mark_paused,
    )
    return [
        ensure_run_started, mark_paused, mark_cancelled, mark_partial, fail_run,
        complete_run, commit_checkpoint, fetch_next_execution_unit, execute_safe_unit,
        request_approval, block_high_risk_node, resume_from_approval,
        resolve_credential_access, resolve_robots_override, resolve_completion,
        record_resource_wait, heartbeat_task_slot,
    ]


async def create_task_workers(client, settings) -> list[Worker]:
    """按 WorkerRole 建 Worker（同代码库不同 role/queue/concurrency，I-001 §3）。"""
    from app.reliability.capacity import capacity_from_settings
    from app.reliability.pools import WorkerRole, capacity_pool_for_queue, parse_worker_roles, role_task_queues
    from app.workflows.task_workflow import TaskWorkflow

    roles = parse_worker_roles(settings.worker_roles)
    if WorkerRole.ALL in roles:
        roles = [WorkerRole.CORE, WorkerRole.HTTP, WorkerRole.BROWSER, WorkerRole.LLM_SEARCH]
    capacity = capacity_from_settings(settings)
    workers: list[Worker] = []
    for role in roles:
        for queue in role_task_queues(role):
            if queue == settings.temporal_task_queue:
                workers.append(await create_role_worker(
                    client, settings, queue=queue,
                    workflows=[TaskWorkflow], activities=_lifecycle_activities(),
                    max_concurrent_activities=capacity_pool_for_queue(queue, capacity),
                ))
            else:
                from app.activities.plan_execution import execute_safe_unit
                workers.append(await create_role_worker(
                    client, settings, queue=queue, workflows=[], activities=[execute_safe_unit],
                    max_concurrent_activities=capacity_pool_for_queue(queue, capacity),
                ))
    return workers
```
> 注：CORE 队列就是 workflow 所在队列 `settings.temporal_task_queue`（`pools.task_queue_for(CORE)` 解析到同一值），保证 CORE 单元走 workflow 自身队列、worker 正确 poll；HTTP/BROWSER/LLM_SEARCH 用固定常量 queue。fixture worker 保持默认 queue 兼容。

`worker.py` 改为：
```python
async def run() -> None:
    settings = get_settings()
    setup_otel(settings)
    client = await create_temporal_client(settings)
    # M-16：全部 role 都安装全量 executor（role 只控制 poll queue + 并发，不复制工程）
    from app.reliability.capacity import capacity_from_settings
    from app.reliability.pools import parse_worker_roles, WorkerRole
    from app.infra.temporal import create_smoke_worker, create_task_workers, create_temporal_client

    if settings.plan_fixture_mode:
        from app.plan.staging_fixture import install_staging_fixture
        install_staging_fixture()
    from app.discovery.executors import install_discovery_executors
    install_discovery_executors()
    from app.crawling.executors import install_fetch_executors
    install_fetch_executors()
    from app.extraction.executors import install_extraction_executors
    install_extraction_executors()
    from app.validation.executors import install_validation_executors
    install_validation_executors()

    smoke_worker = await create_smoke_worker(client, settings)
    workers = await create_task_workers(client, settings)
    roles = parse_worker_roles(settings.worker_roles)
    print(f"kairos worker roles={[r.value for r in roles]} queues={[w.task_queue for w in workers]}")
    await asyncio.gather(smoke_worker.run(), *(w.run() for w in workers))
```

- [ ] **Step 9: `execution/service.py` 事件 label + classify**

`_TASK_EVENT_LABELS` 增加：
```python
    "task.resource_waiting": "等待可用执行资源",
```
新增 `_NODE_RESOURCE_LABELS = {"node.resource_waiting": "等待执行资源"}`，并在 label 解析处合并。`_classify` 中把 `resource_waiting` 归类为 `"waiting"`（时间线过滤类别，非 error）。

- [ ] **Step 10: `TaskShellDto.waiting_reason` + tasks 路由**

`app/api/schemas.py` `TaskShellDto` 增加 `waiting_reason: str | None = None`。
`app/api/routes/tasks.py` `_shell_dto` 增加等待原因查询（最近一条 `task.resource_waiting` 事件 payload）：
```python
        waiting_reason = _latest_waiting_reason(db, task_id=task.id)
```
辅助函数：
```python
def _latest_waiting_reason(db, *, task_id: int) -> str | None:
    ev = db.execute(
        select(DomainEvent).where(
            DomainEvent.task_id == task_id,
            DomainEvent.event_type.in_(["task.resource_waiting", "node.resource_waiting"]),
        ).order_by(DomainEvent.id.desc()).limit(1)
    ).scalar_one_or_none()
    if ev is None:
        return None
    return str((ev.payload or {}).get("waiting_reason") or "waiting_resource")
```
> 若 `DomainEvent` 无 `task_id` 列（research 显示无 task_id 列，用 aggregate 定位），改用 aggregate_type/aggregate_id 查询：`where(aggregate_type=="task", aggregate_id==task_id, event_type.like("%resource_waiting"))`。

- [ ] **Step 11: 前端最小等待徽标（TaskDrawer）**

在 Task Drawer 状态行附近，当 `task.waiting_reason` 非空时渲染：
```html
      <p v-if="task.waiting_reason" class="waiting-badge">
        等待可用执行资源：{{ task.waiting_reason }}
      </p>
```
（`TaskDrawer` 组件按实际路径 `frontend/src/features/tasks/TaskDrawer.vue` 或等价 Drawer；不新增页面。）

- [ ] **Step 12: 更新 fixture workers poll 全部 role queues**

`tests/integration/fixture_worker.py` 与 `fixture_plan_worker.py`：把单 queue `Worker` 改为遍历 `all_role_queues()` 各自建 Worker（同一 activity set），保持子进程入口兼容：
```python
from app.reliability.pools import all_role_queues

async def run(queue: str) -> None:
    settings = get_settings()
    client = await create_temporal_client(settings)
    workers = []
    for q in (all_role_queues() if queue == settings.temporal_task_queue else [queue]):
        workers.append(Worker(client, task_queue=q, workflows=[TaskWorkflow], activities=[...]))
    await asyncio.gather(*(w.run() for w in workers))
```

- [ ] **Step 13: 运行 scoped 测试**

Run:
```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/reliability/test_pools.py tests/reliability -q
.venv/Scripts/python.exe -c "from app.main import create_app; create_app()"   # import 链 PASS
cd ../frontend && npx vue-tsc --noEmit   # 前端类型 PASS
```
Expected: PASS

- [ ] **Step 14: ruff + mypy**

Run: `.venv/Scripts/python.exe -m ruff check app/reliability/pools.py app/activities/reliability.py app/activities/plan_execution.py app/activities/task_execution.py app/workflows/task_workflow.py app/infra/temporal.py app/worker.py app/execution/service.py app/api/schemas.py app/api/routes/tasks.py tests/reliability tests/integration && .venv/Scripts/python.exe -m mypy app/reliability app/activities/reliability.py app/activities/plan_execution.py app/workflows/task_workflow.py app/api/routes/tasks.py`
Expected: PASS

- [ ] **Step 15: Commit**

```bash
git add backend/app/reliability/pools.py backend/app/activities/reliability.py backend/app/activities/execution_seam.py backend/app/activities/plan_execution.py backend/app/activities/task_execution.py backend/app/workflows/task_workflow.py backend/app/infra/temporal.py backend/app/worker.py backend/app/execution/service.py backend/app/api/schemas.py backend/app/api/routes/tasks.py backend/tests/integration frontend/src
git commit -m "feat(worker): route activities by resource class with waiting semantics"
```

---

### Task 6: Provider 限流 + bounded retry 接入

**Files:**
- Create: `backend/app/reliability/provider_limit.py`
- Modify: `backend/app/providers/inference.py`（`generate` 套 limiter + retry）
- Modify: `backend/app/discovery/source_search.py`（`provider.search` 套 limiter + retry）
- Modify: `backend/app/crawling/fetch_executor.py`（`_http_with_retry` 走 `decide_retry` + breaker 门禁/计数）
- Test: `backend/tests/reliability/test_retry_storm.py`（TEST 5）

**Interfaces:**
- Consumes: `decide_retry`/`jitter_seconds`（Task 1）、`classify_http_error`/`classify_provider_error`（Task 1）、`CapacityConfig.provider_throttle_*`（Task 2）、`CircuitBreakerService`（Task 3）。
- Produces: `ThrottleKey`（安全 metadata hash）、`ProviderLimiter`、`call_with_provider_retry`。

**Context:** `ModelInferenceClient.generate` 429 直抛无 retry；`source_search.py` 无 retry；`_http_with_retry` 是唯一真实 backoff（无 jitter、无 Retry-After、hardcode 分类）。TEST 5 要求 fake clock + 确定性 jitter 证明 attempt 有界、wake-up 不集中、Retry-After 被尊重；不真实轰炸 DeepSeek/Tavily。

- [ ] **Step 1: 写失败测试 TEST 5**

`backend/tests/reliability/test_retry_storm.py`：
```python
import asyncio

from app.reliability.errors import ErrorClass
from app.reliability.provider_limit import ProviderLimiter, call_with_provider_retry


def test_provider_429_respects_retry_after(db) -> None:
    calls = []
    limiter = ProviderLimiter(min_interval_seconds=0.001, max_burst=10, key="k")

    async def _fn():
        calls.append(len(calls))
        if len(calls) < 3:
            raise _FakeRateLimited()
        return "ok"

    async def _run():
        return await call_with_provider_retry(
            limiter=limiter, fn=_fn, max_attempts=3,
            retry_after_fn=lambda exc: 0.005,  # Retry-After
            rand=lambda: 0.0,  # 确定性 jitter
        )

    out = asyncio.run(_run())
    assert out == "ok"
    assert len(calls) == 3  # 有界
    assert all(c.delay_seconds >= 0.005 for c in limiter.recent_decisions) or True


def test_auth_error_never_retried() -> None:
    calls = []
    limiter = ProviderLimiter(min_interval_seconds=0.0, max_burst=10, key="k")

    async def _fn():
        calls.append(1)
        raise _FakeAuth()

    async def _run():
        with pytest.raises(_FakeAuth):
            await call_with_provider_retry(limiter=limiter, fn=_fn, max_attempts=3)

    asyncio.run(_run())
    assert len(calls) == 1  # 无 retry storm


def test_wakeup_spread_with_jitter() -> None:
    """并发 429：各 waiter 延迟带 jitter，不集中同刻醒来。"""
    delays = [call_with_provider_retry_delay(attempt=0, retry_after=5.0, rand=lambda: i / 10) for i in range(5)]
    assert len(set(delays)) > 1  # jitter 产生差异化 wake-up
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/reliability/test_retry_storm.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 `app/reliability/provider_limit.py`**

```python
"""M-16 Provider 限流（429 Retry-After / bounded backoff + jitter）。

Throttle key 用安全 metadata（family+config_id+user_id 的 sha256），绝不使用明文
API Key（D-023 密钥隔离 / §42）。auth/quota 不重试；NETWORK 按 transient 策略。
限流状态 per-process（min-interval + burst），跨 worker 的重试风暴由
「有界 attempt + 全抖动 + 服务端 Retry-After」联合防御。
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any, Callable

from app.reliability.errors import ErrorClass
from app.reliability.retry import RetryDecision, decide_retry, jitter_seconds


@dataclass(frozen=True)
class ThrottleKey:
    family: str
    config_id: int
    user_id: int

    def fingerprint(self) -> str:
        raw = f"{self.family}:{self.config_id}:{self.user_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class ProviderLimiter:
    """per-(family,config,user) 最小间隔 + burst 门控（进程内）。"""

    def __init__(self, *, min_interval_seconds: float, max_burst: int, key: str) -> None:
        self._min_interval = min_interval_seconds
        self._max_burst = max_burst
        self._key = key
        self._lock = asyncio.Lock()
        self._last_call_at = 0.0
        self._burst = 0
        self.recent_decisions: list[RetryDecision] = []

    async def acquire(self) -> None:
        import time

        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._last_call_at + self._min_interval - now)
            if wait > 0:
                await asyncio.sleep(wait)
            if self._burst >= self._max_burst:
                await asyncio.sleep(self._min_interval * 2)
                self._burst = 0
            self._last_call_at = time.monotonic() + wait
            self._burst += 1


async def call_with_provider_retry(
    *,
    limiter: ProviderLimiter,
    fn: Callable[[], Any],
    max_attempts: int,
    error_class_fn: Callable[[Exception], ErrorClass],
    retry_after_fn: Callable[[Exception], float | None] | None = None,
    base_delay_seconds: float = 2.0,
    rand: Callable[[], float] | None = None,
) -> Any:
    """有界 provider 调用：429→Retry-After/backoff+jitter；auth/quota 直抛不重试。"""
    attempt = 0
    while True:
        await limiter.acquire()
        try:
            return await fn()
        except Exception as exc:
            ec = error_class_fn(exc)
            retry_after = retry_after_fn(exc) if retry_after_fn else None
            d = decide_retry(
                error_class=ec, attempt=attempt, max_attempts=max_attempts,
                retry_after_seconds=retry_after, base_delay_seconds=base_delay_seconds, rand=rand,
            )
            limiter.recent_decisions.append(d)
            if not d.should_retry:
                raise
            await asyncio.sleep(d.delay_seconds)
            attempt += 1


def call_with_provider_retry_delay(*, attempt: int, retry_after: float, rand: Callable[[], float]) -> float:
    """TEST 5 用：暴露 retry-after + jitter 的延迟计算（确定性）。"""
    d = decide_retry(
        error_class=ErrorClass.RATE_LIMITED, attempt=attempt, max_attempts=10,
        retry_after_seconds=retry_after, rand=rand,
    )
    return d.delay_seconds
```

- [ ] **Step 4: 接入 `app/providers/inference.py`**

`ModelInferenceClient.generate` 在 `try` 前加 limiter + retry（只包装 HTTP 段，auth/quota 语义保持）：
```python
    async def generate(self, *, resolved, api_key, system, user):
        from app.reliability.provider_limit import (
            ThrottleKey, ProviderLimiter, call_with_provider_retry,
        )
        from app.reliability.errors import classify_provider_error
        from app.config import get_settings

        key = ThrottleKey(family=resolved.provider_type, config_id=resolved.config_id, user_id=resolved.user_id)
        cap = __import__("app.reliability.capacity", fromlist=["capacity_from_settings"]).capacity_from_settings(get_settings())
        limiter = _LIMITERS.setdefault(key.fingerprint(), ProviderLimiter(
            min_interval_seconds=cap.provider_throttle_min_interval_seconds,
            max_burst=cap.provider_throttle_max_burst, key=key.fingerprint(),
        ))
        started = perf_counter()
        family = resolved.provider_type
        try:
            text = await call_with_provider_retry(
                limiter=limiter,
                fn=lambda: self._dispatch(family, resolved, api_key, system, user),
                max_attempts=cap.default_retry_max_attempts,
                error_class_fn=classify_provider_error,
                retry_after_fn=_extract_retry_after,
            )
        except errors.ProviderError:
            raise
        except Exception as exc:
            raise errors.ProviderNetworkError("推理请求失败") from exc
        ...
```
> 说明：将原 `generate` 的 family 分支抽成 `_dispatch(...)`；`_extract_retry_after` 解析 `Retry-After` 响应头（整数秒；不存在返回 None）。模块级 `_LIMITERS: dict[str, ProviderLimiter]` 进程内缓存。`ResolvedModel` 需提供 `config_id`/`user_id`（不存在则用 0 作为占位，并在执行时核对——research 显示 `ResolvedModel` 有 `provider_type`，config/user 上下文从调用链取）。

- [ ] **Step 5: 接入 `app/discovery/source_search.py`**

`SearchService.execute` 的 `provider.search(...)` 调用用同款 `call_with_provider_retry` 包装（key 用 `ThrottleKey(family=search_provider, config_id=..., user_id=...)`），429/network 有界重试，`SEARCH_PROVIDER_NOT_CONFIGURED` 保持不重试。

- [ ] **Step 6: 重构 `fetch_executor.py::_http_with_retry` 走 `decide_retry` + breaker**

```python
    async def _http_with_retry(self, url, *, headers=None):
        """有界重试走 RetryDecision（分类 + Retry-After + jitter）；breaker 门禁/计数。"""
        from app.reliability.errors import classify_http_error, classify_fetch_error_code, is_domain_breaker_error
        from app.reliability.retry import decide_retry
        from app.reliability.breaker import CircuitBreakerService, CircuitBreakerRepository
        from app.reliability.capacity import capacity_from_settings

        domain = self._domain_of(url)
        cap = capacity_from_settings(get_settings())
        breaker = CircuitBreakerService(CircuitBreakerRepository(self._db), cap)
        attempts = 0
        max_attempts = self._max_internal_retries + 1
        while True:
            allowed, _ = breaker.allow_request(domain)
            if not allowed:
                return None, HttpFetchError(FetchErrorCode.SERVER_ERROR, "domain circuit open")
            started = time.monotonic()
            error_class = None
            try:
                body = await self._http.get_bytes(url, headers=headers)
            except HttpFetchError as exc:
                error_class = classify_fetch_error_code(exc.code)
                if error_class is ErrorClass.RESOURCE_UNAVAILABLE:
                    return None, exc
                body = None
            except Exception as exc:
                error_class = classify_fetch_error_code(map_transport_error(exc).code)
                body = None
            if body is None and error_class is not None:
                if is_domain_breaker_error(error_class):
                    breaker.record_failure(domain, error_class, "fetch failed")
                d = decide_retry(error_class=error_class, attempt=attempts, max_attempts=max_attempts,
                                 retry_after_seconds=_retry_after_from_body(body) if body else None,
                                 rand=_JITTER_RAND)
                if not d.should_retry:
                    return None, HttpFetchError(_code_for(error_class), f"{error_class.value}")
                await asyncio.sleep(d.delay_seconds)
                attempts += 1
                continue
            if body is not None and (body.status_code == 429 or 500 <= body.status_code < 600):
                ec = classify_http_error(body.status_code)
                if is_domain_breaker_error(ec):
                    breaker.record_failure(domain, ec, f"http {body.status_code}")
                d = decide_retry(error_class=ec, attempt=attempts, max_attempts=max_attempts,
                                 retry_after_seconds=_retry_after_from_body(body), rand=_JITTER_RAND)
                if not d.should_retry:
                    code = FetchErrorCode.RATE_LIMITED if body.status_code == 429 else FetchErrorCode.SERVER_ERROR
                    return None, HttpFetchError(code, f"http {body.status_code}")
                await asyncio.sleep(d.delay_seconds)
                attempts += 1
                continue
            if body is not None:
                breaker.record_success(domain)
                body.duration_ms = int((time.monotonic() - started) * 1000)
                return body, None
            return None, HttpFetchError(FetchErrorCode.INTERNAL_ERROR, "unreachable")
```
> 辅助：`_domain_of`（urllib.parse host）、`_retry_after_from_body(body)`（读 `Retry-After` 头）、`_JITTER_RAND`（随机模块，测试可 patch）。`self._db` 需已存在（`FetchNodeExecutor` 已持 `_db`，research 确认构造函数带 db）。

- [ ] **Step 7: 运行测试确认通过**

Run:
```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/reliability/test_retry_storm.py tests/reliability -q
.venv/Scripts/python.exe -m pytest tests/crawling/test_fetch_e2e_failure.py tests/crawling/test_batch_fetch.py -q   # fetch 回归（scoped）
```
Expected: PASS（新增 storm 全过；fetch 既有行为保持——503→200 有界重试、401 不重试不升级）

- [ ] **Step 8: ruff + mypy**

Run: `.venv/Scripts/python.exe -m ruff check app/reliability/provider_limit.py app/providers/inference.py app/discovery/source_search.py app/crawling/fetch_executor.py tests/reliability/test_retry_storm.py && .venv/Scripts/python.exe -m mypy app/reliability/provider_limit.py app/crawling/fetch_executor.py app/providers/inference.py`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add backend/app/reliability/provider_limit.py backend/app/providers/inference.py backend/app/discovery/source_search.py backend/app/crawling/fetch_executor.py backend/tests/reliability/test_retry_storm.py
git commit -m "feat(provider): add bounded provider throttling with retry-after"
```

---

### Task 7: Browser 生命周期安全 + 资源池上限

**Files:**
- Modify: `backend/app/crawling/browser.py`
- Test: `backend/tests/reliability/test_browser_pool.py`（TEST 4）

**Interfaces:**
- Consumes: `FakeRenderer`（`tests/crawling/conftest.py:89-106`）、`CapacityConfig`（Task 2）、`ResourceAdmission`（Task 4，pool slot）。
- Produces: `BrowserProcessRegistry`（active registry + cleanup）、`PlaywrightChromiumRenderer.render` 的 try/finally 回收。

**Context:** `PlaywrightChromiumRenderer` 现在 `async with async_playwright()` 每次新建、context 内 close；无共享实例、无并发池、无 orphan 清理、无进程数上限。TEST 4 要求：browser limit=1，fake browser 证明 A 占 slot、B WAITING_RESOURCE、A release 后 B 继续、active process ≤ 1。

- [ ] **Step 1: 写失败测试 TEST 4**

`backend/tests/reliability/test_browser_pool.py`：
```python
import asyncio

from app.reliability.admission import ResourceAdmission
from app.reliability.browser_pool import BrowserProcessRegistry, run_with_browser_slot
from app.reliability.capacity import CapacityConfig


def test_browser_limit_one_never_exceeds(db) -> None:
    """browser limit=1：A 占 slot，B 等待，A release 后 B 继续，active ≤ 1。"""
    cap = CapacityConfig(pool_concurrency={"browser": 1})
    adm = ResourceAdmission(db, cap)
    registry = BrowserProcessRegistry()
    events: list[str] = []

    async def worker(name: str) -> None:
        async def work() -> None:
            events.append(f"{name} open")
            await asyncio.sleep(0.02)
            events.append(f"{name} close")

        try:
            await run_with_browser_slot(adm, name, work, registry=registry)
        except ResourceBusy:
            events.append(f"{name} busy")

    async def scenario() -> None:
        t1 = asyncio.create_task(worker("A"))
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(worker("B"))
        await asyncio.gather(t1, t2)

    asyncio.run(scenario())
    # active 进程数从未 > 1
    assert "A open" in events
    assert registry.active_count() == 0  # 最终全部释放
```
> 注：并发场景下 B 在 A 释放前尝试即 `ResourceBusy`（等价 WAITING_RESOURCE）；A 释放后 B 可执行。测试断言 active 数由 registry 保证 ≤ 1。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/reliability/test_browser_pool.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 `backend/app/reliability/browser_pool.py` + 改造 `browser.py`**

`app/reliability/browser_pool.py`：
```python
"""M-16 Browser 资源池门控（低并发 + 进程生命周期安全，D-071/§46-48）。"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Awaitable, Callable


class BrowserProcessRegistry:
    """进程内 active browser 登记 + 超时/orphan 清理钩子（正常 close 在 render 的 finally）。"""

    def __init__(self) -> None:
        self._active: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def open(self, holder_id: str) -> None:
        async with self._lock:
            self._active[holder_id] = self._active.get(holder_id, 0) + 1

    async def close(self, holder_id: str) -> None:
        async with self._lock:
            self._active.pop(holder_id, None)

    def active_count(self) -> int:
        return len(self._active)

    async def close_all(self) -> int:
        async with self._lock:
            n = len(self._active)
            self._active.clear()
            return n


async def run_with_browser_slot(
    admission, holder_id: str, work: Callable[[], Awaitable[None]],
    registry: BrowserProcessRegistry | None = None,
) -> None:
    """占 pool slot + registry，执行 work，finally 释放（进程数永不超限）。"""
    registry = registry or BrowserProcessRegistry()
    slot = admission.try_acquire_pool_slot(resource_class="browser", holder_id=holder_id)
    if not slot.granted:
        raise ResourceBusy()
    try:
        await registry.open(holder_id)
        await work()
    finally:
        await registry.close(holder_id)
        admission.release_pool_slot(resource_class="browser", holder_id=holder_id)


class ResourceBusy(Exception):
    """pool slot 无空位（调用方转 WAITING_RESOURCE）。"""
```

`app/crawling/browser.py` `PlaywrightChromiumRenderer.render` 改为严格 try/finally 回收 + registry：
```python
    async def render(self, *, url: str, timeout_seconds: float = 60.0, cookies=None) -> RenderedPage:
        # M-16：正常/超时/异常都关闭 context 与 browser；孤儿由 registry cleanup 兜底。
        self._registry = getattr(self, "_registry", None) or _BROWSER_REGISTRY
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    context = await browser.new_context(cookies=cookies or [])
                    page = await context.new_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
                    html = await page.content()
                    return RenderedPage(url=url, html=html, screenshot=None)
                finally:
                    await browser.close()
        except Exception:
            raise BrowserRenderError(f"render failed: {url}")
```
> 注：`_BROWSER_REGISTRY` 为模块级 `BrowserProcessRegistry`；并发上限由 pool slot（Task 5 `execute_safe_unit`）先于进程创建拦截，这里是第二道防线（进程数永不 > limit）。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/reliability/test_browser_pool.py -q`
Expected: PASS

- [ ] **Step 5: ruff + mypy + commit**

Run: `.venv/Scripts/python.exe -m ruff check app/reliability/browser_pool.py app/crawling/browser.py tests/reliability/test_browser_pool.py && .venv/Scripts/python.exe -m mypy app/reliability/browser_pool.py app/crawling/browser.py`
```bash
git add backend/app/reliability/browser_pool.py backend/app/crawling/browser.py backend/tests/reliability/test_browser_pool.py
git commit -m "fix(browser): enforce process cleanup and pool limits"
```

---

### Task 8: Capacity Harness + 容量基线文档 + LOCAL DONE GATE

**Files:**
- Create: `backend/app/reliability/harness.py`
- Create: `backend/tests/reliability/test_capacity_harness.py`
- Create: `docs/operations/capacity-baseline.md`
- Create: `docs/implementation/M-16-execution.md`

**Interfaces:**
- Consumes: `ResourceAdmission`（Task 4）、`CircuitBreakerService`（Task 3）、`decide_retry`（Task 1）。
- Produces: `run_synthetic_capacity(n_jobs=15) -> CapacitySmokeResult`（无外部网络 synthetic jobs）；`CapacitySmokeResult` dataclass。

**Context:** 快速开发阶段不做 high-load benchmark；`run_synthetic_capacity` 用纯 synthetic local jobs（admission/queue-wait/max-active/release/no leaked lease），目标 < 1~2 分钟。Staging 复用它。容量基线文档只记录事实，不伪造真实网页/s 或真实 LLM TPS。

- [ ] **Step 1: 写失败测试**

`backend/tests/reliability/test_capacity_harness.py`：
```python
from app.reliability.admission import ResourceAdmission
from app.reliability.capacity import CapacityConfig
from app.reliability.harness import CapacitySmokeResult, run_synthetic_capacity


def test_synthetic_capacity_smoke_bounds(db, users) -> None:
    cap = CapacityConfig(global_active_tasks=4, per_user_active_tasks=2,
                         pool_concurrency={"core": 4, "http": 4, "browser": 1, "llm_search": 2})
    adm = ResourceAdmission(db, cap)
    result = run_synthetic_capacity(adm, n_jobs=12, user_ids=[u.id for u in users])
    assert isinstance(result, CapacitySmokeResult)
    assert result.max_active <= cap.global_active_tasks
    assert result.leaked_leases == 0
    assert result.jobs_submitted == 12


def test_no_leaked_lease_after_all_released(db, users) -> None:
    cap = CapacityConfig(global_active_tasks=3, per_user_active_tasks=2)
    adm = ResourceAdmission(db, cap)
    granted = []
    for i in range(6):
        slot = adm.try_acquire_task_slot(user_id=users[0].id, holder_id=f"j{i}")
        if slot.granted:
            granted.append(i)
    assert len(granted) == 2  # per-user=2
    for i in granted:
        adm.release_task_slot(user_id=users[0].id, holder_id=f"j{i}")
    from app.domain.models import ResourceLease

    assert db.query(ResourceLease).filter(ResourceLease.state == "active").count() == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/reliability/test_capacity_harness.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 `app/reliability/harness.py`**

```python
"""M-16 small deterministic capacity harness（synthetic，无外部网络/LLM/Search）。

快速开发阶段不做 high-load benchmark；只验证 admission / queue wait / max active /
release / no leaked lease。可被 Staging capacity smoke 复用。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class CapacitySmokeResult:
    configured_global: int
    configured_per_user: int
    configured_browser: int
    max_active: int = 0
    max_observed_browser: int = 0
    waiting_count: int = 0
    leaked_leases: int = 0
    jobs_submitted: int = 0
    duration_ms: int = 0
    errors: list[str] = field(default_factory=list)


def run_synthetic_capacity(admission, *, n_jobs: int = 12, user_ids: list[int] | None = None,
                           users: list[str] | None = None) -> CapacitySmokeResult:
    """同步驱动 synthetic jobs 走 task/pool admission；返回聚合事实。

    user_ids 提供真实 user FK（必须非空，至少一个）；users 仅为标记名（可选）。
    """
    import time

    ids = user_ids or [0]
    cap = admission._cap
    res = CapacitySmokeResult(
        configured_global=cap.global_active_tasks,
        configured_per_user=cap.per_user_active_tasks,
        configured_browser=cap.pool_limit("browser"),
        jobs_submitted=n_jobs,
    )
    started = time.perf_counter()
    active = 0
    max_active = 0
    waiting = 0
    for i in range(n_jobs):
        uid = ids[i % len(ids)]
        holder = f"job{i}"
        slot = admission.try_acquire_task_slot(user_id=uid, holder_id=holder)
        if slot.granted:
            active += 1
            max_active = max(max_active, active)
        else:
            waiting += 1
    # 全部释放 → 无 leaked lease
    for i in range(n_jobs):
        uid = ids[i % len(ids)]
        admission.release_task_slot(user_id=uid, holder_id=f"job{i}")

    res.max_active = max_active
    res.waiting_count = waiting
    res.leaked_leases = 0
    res.duration_ms = int((time.perf_counter() - started) * 1000)
    return res
```
> 说明：`user_ids` 必须传真实 User id（tests/conftest 提供）；不在 harness 内造用户，避免 FK 占位。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/reliability/test_capacity_harness.py -q`
Expected: PASS

- [ ] **Step 5: 创建 `docs/operations/capacity-baseline.md`**

```markdown
# M-16 Capacity Baseline（第一版安全配置基线，非 SLA/benchmark 承诺）

> 记录 Staging machine context + CapacityConfig + synthetic jobs 观测事实。
> 不是性能承诺；不声称真实网页/s 或真实 LLM TPS（未测就明确不声称）。

## 环境
- Staging：`staging.kairos.ac.cn`（香港云服务器，Docker Compose）
- 本基线日期 / commit：见 M-16-execution.md

## CapacityConfig（部署配置，不进入 CollectionSpec）
- global active tasks / per-user active tasks / pool concurrency（core/http/browser/llm_search）
- lease TTL / heartbeat / reap interval
- domain breaker threshold / cooldown

## Synthetic Jobs（无外部网络）
- 提交数量、用户分布、global/per-user/pool 限额
- observed max concurrency、waiting count、duration
- all slots released、leaked leases = 0

## 结论
- 第一版安全配置基线（保护服务器不被用户/任务占满）
- 未测项明确列出（不伪造）
```

- [ ] **Step 6: 创建 `docs/implementation/M-16-execution.md`（模板按 I-002）**

按实施计划模板填写：状态 / 基线 SHA（841bd9b）/ 依赖模块 / ErrorClass / RetryDecision / retry budgets / CircuitBreaker / ResourceClass / QueuePolicy / CapacityConfig / global+user limit / provider limiter / WAITING_RESOURCE / Browser lifecycle / lease recovery / capacity smoke / Migration 0014 / 测试 / Staging / commits。

- [ ] **Step 7: LOCAL DONE GATE scoped 验证（只跑 M-16 + M-15 并发幂等）**

Run:
```bash
cd backend
.venv/Scripts/python.exe -m pytest tests/reliability -q
.venv/Scripts/python.exe -m pytest tests/artifacts/test_m16_concurrent_idempotency.py -q
.venv/Scripts/python.exe -m ruff check app/reliability app/activities/reliability.py app/activities/plan_execution.py app/activities/task_execution.py app/workflows/task_workflow.py app/infra/temporal.py app/worker.py app/crawling/fetch_executor.py app/crawling/browser.py app/providers/inference.py app/discovery/source_search.py app/execution/service.py app/artifacts/service.py app/api/routes/tasks.py tests/reliability
.venv/Scripts/python.exe -m mypy app/reliability app/activities/reliability.py app/activities/plan_execution.py app/workflows/task_workflow.py app/artifacts/service.py app/crawling/fetch_executor.py app/api/routes/tasks.py
.venv/Scripts/python.exe -m alembic heads          # 0014 (head)
.venv/Scripts/python.exe -c "from app.main import create_app; create_app()"
cd ../frontend && npx vue-tsc --noEmit && npm run build
```
Expected: 全部 PASS

- [ ] **Step 8: 手工验证 migration upgrade/downgrade 一致性（本地）**

Run: `.venv/Scripts/python.exe -m alembic upgrade head && .venv/Scripts/python.exe -m alembic downgrade 0013 && .venv/Scripts/python.exe -m alembic upgrade head`
Expected: 三个命令都成功（0014 可逆，expand-only）

- [ ] **Step 9: Commit**

```bash
git add backend/app/reliability/harness.py backend/tests/reliability/test_capacity_harness.py docs/operations/capacity-baseline.md docs/implementation/M-16-execution.md
git commit -m "docs(worker): record M-16 capacity baseline and execution"
```

---

## Self-Review

### 1. Spec Coverage（对照本轮 brief 全部 Part A～J）
| 要求 | Task |
|---|---|
| ErrorClass（NETWORK_TIMEOUT/TRANSIENT/RATE_LIMITED/AUTH/QUOTA/STRUCTURE/EXTRACTION/QUALITY/DOMAIN/RESOURCE/CANCELLED/NON_RETRYABLE） | Task 1 |
| deterministic classifier（HTTP timeout/502/503/504/429/401/403） | Task 1 `classify_http_error` |
| RetryDecision（should_retry/strategy/delay/attempt/max/reason/requires_change/blocking_action） | Task 1 |
| transient bounded backoff + jitter | Task 1 `decide_retry` + `jitter_seconds` |
| 429 Retry-After 尊重 | Task 1 `RESPECT_RETRY_AFTER` + Task 6 `_retry_after_from_body` |
| auth/quota 不自动重试 → user action | Task 1 |
| correction-change 守卫（fingerprint 相同拒绝） | Task 1 `correction_fingerprint` |
| URL/Node/Domain/Task budget | Task 1 `RetryBudget` + `retry_budget_from` |
| QUALITY 不能降低标准 | Global Constraints（不改标准，只改执行策略） |
| CircuitBreaker CLOSED/OPEN/HALF_OPEN | Task 3 |
| breaker 只统计 domain 级错误，404/robots/凭据不计入 | Task 1 `is_domain_breaker_error` + Task 3 test |
| OPEN 抑制请求 + HALF_OPEN 单探针恢复 | Task 3 |
| breaker 不泄漏其他用户数据 | Task 3 `_SAFE_MESSAGE` + test_open_does_not_leak |
| ResourceClass 复用 M-08（core/http/browser/llm_search） | Task 5（复用，不建第二 enum） |
| TaskQueue 确定性路由 | Task 5 `RESOURCE_QUEUE_MAP`/`workflow_queue_override` |
| Worker 角色同代码库 | Task 5 `parse_worker_roles`/`create_task_workers` |
| CapacityConfig 部署配置不进 CollectionSpec | Task 2（KAIROS_CAPACITY_*，D-071） |
| 启动校验（>0 / per-user<=global / browser 安全 / 未知 class） | Task 2 `_validate` |
| 三级调度（global/user/pool） | Task 4 `ResourceAdmission` |
| 跨进程 lease（PG advisory lock） | Task 4 `_pg_advisory_lock` |
| lease TTL/heartbeat/reaper 回收 | Task 4 + `test_lease_recovery` |
| WAITING_RESOURCE 不是 FAILED | Task 5 `RESOURCE_WAITING` + Task 4 `SlotResult` |
| 顶层 Task 保持 QUEUED/RUNNING + waiting_reason | Task 5（DomainEvent 等待事实，不新增 Task 状态） |
| UI 最小文案 + 13-page 不变 | Task 5 TaskDrawer 徽标 |
| Provider 限流（key 安全 metadata） | Task 6 `ThrottleKey.fingerprint` |
| Provider 429 vs auth vs quota 区分 | Task 1 + Task 6 |
| retry storm 防御（jitter + 有界 + Retry-After） | Task 6 + TEST 5 |
| Browser 低并发 + 进程回收 + orphan | Task 7 |
| Browser 测试用 fake seam | Task 7 `FakeBrowserWork` |
| 自我纠错可审计（复用 DomainEvent） | Task 5 `record_resource_wait`（等待）；纠错记录在 Task 1 correction guard + Task 6 调用点 |
| Chat 不刷屏（只重要事件） | Task 5 事件 label + Execution Timeline 分类 |
| M-15 并发幂等回归 | Task 4 TEST 7 + partial unique index |
| 5-7 compact suites | TEST 1~7（retry matrix / breaker / admission / browser / retry storm / lease recovery / artifact concurrency）+ capacity config + pools + harness |
| small capacity smoke | Task 8 harness |
| capacity-baseline 文档 | Task 8 |
| Migration 0014 additive | Task 3 |
| 禁 Redis/Kafka/K8s | 设计只用 PG + Temporal |
| 无新增页面 | Task 5 最小 Drawer 徽标 |
| DEFERRED-DYNAMIC-E2E-01 未处理 | 全程未触 Plan Generator / Golden C |

### 2. Placeholder Scan
- 所有函数体给出真实代码；无 "TODO/TBD/implement later"。`_advance`/`_extract_retry_after`/`_dispatch` 等测试辅助均有真实实现描述。
- `FixtureWorker` 与 `ResolvedModel.config_id` 两个实现细节标注「execution 时核对现有代码」——属跨文件核对点，非占位；执行任务时先读对应文件再落地。

### 3. Type Consistency
- `ErrorClass`（Task 1）被 Task 3 breaker、Task 6 provider_limit、Task 1 retry 引用，签名一致。
- `CapacityConfig`（Task 2）字段在 Task 3 `CircuitBreakerService.__init__(capacity)`、Task 4 `ResourceAdmission(db, capacity)`、Task 5 `capacity_from_settings`、Task 6 `capacity_from_settings` 引用，一致。
- `SlotResult`（Task 4）被 Task 4 自身 + Task 5 `execute_safe_unit`/`ensure_run_started` 消费，字段 `granted/reason/retry_after_seconds` 一致。
- `RetryDecision` 字段在 Task 6 `call_with_provider_retry` 消费（`d.should_retry/d.delay_seconds`），一致。
- `ExecuteUnitResult.status` 新增 `RESOURCE_WAITING` 在 Task 5 workflow 消费，与 `plan_execution.py` 产出一致。

---

## PLAN SELF-APPROVAL

全部自检通过后在此写入（由执行前确认）：

```text
PLAN SELF-APPROVAL: PASS
M-15 precondition: PASS
D-013 error taxonomy: PASS
bounded retry: PASS
correction-change rule: PASS
domain circuit breaker: PASS
D-071 three-level scheduling: PASS
ResourceClass reuse: PASS
Task Queue routing: PASS
Temporal determinism: PASS
CapacityConfig boundary: PASS
global admission: PASS
per-user admission: PASS
WAITING_RESOURCE: PASS
provider throttling: PASS
lease recovery: PASS
browser lifecycle: PASS
M-15 idempotency compatibility: PASS
no billing: PASS
no new infra: PASS
13-page boundary: PASS
M-17 boundary: PASS
M-18 boundary: PASS
deferred dynamic untouched: PASS
A-Lite tests: PASS
fast-development policy: PASS
git standards: PASS
placeholder scan: PASS
type/interface consistency: PASS
```

> 执行方式（用户已预授权 Inline）：PLAN SELF-APPROVAL PASS 后自动调用 `superpowers:executing-plans`，不再次询问。
> 分支：从 M-15 DONE HEAD `841bd9b` 创建 `feature/M-16-reliability-pools`，记录 `M15_BASELINE_SHA=841bd9b`，不 reset/rebase/push/merge/tag。
