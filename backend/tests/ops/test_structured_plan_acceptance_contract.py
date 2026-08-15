"""Release-gate contract for the real DeepSeek structured-plan harness."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HARNESS = ROOT / "infra/scripts/structured-plan-staging-acceptance.py"

PROMPTS = (
    "采集山东省人民政府官网发布的最近一个月的干部任前公示信息",
    "采集上海市人民政府官网最近一个月的任前公示信息",
)
REQUIRED_RESULT_FIELDS = {
    "goal_ms",
    "plan_model_1_ms",
    "repair_used",
    "plan_model_2_ms",
    "plan_total_ms",
    "validation_result",
    "plan_version",
    "run_id",
    "workflow_id",
}


def _source() -> str:
    return HARNESS.read_text(encoding="utf-8")


def test_harness_contains_exact_real_provider_cases_and_safe_result_contract() -> None:
    source = _source()

    for prompt in PROMPTS:
        assert prompt in source
    for field in REQUIRED_RESULT_FIELDS:
        assert f'"{field}"' in source
    assert "RESOURCE_EDGE_INCOMPATIBLE" in source
    assert "ProviderTimeoutError" in source
    assert "PlanVersion" in source
    assert "Run" in source


def test_harness_accepts_no_cli_secret_or_key_literal() -> None:
    source = _source()
    tree = ast.parse(source)
    string_literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]

    assert "argparse" not in source
    assert "sys.argv" not in source
    assert "--api-key" not in source.lower()
    assert not any(value.startswith(("sk-", "Bearer ")) for value in string_literals)
    assert "KAIROS_ACCEPTANCE_EMAIL" in source
    assert "CredentialVault" in source


def test_harness_runs_fixture_only_after_both_real_cases_pass_first_plan() -> None:
    source = _source()

    assert "all(result.first_plan_valid for result in real_results)" in source
    assert "run_controlled_repair_fixture" in source
    assert "generation_attempt_ms" in source


def test_controlled_resource_edge_fixture_repairs_once() -> None:
    spec = importlib.util.spec_from_file_location("structured_plan_acceptance", HARNESS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    result = asyncio.run(module.run_controlled_repair_fixture())

    assert result.repair_used is True
    assert result.plan_model_2_ms >= 0
    assert result.validation_result != "INVALID"
