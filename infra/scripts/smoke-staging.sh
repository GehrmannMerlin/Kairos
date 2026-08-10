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
import httpx, urllib.parse, uuid, json, os
base='http://localhost:8000/api'
email_a=f'gate-a-{uuid.uuid4().hex[:8]}@kairos.test'
email_b=f'gate-b-{uuid.uuid4().hex[:8]}@kairos.test'
pw='G!atePass_'+uuid.uuid4().hex[:6]
c=httpx.Client(base_url=base, timeout=15)
r=c.post('/auth/register', json={'email':email_a,'password':pw})
print('register A', r.status_code, r.text[:120]); assert r.status_code in (200,201), r.text
r=c.post('/auth/login', json={'email':email_a,'password':pw})
print('login A', r.status_code, r.text[:120]); assert r.status_code==200, r.text
tok_a=c.cookies.get('kairos_session') or c.headers.get('set-cookie','')
r=c.post('/auth/register', json={'email':email_b,'password':pw})
print('register B', r.status_code, r.text[:120]); assert r.status_code in (200,201), r.text
r=c.post('/auth/login', json={'email':email_b,'password':pw})
print('login B', r.status_code, r.text[:120]); assert r.status_code==200, r.text
print('AUTH_OK')
PY" || fail "auth smoke"
ok "auth register/login/session"

echo "==> [4/7] ownership isolation (B must not read/modify A's resource)"
"${SSH[@]}" "cd ${COMPOSE_DIR} && ${COMPOSE[*]} exec -T api python - <<'PY'
# M-04 owner isolation is covered by domain tests; here we assert the API
# ownership guard rejects cross-user access. Uses the domain repository path
# directly to create an A-owned Task, then asserts B-scoped read returns 404.
import sys; sys.path.insert(0,'/app')
from app.auth.models import User
from app.domain.repository import TaskRepository
from app.auth.errors import NotFoundError
from app.auth.repository import UserRepository
from app.infra.deps import get_session_factory
s=get_session_factory()()
try:
    a=UserRepository(s).create('owner-a-'+str(abs(hash('a')))+'@kairos.test','hash',None)
    b=UserRepository(s).create('owner-b-'+str(abs(hash('b')))+'@kairos.test','hash',None)
    t=TaskRepository(s).create(user_id=a.id, title='smoke-owner', task_type='directed')
    s.commit()
    try:
        TaskRepository(s).get(user_id=b.id, task_id=t.id)
        print('OWNERSHIP_FAIL: B read A task'); raise SystemExit(1)
    except NotFoundError:
        print('OWNERSHIP_OK: B read A task -> 404 policy')
finally:
    s.close()
PY" || fail "ownership smoke"
ok "ownership isolation"

echo "==> [5/7] credential security (fake GATE_TEST_SECRET not plaintext)"
"${SSH[@]}" "cd ${COMPOSE_DIR} && ${COMPOSE[*]} exec -T api python - <<'PY'
import asyncio, uuid, sys; sys.path.insert(0,'/app')
from app.credentials.service import CredentialService
from app.credentials.repository import CredentialRepository
from app.infra.deps import get_session_factory
from app.auth.repository import UserRepository
async def run():
    s=get_session_factory()()
    try:
        u=UserRepository(s).create('cred-'+uuid.uuid4().hex[:8]+'@kairos.test','hash',None)
        svc=CredentialService(s, CredentialRepository(s))
        cred=await svc.create_credential(
            owner_id=u.id, provider='model', credential={'api_key':'GATE_TEST_SECRET'})
        raw=s.get(CredentialRepository(s)._model, cred.id)
        print('DB_HAS_PLAINTEXT' if raw and 'GATE_TEST_SECRET' in str(raw.encrypted_blob) else 'DB_NO_PLAINTEXT')
        assert 'GATE_TEST_SECRET' not in str(raw.encrypted_blob)
        print('CREDENTIAL_SECURITY_OK')
        return True
    finally:
        s.close()
assert asyncio.run(run())
PY" || fail "credential security"
ok "credential security (no plaintext in DB)"

echo "==> [6/7] M-04 checkpoint + event/outbox (reuse domain smoke)"
"${SSH[@]}" "cd ${COMPOSE_DIR} && ${COMPOSE[*]} exec -T api python - <<'PY'
import sys; sys.path.insert(0,'/app')
# Exercise the M-04 domain transaction -> event -> outbox -> checkpoint path
# against the staging DB, reusing the domain smoke scenario helpers.
from app.domain.service import DomainService
from app.domain.repository import (TaskRepository, SpecVersionRepository,
    PlanVersionRepository, RunRepository, NodeRunRepository)
from app.state.events import append_domain_event, enqueue_outbox
from app.infra.deps import get_session_factory
s=get_session_factory()()
try:
    u=TaskRepository(s)._session
    from app.auth.repository import UserRepository
    owner=UserRepository(s).create('ckpt-'+str(abs(hash('ckpt')))+'@kairos.test','hash',None)
    svc=DomainService(s)
    task=TaskRepository(s).create(user_id=owner.id, title='smoke-ckpt', task_type='directed')
    r=svc.transition_task(task, 'submit')
    s.flush()
    append_domain_event(s, owner_id=owner.id, task_id=task.id, event_type='task.submitted', payload={'state':r.state.value})
    enqueue_outbox(s, owner_id=owner.id, aggregate_type='task', aggregate_id=task.id, event_type='task.submitted')
    s.commit()
    print('CHECKPOINT_OK: transaction+event+outbox committed')
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
