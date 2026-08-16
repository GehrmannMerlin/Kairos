"""M-14 Plan DAG + Node Detail Query API（D-063/D-055）。

验证（A-Lite 紧凑套件）：
1. frozen PlanVersion → 正确 DAG DTO（nodes/edges round-trip + stage 映射 + resource class）。
2. Node Detail 展示 status/version/technical stats。
3. 敏感参数键（credential_ref 等）不返回。
4. 不存在的 node_id → 404。
5. owner isolation：跨用户 → 404。
6. 无 Plan → 空 DagView（不伪造）。
"""

from __future__ import annotations

from app.domain.models import DomainEvent, NodeAttempt, NodeRun, PlanVersion, Run, URLResource
from app.domain.repository import TaskRepository
from fastapi.testclient import TestClient


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "confirm_password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_plan(factory, user_id: int, task_id: int) -> int:
    session = factory()
    try:
        plan = PlanVersion(
            user_id=user_id,
            task_id=task_id,
            spec_version=1,
            version=1,
            validation_status="VALID",
            plan_fingerprint="fp",
            payload={
                "graph": {
                    "nodes": [
                        {
                            "node_id": "n-source",
                            "node_type": "source_search",
                            "definition_version": "1.0.0",
                            "parameters": {"query": "深圳工业自动化", "max_results": 20},
                            "depends_on": [],
                            "optional": False,
                            "fail_policy": "block",
                        },
                        {
                            "node_id": "n-fetch",
                            "node_type": "fetch",
                            "definition_version": "1.0.0",
                            "parameters": {
                                "url_template": "https://example.com/{id}",
                                "credential_ref": "cred-1",
                                "render_if_empty": True,
                            },
                            "depends_on": ["n-source"],
                            "optional": False,
                            "fail_policy": "retry",
                        },
                        {
                            "node_id": "n-extract",
                            "node_type": "extract",
                            "definition_version": "1.0.0",
                            "parameters": {"fields": ["company", "phone"]},
                            "depends_on": ["n-fetch"],
                            "optional": True,
                            "fail_policy": "skip",
                        },
                    ],
                    "edges": [
                        {"from_node_id": "n-source", "to_node_id": "n-fetch"},
                        {"from_node_id": "n-fetch", "to_node_id": "n-extract"},
                    ],
                }
            },
        )
        session.add(plan)
        session.flush()
        plan_id = plan.id
        run = Run(
            user_id=user_id,
            task_id=task_id,
            spec_version=1,
            plan_version=1,
            state="COMPLETED",
        )
        session.add(run)
        session.commit()
        return plan_id
    finally:
        session.close()


def test_dag_round_trip_nodes_edges_and_resource_class(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="seed", task_type="directed"
        )
        session.flush()
        task_id = task.id
    finally:
        session.close()
    _seed_plan(factory, alice["id"], task_id)

    resp = c.get(f"/api/tasks/{task_id}/execution/dag")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan_version"] == 1
    assert len(body["nodes"]) == 3
    assert len(body["edges"]) == 2
    nodes = {n["node_id"]: n for n in body["nodes"]}
    assert nodes["n-source"]["resource_class"] == "llm_search"
    assert nodes["n-fetch"]["resource_class"] == "http"
    assert nodes["n-source"]["stage"] == "source_discovery"
    assert nodes["n-fetch"]["stage"] == "fetch"
    assert nodes["n-extract"]["stage"] == "extraction"
    assert nodes["n-extract"]["optional"] is True
    assert body["stage_status"]["fetch"] == "not_started"  # 无事件 → 诚实空状态


def test_dag_redacts_sensitive_parameter_keys(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="seed", task_type="directed"
        )
        session.flush()
        task_id = task.id
    finally:
        session.close()
    _seed_plan(factory, alice["id"], task_id)

    resp = c.get(f"/api/tasks/{task_id}/execution/dag")
    raw = resp.text
    assert "credential_ref" not in raw
    assert "cred-1" not in raw
    assert "url_template" in raw  # 非 secret 标量正常展示


