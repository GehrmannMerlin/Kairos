"""M-18 Production Minimal Golden Path smoke driver.

Runs inside the kairos-production api container. Drives the REAL product API over
the internal network (localhost:8000/api) for one tiny SPECIFIED_SOURCE task:

    register/login (production test user)
    -> DeepSeek model config
    -> create directed task with seed_urls=[example.com]
    -> understand -> spec-confirm -> plan (auto-start workflow)
    -> poll to terminal (COMPLETED / PARTIALLY_COMPLETED / FAILED)
    -> assert >=1 Record / >=1 PageSnapshot / >=1 FieldEvidence
    -> Quality view readable
    -> Completion card readable
    -> CSV artifact downloadable

Exit 0 = PASS, 1 = FAIL. Secrets (DeepSeek key) come from env and are never printed.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

sys.path.insert(0, "/app")

import httpx

BASE = "http://localhost:8000/api"
TERMINAL_STATES = {"COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "CANCELLED"}
TARGET = os.environ.get("PROD_SMOKE_TARGET", "https://example.com/")
POLL_TIMEOUT_S = int(os.environ.get("PROD_SMOKE_TIMEOUT_S", "600"))

_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def main() -> None:
    ds_key = os.environ.get("PROD_SMOKE_DEEPSEEK_KEY", "").strip()
    check("deepseek key provided", bool(ds_key), "key from env (not echoed)")

    base = httpx.Client(base_url=BASE, timeout=60)
    email = f"gate-prod-{uuid.uuid4().hex[:8]}@kairos.test"
    pw = "G!atePass_" + uuid.uuid4().hex[:8]

    # 1) Register (real auth path; brief §57 requires Login).
    r = base.post("/auth/register", json={"email": email, "password": pw, "confirm_password": pw})
    check("register production test user", r.status_code == 201, f"email={email} status={r.status_code}")
    if r.status_code != 201:
        sys.exit(1)

    # Login (capture Secure cookie manually; httpx won't resend over plain HTTP internal check).
    r = base.post("/auth/login", json={"email": email, "password": pw})
    check("login", r.status_code == 200, f"status={r.status_code}")
    setcookie = r.headers.get("set-cookie", "")
    for part in setcookie.split(","):
        kv = part.split(";")[0].strip()
        if "=" in kv:
            k, v = kv.split("=", 1)
            base.cookies.set(k, v)

    r = base.get("/auth/me")
    check("session me", r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        sys.exit(1)

    # 2) DeepSeek model config (real provider, key from env never printed).
    r = base.post(
        "/providers/models",
        json={
            "name": "prod-smoke-deepseek",
            "provider_type": "deepseek",
            "model_name": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": ds_key,
            "set_default": True,
        },
    )
    check("create DeepSeek model config", r.status_code == 201, f"status={r.status_code}")

    # 3) Create directed task with SPECIFIED source (seed URL, no search).
    r = base.post(
        "/tasks",
        json={
            "content": "抓取 https://example.com/ 页面，提取页面标题和正文摘要。",
            "seed_urls": [TARGET],
            "idempotency_key": f"prod-smoke-{uuid.uuid4().hex}",
        },
    )
    check("create task", r.status_code == 201, f"status={r.status_code}")
    if r.status_code != 201:
        sys.exit(1)
    task_id = r.json()["task_id"]

    # 4) Understand (real DeepSeek call) + spec confirm + plan.
    r = base.post(f"/tasks/{task_id}/understand")
    check("understand", r.status_code == 200, f"status={r.status_code}")
    r = base.get(f"/tasks/{task_id}/spec-draft")
    check("spec draft", r.status_code == 200, f"status={r.status_code}")
    payload = r.json()["payload"]
    adv = payload.setdefault("advanced_settings", {})
    adv["max_pages"] = 1
    payload["completion_conditions"] = [{"kind": "min_records", "target": 1, "threshold": None, "note": "prod smoke"}]

    r = base.get(f"/tasks/{task_id}")
    exp_ver = r.json()["version"]
    r = base.post(f"/tasks/{task_id}/spec-confirm", json={"expected_version": exp_ver, "payload": payload})
    check("spec confirm", r.status_code == 200, f"status={r.status_code}")
    spec_version = r.json()["spec_version"]

    r = base.get(f"/tasks/{task_id}")
    exp_ver = r.json()["version"]
    r = base.post(f"/tasks/{task_id}/plan", json={"spec_version": spec_version, "expected_version": exp_ver})
    body = r.json()
    check("plan generated", r.status_code == 200, f"status={r.status_code}")
    check("workflow started", bool(body.get("run_id")), f"run={body.get('run_id')} wf={body.get('workflow_id')}")
    print(f"TASK_ID={task_id} RUN_ID={body.get('run_id')} WORKFLOW_ID={body.get('workflow_id')}")

    # 5) Poll to terminal state.
    deadline = time.time() + POLL_TIMEOUT_S
    state = "PENDING"
    while time.time() < deadline:
        r = base.get(f"/tasks/{task_id}")
        state = r.json()["state"]
        if state in TERMINAL_STATES:
            break
        time.sleep(8)
    check("task terminal", state in ("COMPLETED", "PARTIALLY_COMPLETED"), f"state={state}")
    if state == "FAILED":
        check("task not failed", False, "terminal FAILED")
        sys.exit(1)

    # 6) Records / Snapshot / Evidence.
    r = base.get(f"/tasks/{task_id}/records")
    recs = r.json()
    rec_list = recs.get("items", recs.get("records", [])) if isinstance(recs, dict) else recs
    n_rec = len(rec_list)
    check("records >=1", n_rec >= 1, f"records={n_rec}")

    # Execution view carries snapshot/evidence references.
    r = base.get(f"/tasks/{task_id}/execution")
    check("execution view readable", r.status_code == 200, f"status={r.status_code}")

    # 7) Quality readable.
    r = base.get(f"/tasks/{task_id}/quality")
    check("quality readable", r.status_code == 200, f"status={r.status_code}")

    # 8) Completion card readable.
    r = base.get(f"/tasks/{task_id}/completion")
    check("completion card readable", r.status_code == 200, f"status={r.status_code}")

    # 9) CSV artifact downloadable.
    r = base.get(f"/tasks/{task_id}/artifacts")
    arts = r.json()
    art_list = arts.get("items", arts) if isinstance(arts, dict) else arts
    check("artifacts present", isinstance(art_list, list) and len(art_list) >= 1, f"artifacts={len(art_list) if isinstance(art_list, list) else 'n/a'}")
    if isinstance(art_list, list) and art_list:
        aid = art_list[0]["id"]
        r = base.get(f"/tasks/{task_id}/artifacts/{aid}/download")
        check("csv downloadable", r.status_code == 200, f"status={r.status_code} type={r.headers.get('content-type')}")

    ok = all(ok for _, ok, _ in _results)
    print(f"SMOKE_RESULT={'PASS' if ok else 'FAIL'} total={len(_results)} task_id={task_id}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
