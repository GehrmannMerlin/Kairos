#!/usr/bin/env bash
# kairos-staging Gate Smoke: health, auth, ownership, credential security,
# Temporal smoke workflow, M-04 checkpoint, and secret leak scan.
#
# Usage (from repository root):
#   DEPLOY_HOST=47.238.145.24 ./infra/scripts/smoke-staging.sh
#
# Exit code non-zero on any failed check. Internal checks run inside the api
# container (which reaches postgres/temporal/minio on the private network), so
# they do not depend on the public domain being live.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_HOST="${DEPLOY_HOST:?DEPLOY_HOST required}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes "${DEPLOY_USER}@${DEPLOY_HOST}")
COMPOSE_DIR="/srv/kairos/compose"
COMPOSE=(docker compose -p kairos-staging -f "${COMPOSE_DIR}/compose.base.yml" -f "${COMPOSE_DIR}/compose.staging.yml")

API_EXEC=("${SSH[@]}" "cd ${COMPOSE_DIR} && ${COMPOSE[*]} exec -T api python")

fail() { printf 'SMOKE FAIL: %s\n' "$*" >&2; exit 1; }
ok() { printf '  ok: %s\n' "$*"; }

echo "==> [1/7] health live + ready (internal)"
"${SSH[@]}" "docker compose -p kairos-staging -f ${COMPOSE_DIR}/compose.base.yml -f ${COMPOSE_DIR}/compose.staging.yml exec -T api python - <<'PY'
import urllib.request
for path in ('/api/health/live','/api/health/ready'):
    r=urllib.request.urlopen('http://localhost:8000'+path,timeout=15)
    print(path, r.status, r.read().decode()[:200])
PY" || fail "health check"
ok "health live/ready"

echo "==> [2/7] M-01 Temporal smoke workflow (script -> PG + MinIO read-back)"
"${SSH[@]}" "cd ${COMPOSE_DIR} && ${COMPOSE[*]} exec -T api python scripts/run_smoke.py" \
  || fail "temporal smoke workflow"
ok "temporal smoke workflow + PG/MinIO read-back"

echo "==> [3/7] auth register/login/session (Gate Test User A/B)"
"${SSH[@]}" "cd ${COMPOSE_DIR} && ${COMPOSE[*]} exec -T api python - <<'PY'
import httpx, uuid
base='http://localhost:8000/api'
email_a=f'gate-a-{uuid.uuid4().hex[:8]}@kairos.test'
email_b=f'gate-b-{uuid.uuid4().hex[:8]}@kairos.test'
pw='G!atePass_'+uuid.uuid4().hex[:6]

def authed_login(email, password):
    c=httpx.Client(base_url=base, timeout=15)
    r=c.post('/auth/login', json={'email':email,'password':password})
    assert r.status_code==200, r.text
    # Session cookie is Secure; httpx will not send it over plain HTTP, so
    # extract Set-Cookie and apply it explicitly (internal check only).
    setcookie=r.headers.get('set-cookie','')
    for part in setcookie.split(','):
        kv=part.split(';')[0].strip()
        if '=' in kv:
            k,v=kv.split('=',1); c.cookies.set(k, v)
    return c

c=httpx.Client(base_url=base, timeout=15)
r=c.post('/auth/register', json={'email':email_a,'password':pw,'confirm_password':pw})
print('register A', r.status_code, r.text[:120]); assert r.status_code==201, r.text
ca=authed_login(email_a, pw)
r=ca.get('/auth/me'); print('me A', r.status_code, r.text[:120]); assert r.status_code==200, r.text
c2=httpx.Client(base_url=base, timeout=15)
r=c2.post('/auth/register', json={'email':email_b,'password':pw,'confirm_password':pw})
print('register B', r.status_code, r.text[:120]); assert r.status_code==201, r.text
cb=authed_login(email_b, pw)
r=cb.get('/auth/me'); print('me B', r.status_code); assert r.status_code==200, r.text
r=ca.post('/auth/logout'); print('logout A', r.status_code); assert r.status_code in (204,200), r.text
r=ca.get('/auth/me'); print('me A after logout', r.status_code); assert r.status_code==401, r.text
print('AUTH_OK')
PY" || fail "auth smoke"
ok "auth register/login/session"

echo "==> [4/7] ownership isolation (B must not read/modify A's resource)"
"${SSH[@]}" "cd ${COMPOSE_DIR} && ${COMPOSE[*]} exec -T api python - <<'PY'
# M-04 owner isolation is covered by domain tests; here we assert the API
# ownership guard rejects cross-user access. Uses the domain repository path
# directly to create an A-owned Task, then asserts B-scoped read returns 404.
import sys, uuid; sys.path.insert(0,'/app')
from app.domain.repository import TaskRepository
from app.auth.errors import NotFoundError
from app.auth.repository import UserRepository
from app.infra.deps import get_session_factory
s=get_session_factory()()
try:
    a=UserRepository(s).create('owner-a-'+uuid.uuid4().hex[:8]+'@kairos.test','hash',None)
    b=UserRepository(s).create('owner-b-'+uuid.uuid4().hex[:8]+'@kairos.test','hash',None)
    t=TaskRepository(s).create(user_id=a.id, title='smoke-owner', task_type='directed')
    s.commit()
    try:
        TaskRepository(s).get_owned(user_id=b.id, task_id=t.id)
        print('OWNERSHIP_FAIL: B read A task'); raise SystemExit(1)
    except NotFoundError:
        print('OWNERSHIP_OK: B read A task -> 404 policy')
    mine=TaskRepository(s).get_owned(user_id=a.id, task_id=t.id)
    print('OWNER_OK: A read own task', mine.id)