def test_node_detail_shows_definition_and_technical_stats(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="seed", task_type="directed"
        )
        session.flush()
        task_id = task.id
    finally:
        session.close()
    _seed_plan(factory, alice["id"], task_id)

    resp = c.get(f"/api/tasks/{task_id}/execution/nodes/n-fetch")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["node_type"] == "fetch"
    assert body["definition_version"] == "1.0.0"
    assert body["resource_class"] == "http"
    assert body["run"]["state"] == "COMPLETED"
    assert body["parameters_summary"] == {
        "url_template": "https://example.com/{id}",
        "render_if_empty": True,
    }
    assert body["execution"]["event_count"] == 0  # 无节点事件 → 诚实 0


def test_node_detail_unknown_node_404(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="seed", task_type="directed"
        )
        session.flush()
        task_id = task.id
    finally:
        session.close()
    _seed_plan(factory, alice["id"], task_id)

    resp = c.get(f"/api/tasks/{task_id}/execution/nodes/nope")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "NOT_FOUND"


def test_dag_cross_user_404(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="seed", task_type="directed"
        )
        session.flush()
        task_id = task.id
    finally:
        session.close()
    _seed_plan(factory, alice["id"], task_id)
    _register(c, "bob@example.com")

    resp = c.get(f"/api/tasks/{task_id}/execution/dag")
    assert resp.status_code == 404


def test_dag_without_plan_is_empty(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="seed", task_type="directed"
        )
        session.commit()
        task_id = task.id
    finally:
        session.close()

    resp = c.get(f"/api/tasks/{task_id}/execution/dag")
    assert resp.status_code == 200
    body = resp.json()
    assert body["nodes"] == []
    assert body["edges"] == []


def test_node_detail_prefers_persisted_node_attempt_facts(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "node-facts@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="seed", task_type="directed"
        )
        session.flush()
        task_id = task.id
    finally:
        session.close()
    _seed_plan(factory, alice["id"], task_id)
    session = factory()
    try:
        run = session.query(Run).filter(Run.task_id == task_id).one()
        node = NodeRun(
            user_id=alice["id"],
            task_id=task_id,
            run_id=run.id,
            node_id="n-fetch",
            node_type="fetch",
            state="SUCCEEDED",
            position=2,
        )
        session.add(node)
        session.flush()
        session.add(
            NodeAttempt(
                user_id=alice["id"],
                node_run_id=node.id,
                attempt=2,
                status="SUCCEEDED",
            )
        )
        session.commit()
    finally:
        session.close()

    resp = c.get(f"/api/tasks/{task_id}/execution/nodes/n-fetch")

    assert resp.status_code == 200, resp.text
    execution = resp.json()["execution"]
    assert execution["last_status"] == "SUCCEEDED"
    assert execution["attempt_count"] == 2


