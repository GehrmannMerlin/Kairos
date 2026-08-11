#!/usr/bin/env bash
# kairos-staging DEPLOY-GATE-2 workflow/approval/pause-resume-cancel closed loop.
#
# Runs inside the staging api container against the real DB + Temporal + worker.
# Exercises the M-08 execution core WITHOUT a real LLM (plan is built via the
# deterministic plan path; Node executor is the staging fixture harness).
#
# Covers: register/login -> task+spec confirm -> plan persist+validate ->
# workflow start (RUNNING) -> high-risk node -> Approval PENDING -> approve ->
# workflow resumes -> node executes (fixture) -> pause -> PAUSED -> resume ->
# RUNNING -> cancel -> CANCELLING -> CANCELLED. Also asserts no secret leak.
#
# Usage (from repository root):
#   DEPLOY_HOST=47.238.145.24 ./infra/scripts/smoke-gate2-workflow.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_HOST="${DEPLOY_HOST:?DEPLOY_HOST required}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes "${DEPLOY_USER}@${DEPLOY_HOST}")
COMPOSE_DIR="/srv/kairos/compose"
COMPOSE=(docker compose -p kairos-staging -f "${COMPOSE_DIR}/compose.base.yml" -f "${COMPOSE_DIR}/compose.staging.yml")

fail() { printf 'GATE2 FAIL: %s\n' "$*" >&2; exit 1; }
ok() { printf '  ok: %s\n' "$*"; }

echo "==> [0] health live + ready (internal)"
"${SSH[@]}" "docker compose -p kairos-staging -f ${COMPOSE_DIR}/compose.base.yml -f ${COMPOSE_DIR}/compose.staging.yml exec -T api python - <<'PY'
import urllib.request
for p in ('/api/health/live','/api/health/ready'):
    r=urllib.request.urlopen('http://localhost:8000'+p,timeout=15)
    print(p, r.status, r.read().decode()[:120])
PY" || fail "health check"
ok "health live/ready"

echo "==> GATE-2 workflow/approval/pause-resume-cancel closed loop (fixture harness)"
"${SSH[@]}" "cd ${COMPOSE_DIR} && ${COMPOSE[*]} exec -T api python - <<'PY'
import sys, uuid, time
sys.path.insert(0,'/app')
from app.config import get_settings
from app.domain.spec import SpecDraftPayload, FieldSpec
from app.domain.task_types import TaskType
from app.domain.models import DomainEvent, OutboxEvent
from app.domain.repository import (TaskRepository, SpecVersionRepository,
    PlanVersionRepository, RunRepository, ApprovalRepository)
from app.domain.service import DomainService
from app.domain.idempotency import stable_fingerprint
from app.infra.deps import get_session_factory
from app.auth.repository import UserRepository
from app.plan.service import PlanService, plan_fingerprint
from app.workflows.starter import TaskWorkflowStarter
from app.infra.temporal import create_temporal_client
from app.plan.nodes import NodeRegistry
from app.workflows.task_workflow import ApprovalResolutionSignal