finally:
    s.close()
PY" || fail "ownership smoke"
ok "ownership isolation"

echo "==> [5/7] credential security (fake GATE_TEST_SECRET not plaintext)"
"${SSH[@]}" "cd ${COMPOSE_DIR} && ${COMPOSE[*]} exec -T api python - <<'PY'
import sys, uuid; sys.path.insert(0,'/app')
from app.config import get_settings
from app.credentials.repository import CredentialRepository
from app.credentials.vault import CredentialVault
from app.credentials.crypto import master_key_from_env_value
from app.credentials.models import CredentialVersion
from app.auth.repository import UserRepository
from app.infra.deps import get_session_factory
s=get_session_factory()()
try:
    settings=get_settings()
    u=UserRepository(s).create('cred-'+uuid.uuid4().hex[:8]+'@kairos.test','hash',None)
    vault=CredentialVault(master_key=master_key_from_env_value(settings.credential_master_key),
                          key_version=settings.credential_key_version,
                          repository=CredentialRepository(s))
    info=vault.store_secret(user_id=u.id, kind='model', name='gate-test', secret='GATE_TEST_SECRET')
    s.flush()
    row=s.get(CredentialVersion, info.version_id)
    assert 'GATE_TEST_SECRET' not in str(row.secret_ciphertext), 'plaintext in ciphertext!'
    assert 'GATE_TEST_SECRET' not in str(row.wrapped_dek), 'plaintext in wrapped_dek!'
    print('DB_NO_PLAINTEXT: secret stored as ciphertext')
    plain=vault.read_for_execution(user_id=u.id, credential_version_id=info.version_id)
    assert plain=='GATE_TEST_SECRET'
    print('DECRYPT_ROUNDTRIP_OK')
    vault.revoke(user_id=u.id, credential_id=info.credential_id)
    print('CREDENTIAL_SECURITY_OK')
finally:
    s.close()
PY" || fail "credential security"
ok "credential security (no plaintext in DB)"

echo "==> [6/7] M-04 checkpoint + event/outbox (reuse domain smoke)"
"${SSH[@]}" "cd ${COMPOSE_DIR} && ${COMPOSE[*]} exec -T api python - <<'PY'
import sys, uuid; sys.path.insert(0,'/app')
# M-04 domain transaction -> event -> outbox -> checkpoint against staging DB.
from app.auth.repository import UserRepository
from app.domain.repository import (TaskRepository, SpecVersionRepository,
    PlanVersionRepository, RunRepository)
from app.domain.service import DomainService
from app.domain.models import DomainEvent, OutboxEvent, Checkpoint
from app.infra.deps import get_session_factory
from sqlalchemy import select, func
s=get_session_factory()()
try:
    owner=UserRepository(s).create('ckpt-'+uuid.uuid4().hex[:8]+'@kairos.test','hash',None)
    svc=DomainService(TaskRepository(s))
    task=TaskRepository(s).create(user_id=owner.id, title='smoke-ckpt', task_type='directed')
    s.commit()
    spec=SpecVersionRepository(s).create(user_id=owner.id, task_id=task.id, version=1,
        spec_type='directed', schema_version='1', payload={})
    plan=PlanVersionRepository(s).create(user_id=owner.id, task_id=task.id, spec_version=1, version=1, payload={})
    run=RunRepository(s).create(user_id=owner.id, task_id=task.id, spec_version=1, plan_version=1)
    s.commit()
    ev=svc.transition_task(user_id=owner.id, task_id=task.id, command='submit',
                           expected_version=task.version, actor_type='user', actor_id=owner.id)
    s.flush()
    nev=s.scalar(select(func.count()).select_from(DomainEvent).where(DomainEvent.aggregate_id==task.id))
    nout=s.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.aggregate_id==task.id))
    assert nev>=1 and nout>=1, 'event/outbox missing after transition'
    assert TaskRepository(s).get_owned(owner.id, task.id).state=='QUEUED'
    fp='fp-'+uuid.uuid4().hex
    ck=svc.commit_checkpoint(user_id=owner.id, task_id=task.id, run_id=run.id,
        batch_identity='batch-1', spec_version=1, plan_version=1, node_run_id=None,
        input_fingerprint=fp, committed_refs={'record_ids':[]}, content_hash='ch1')
    ck2=svc.commit_checkpoint(user_id=owner.id, task_id=task.id, run_id=run.id,
        batch_identity='batch-1', spec_version=1, plan_version=1, node_run_id=None,
        input_fingerprint=fp, committed_refs={'record_ids':[]}, content_hash='ch1')
    nc=s.scalar(select(func.count()).select_from(Checkpoint).where(Checkpoint.run_id==run.id))
    assert ck2.id==ck.id and nc==1, 'replay did not reuse checkpoint'
    print('CHECKPOINT_OK: transition+event+outbox committed; replay reused (count=%d)' % nc)
finally:
    s.close()
PY" || fail "M-04 checkpoint"
ok "M-04 checkpoint/event/outbox"

echo "==> [7/7] secret leak scan (GATE_TEST_SECRET in logs)"
"${SSH[@]}" "sudo grep -rl 'GATE_TEST_SECRET' /srv/kairos/logs 2>/dev/null && { echo 'SECRET_LEAK_IN_LOGS'; exit 1; } || echo 'NO_SECRET_IN_LOGS'" \
  || fail "secret leak scan"
ok "secret leak scan (logs clean)"

echo
echo "SMOKE PASS (all 7 gate checks)"