def test_dag_and_node_detail_use_latest_run_frozen_plan_and_run_scoped_facts(
    client: dict,
) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "frozen-dag@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="frozen dag", task_type="directed"
        )
        session.flush()
        plan_v1 = PlanVersion(
            user_id=alice["id"],
            task_id=task.id,
            spec_version=1,
            version=1,
            validation_status="VALID",
            plan_fingerprint="v1",
            payload={
                "graph": {
                    "nodes": [
                        {
                            "node_id": "shared",
                            "node_type": "fetch",
                            "definition_version": "1.0.0",
                            "parameters": {"generation": "v1"},
                            "depends_on": [],
                        }
                    ],
                    "edges": [],
                }
            },
        )
        plan_v2 = PlanVersion(
            user_id=alice["id"],
            task_id=task.id,
            spec_version=2,
            version=2,
            validation_status="VALID",
            plan_fingerprint="v2",
            payload={
                "graph": {
                    "nodes": [
                        {
                            "node_id": "shared",
                            "node_type": "validate",
                            "definition_version": "2.0.0",
                            "parameters": {"generation": "v2"},
                            "depends_on": [],
                        },
                        {
                            "node_id": "new-only",
                            "node_type": "extract",
                            "definition_version": "2.0.0",
                            "parameters": {},
                            "depends_on": ["shared"],
                        },
                    ],
                    "edges": [{"from_node_id": "shared", "to_node_id": "new-only"}],
                }
            },
        )
        session.add_all([plan_v1, plan_v2])
        session.flush()
        old_run = Run(
            user_id=alice["id"],
            task_id=task.id,
            spec_version=1,
            plan_version=1,
            state="COMPLETED",
        )
        current_run = Run(
            user_id=alice["id"],
            task_id=task.id,
            spec_version=1,
            plan_version=1,
            state="RUNNING",
        )
        session.add_all([old_run, current_run])
        session.flush()
        node = NodeRun(
            user_id=alice["id"],
            task_id=task.id,
            run_id=current_run.id,
            node_id="shared",
            node_type="fetch",
            state="SUCCEEDED",
            position=1,
        )
        session.add(node)
        session.flush()
        session.add(
            NodeAttempt(
                user_id=alice["id"],
                node_run_id=node.id,
                attempt=2,
                status="SUCCEEDED",
            )
        )
        session.add_all(
            [
                DomainEvent(
                    user_id=alice["id"],
                    aggregate_type="task",
                    aggregate_id=task.id,
                    event_type="discovery.expanded",
                    aggregate_version=1,
                    payload={"added": 8},
                    run_id=old_run.id,
                ),
                DomainEvent(
                    user_id=alice["id"],
                    aggregate_type="task",
                    aggregate_id=task.id,
                    event_type="fetch.failed",
                    aggregate_version=2,
                    payload={
                        "node_id": "shared",
                        "node_type": "fetch",
                        "tool": "old-run-tool",
                        "attempt": 9,
                        "state": "FAILED",
                    },
                    run_id=old_run.id,
                ),
                DomainEvent(
                    user_id=alice["id"],
                    aggregate_type="task",
                    aggregate_id=task.id,
                    event_type="fetch.completed",
                    aggregate_version=3,
                    payload={
                        "node_id": "shared",
                        "node_type": "fetch",
                        "tool": "current-run-tool",
                        "url_hash": "old-dag-0",
                        "attempt": 2,
                        "state": "SUCCEEDED",
                    },
                    run_id=current_run.id,
                ),
            ]
        )
        for index in range(3):
            session.add(
                URLResource(
                    user_id=alice["id"],
                    task_id=task.id,
                    run_id=old_run.id,
                    spec_version=1,
                    url=f"https://old.example/{index}",
                    url_hash=f"old-dag-{index}",
                    status="FETCHED",
                )
            )
        session.commit()
        task_id = task.id
    finally:
        session.close()

    dag_response = c.get(f"/api/tasks/{task_id}/execution/dag")
    detail_response = c.get(f"/api/tasks/{task_id}/execution/nodes/shared")
    new_only_response = c.get(f"/api/tasks/{task_id}/execution/nodes/new-only")

    assert dag_response.status_code == 200, dag_response.text
    dag = dag_response.json()
    assert dag["plan_version"] == 1
    assert [node["node_id"] for node in dag["nodes"]] == ["shared"]
    assert dag["nodes"][0]["node_type"] == "fetch"
    assert dag["nodes"][0]["execution"]["event_count"] == 1
    assert dag["nodes"][0]["execution"]["tool"] == "current-run-tool"
    assert dag["nodes"][0]["execution"]["url_fetched_count"] == 1
    assert dag["stage_status"]["source_discovery"] == "not_started"

    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    assert detail["plan_version"] == 1
    assert detail["node_type"] == "fetch"
    assert detail["parameters_summary"] == {"generation": "v1"}
    assert detail["execution"]["event_count"] == 1
    assert detail["execution"]["tool"] == "current-run-tool"
    assert detail["execution"]["url_fetched_count"] == 1
    assert new_only_response.status_code == 404
