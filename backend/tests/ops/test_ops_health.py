"""TEST F：ops-health 判定逻辑纯函数（mock 输入 → PASS/P0/P1）。

判定规则（与 infra/scripts/ops-health.sh 一致）：
- API live/ready 异常 → P0
- 任一业务容器 down → P0
- 任一磁盘 >= 90% → P1
- 容器 restart loop（>5）→ P1
- 其余 → PASS
"""

from __future__ import annotations

P0 = "P0"
P1 = "P1"
PASS = "PASS"


def verdict(checks: dict[str, object]) -> str:
    if checks.get("api_live") != "ok" or checks.get("api_ready") != "ok":
        return P0
    for name, st in checks.items():
        if name.startswith("container_") and st == "down":
            return P0
    if any(v >= 90 for k, v in checks.items() if k.startswith("disk_") and isinstance(v, int)):
        return P1
    if checks.get("restart_loop"):
        return P1
    return PASS


def test_all_green_is_pass() -> None:
    assert (
        verdict(
            {
                "api_live": "ok",
                "api_ready": "ok",
                "container_worker": "running",
                "disk_root": 55,
            }
        )
        == PASS
    )


def test_api_live_down_is_p0() -> None:
    assert verdict({"api_live": "down", "api_ready": "ok"}) == P0


def test_api_ready_degraded_is_p0() -> None:
    assert verdict({"api_live": "ok", "api_ready": "degraded"}) == P0


def test_container_down_is_p0() -> None:
    assert verdict({"api_live": "ok", "api_ready": "ok", "container_worker": "down"}) == P0


def test_disk_high_is_p1() -> None:
    assert verdict({"api_live": "ok", "api_ready": "ok", "disk_root": 95}) == P1


def test_restart_loop_is_p1() -> None:
    assert verdict({"api_live": "ok", "api_ready": "ok", "restart_loop": 6}) == P1


def test_mixed_checks_do_not_mask_p0() -> None:
    assert (
        verdict({"api_live": "ok", "api_ready": "degraded", "disk_root": 99, "restart_loop": 9})
        == P0
    )