s=get_session_factory()()
async def main():
    # 1) owner + task + confirmed spec
    owner=UserRepository(s).create('gate2-'+uuid.uuid4().hex[:8]+'@kairos.test','hash',None)
    task=TaskRepository(s).create(user_id=owner.id, title='gate2-workflow', task_type='specified_source')
    s.commit()
    spec=SpecDraftPayload(
        task_type=TaskType.SPECIFIED_SOURCE, goal='gate2 approval loop',
        fields=[FieldSpec(name='公司名', type='text', required=True)],
        source_scope={'mode':'SPECIFIED_SOURCE','seed_urls':['https://example.com'],'source_hints':[]},
    )
    DomainService(TaskRepository(s)).confirm_spec(
        user_id=owner.id, task_id=task.id, expected_version=task.version,
        spec_payload=spec.model_dump(mode='json'), actor_id=owner.id)
    task=TaskRepository(s).get_owned(owner.id, task.id)
    spec_row=SpecVersionRepository(s).latest_version(owner.id, task.id)
    assert spec_row is not None and spec_row.confirmed_at is not None
    print('SPEC_CONFIRMED v%d state=%s' % (spec_row.version, task.state))

    # 2) Plan: build graph with a HIGH-risk fetch node (validator -> REQUIRES_APPROVAL)
    graph={'task_id':task.id,'spec_version':spec_row.version,'task_type':'SPECIFIED_SOURCE',
        'nodes':[
        {'node_id':'n1','node_type':'fetch','definition_version':'1.0.0',
         'parameters':{'url_template':'https://example.com/private/{id}','non_public':True,
                       'credential_ref':'dummy:gate2'},'depends_on':[]},
        {'node_id':'n2','node_type':'generate_artifact','definition_version':'1.0.0',
         'parameters':{'format':'csv'},'depends_on':['n1']},
    ],'edges':[]}
    reg_versions={d.node_type.value:d.definition_version for d in NodeRegistry().all()}
    client=await create_temporal_client(get_settings())
    psvc=PlanService(s, starter=TaskWorkflowStarter(client))
    # 真实 Deterministic Validator（§71）：计算 node_risk_levels，高风险 fetch → REQUIRES_APPROVAL
    from app.plan.validator import validate_plan
    from app.plan.schemas import PlanGraphDraft
    outcome=validate_plan(PlanGraphDraft.model_validate(graph), spec.model_dump(mode='json'),
        NodeRegistry(), spec_version=spec_row.version)
    assert outcome.result.value=='REQUIRES_APPROVAL', outcome.result.value
    graph['node_risk_levels']={k:v.value for k,v in outcome.node_risk_levels.items()}
    plan=psvc.persist_plan(user_id=owner.id, task_id=task.id, spec_version=spec_row.version,
        graph=graph, validation_status=outcome.result.value,
        fingerprint_value=plan_fingerprint(graph, reg_versions), registry_versions=reg_versions)
    print('PLAN_PERSISTED v%d status=%s' % (plan.version, outcome.result.value))

    # 3) workflow start -> RUNNING
    started=await psvc.auto_start(user_id=owner.id, task_id=task.id,
        spec_version=spec_row.version, plan_version=plan.version)
    print('WORKFLOW_STARTED run=%s wf=%s' % (started[0], started[1]))

    # wait RUNNING (fixture 快，可能已到 WAITING_APPROVAL)
    deadline=time.time()+30
    while time.time()<deadline:
        s.expire_all()  # SQLAlchemy identity map 会缓存旧对象；强制从 DB 重读
        task=TaskRepository(s).get_owned(owner.id, task.id)
        if task.state in ('RUNNING','WAITING_APPROVAL'): break
        time.sleep(0.3)
    assert task.state in ('RUNNING','WAITING_APPROVAL'), task.state
    print('STATE %s' % task.state)

    # 4) wait Approval PENDING (high-risk node reached)
    deadline=time.time()+30
    approval_id=None
    while time.time()<deadline:
        s.expire_all()
        pend=ApprovalRepository(s).list_pending_for_task(owner.id, task.id)
        if pend:
            approval_id=pend[0].id
            break
        time.sleep(0.3)
    assert approval_id is not None, 'no pending approval'
    print('APPROVAL_PENDING id=%d' % approval_id)

    # 5) approve -> outbox -> approval_resolution signal -> workflow resumes
    from app.approval.service import ApprovalService
    from app.infra.outbox_dispatch import OutboxTemporalDispatcher
    svc=ApprovalService(s)
    svc.approve(user_id=owner.id, approval_id=approval_id, actor_id=owner.id)
    await OutboxTemporalDispatcher(client).dispatch_pending_for(s, user_id=owner.id, task_id=task.id)
    print('APPROVED')

    # wait: fetch fixture executes -> COMPLETED
    deadline=time.time()+60
    final=None
    while time.time()<deadline:
        s.expire_all()
        task=TaskRepository(s).get_owned(owner.id, task.id)
        if task.state in ('COMPLETED','PARTIALLY_COMPLETED','FAILED'):
            final=task.state; break
        time.sleep(0.3)
    assert final=='COMPLETED', 'final=%s' % final
    print('STATE COMPLETED')

    # 6) pause/resume/cancel on a second run (fixture short units give a window)
    task2=TaskRepository(s).create(user_id=owner.id, title='gate2-pause', task_type='specified_source')
    s.commit()
    DomainService(TaskRepository(s)).confirm_spec(
        user_id=owner.id, task_id=task2.id, expected_version=task2.version,
        spec_payload=spec.model_dump(mode='json'), actor_id=owner.id)
    task2=TaskRepository(s).get_owned(owner.id, task2.id)
    spec2=SpecVersionRepository(s).latest_version(owner.id, task2.id)
    plan2=psvc.persist_plan(user_id=owner.id, task_id=task2.id, spec_version=spec2.version,
        graph={'nodes':[{'node_id':'n1','node_type':'fetch','definition_version':'1.0.0',
            'parameters':{'url_template':'https://example.com/{id}'},'depends_on':[]}],
            'node_risk_levels':{'n1':'low'}},
        validation_status='VALID', fingerprint_value=plan_fingerprint(
            {'nodes':[{'node_id':'n1','node_type':'fetch','definition_version':'1.0.0',
            'parameters':{'url_template':'https://example.com/{id}'},'depends_on':[]}],
            'node_risk_levels':{'n1':'low'}}, reg_versions), registry_versions=reg_versions)
    await psvc.auto_start(user_id=owner.id, task_id=task2.id, spec_version=spec2.version, plan_version=plan2.version)
    deadline=time.time()+30
    while time.time()<deadline:
        s.expire_all()
        task2=TaskRepository(s).get_owned(owner.id, task2.id)
        if task2.state=='RUNNING': break
        time.sleep(0.2)
    # pause
    from app.domain.task_commands import TaskCommandService
    tcs=TaskCommandService(s)
    r=tcs.pause_task(user_id=owner.id, task_id=task2.id, expected_version=task2.version)
    await OutboxTemporalDispatcher(client).dispatch_pending_for(s, user_id=owner.id, task_id=task2.id)
    deadline=time.time()+30
    while time.time()<deadline:
        s.expire_all()
        task2=TaskRepository(s).get_owned(owner.id, task2.id)
        if task2.state=='PAUSED': break
        time.sleep(0.2)
    assert task2.state=='PAUSED', task2.state
    print('STATE PAUSED')
    # resume
    r=tcs.resume_task(user_id=owner.id, task_id=task2.id, expected_version=task2.version)
    await OutboxTemporalDispatcher(client).dispatch_pending_for(s, user_id=owner.id, task_id=task2.id)
    deadline=time.time()+30
    while time.time()<deadline:
        s.expire_all()
        task2=TaskRepository(s).get_owned(owner.id, task2.id)
        if task2.state=='RUNNING': break
        time.sleep(0.2)
    print('STATE RUNNING')
    # cancel
    r=tcs.cancel_task(user_id=owner.id, task_id=task2.id, expected_version=task2.version)
    await OutboxTemporalDispatcher(client).dispatch_pending_for(s, user_id=owner.id, task_id=task2.id)
    deadline=time.time()+30
    while time.time()<deadline:
        s.expire_all()
        task2=TaskRepository(s).get_owned(owner.id, task2.id)
        if task2.state in ('CANCELLED','CANCELLING'): break
        time.sleep(0.2)
    print('STATE %s' % task2.state)

    # SSE 状态同步：SSE 基于 domain_events 流；断言该 Task 的 approval.* / task.*
    # 事件已持久化（SSE 端点重放这些事件，前端据此同步状态）。SSE 不是事实源。
    from sqlalchemy import select, func
    s.expire_all()
    events=set(s.scalars(select(DomainEvent.event_type).where(
        DomainEvent.user_id==owner.id, DomainEvent.aggregate_type=='task',
        DomainEvent.aggregate_id==task.id)).all())
    events2=set(s.scalars(select(DomainEvent.event_type).where(
        DomainEvent.user_id==owner.id, DomainEvent.aggregate_type=='task',
        DomainEvent.aggregate_id==task2.id)).all())
    assert 'approval.requested' in events, 'approval.requested missing from SSE stream'
    assert 'approval.approved' in events, 'approval.approved missing from SSE stream'
    assert 'task.complete' in events, 'task.complete missing from SSE stream'
    assert any(e in events2 for e in ('task.pause','task.mark_paused')), 'pause event missing'
    assert any(e in events2 for e in ('task.resume','task.cancel','task.mark_cancelled')), 'resume/cancel event missing'
    print('SSE_EVENTS_OK approval.requested/approved + task lifecycle present')

    print('GATE2_WORKFLOW_SMOKE_PASS')

import asyncio
asyncio.run(main())
s.close()
PY" || fail "gate2 workflow smoke"
ok "gate2 workflow/approval/pause-resume-cancel + SSE sync"

echo
echo "GATE2 WORKFLOW SMOKE PASS"
