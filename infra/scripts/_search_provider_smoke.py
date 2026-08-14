# D-074 Search Provider staging/production smoke（api 容器内跑，连接环境 DB/API）。
# 覆盖：Tavily 定义字段（requires_base_url=false）→ 未保存 probe（真实请求 + latency，
# 不落库）→ Tavily 无 Base URL 保存（回归）→ 列表回读 → 清理。不含真实 Key 时 probe
# 返回 AUTH_FAILED 仍通过（证明端到端请求路径）；真实 AVAILABLE 由 Adapter 单测覆盖。
# 任何响应/日志不得出现 fake key 明文。
import uuid

import httpx

BASE = "http://localhost:8000/api"
FAKE_KEY = "tvly-" + uuid.uuid4().hex  # 明确 fake，绝不使用真实凭据
_results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def login(c: httpx.Client, email: str, password: str) -> None:
    r = c.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    for part in (r.headers.get("set-cookie", "")).split(","):
        kv = part.split(";")[0].strip()
        if "=" in kv:
            k, v = kv.split("=", 1)
            c.cookies.set(k, v)


def main() -> None:
    email = f"search-smoke-{uuid.uuid4().hex[:8]}@kairos.test"
    pw = "G!atePass_" + uuid.uuid4().hex[:6]

    c = httpx.Client(base_url=BASE, timeout=20)
    r = c.post("/auth/register", json={"email": email, "password": pw, "confirm_password": pw})
    assert r.status_code == 201, r.text
    login(c, email, pw)

    # 1. Tavily 定义字段（Registry 事实来源）
    defs = c.get("/providers/definitions").json()
    tavily = next((d for d in defs["searches"] if d["provider_type"] == "tavily"), None)
    check("tavily in search definitions", tavily is not None)
    check("tavily requires_api_key", tavily is not None and tavily["requires_api_key"] is True)
    check(
        "tavily requires_base_url=false",
        tavily is not None and tavily["requires_base_url"] is False
        and tavily["base_url_mode"] == "managed",
    )

    # 2. 未保存 probe：真实最小请求，返回稳定状态 + latency，不落库
    probe = c.post(
        "/providers/searches/probe",
        json={"provider_type": "tavily", "api_key": FAKE_KEY},
    )
    check("probe returns 200", probe.status_code == 200, probe.text[:120])
    pbody = probe.json()
    check(
        "probe status in stable set",
        pbody.get("status") in ("AVAILABLE", "AUTH_FAILED", "RATE_LIMITED", "NETWORK_ERROR", "FAILED"),
        str(pbody.get("status")),
    )
    check("probe latency_ms present", isinstance(pbody.get("latency_ms"), int))
    check("probe provider_type", pbody.get("provider_type") == "tavily")
    check("probe does not echo key", FAKE_KEY not in probe.text)

    # 3. Tavily 无 Base URL 保存（回归：不得要求 Base URL）
    created = c.post(
        "/providers/searches",
        json={"name": "smoke-tavily", "provider_type": "tavily", "api_key": FAKE_KEY},
    )
    check("tavily create without base_url 201", created.status_code == 201, created.text[:200])
    cbody = created.json()
    check("created base_url is null", cbody.get("base_url") is None)
    check("created credential_configured", cbody.get("credential_configured") is True)
    check("create response hides key", FAKE_KEY not in created.text)
    config_id = cbody.get("config_id")

    # 4. 列表回读：配置存在
    listing = c.get("/providers/searches").json()["configs"]
    found = any(x["config_id"] == config_id for x in listing)
    check("config persists in list", found)
    check("list hides key", FAKE_KEY not in str(listing))

    # 5. 已保存配置测试（复用 /searches/{id}/test），随后清理
    tested = c.post(f"/providers/searches/{config_id}/test")
    check("saved-config test 200", tested.status_code == 200, tested.text[:120])
    tbody = tested.json()
    check(
        "saved-config test status stable",
        tbody.get("status") in ("AVAILABLE", "AUTH_FAILED", "RATE_LIMITED", "NETWORK_ERROR", "FAILED"),
        str(tbody.get("status")),
    )
    check("saved-config test hides key", FAKE_KEY not in tested.text)

    deleted = c.delete(f"/providers/searches/{config_id}")
    check("cleanup delete 204", deleted.status_code == 204, str(deleted.status_code))

    failed = [n for n, ok in _results if not ok]
    print(f"RESULT: {'PASS' if not failed else 'FAIL'} ({len(_results)} checks)")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
