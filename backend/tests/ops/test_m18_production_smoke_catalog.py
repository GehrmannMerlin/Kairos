from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_smoke_module():
    script = Path(__file__).parents[3] / "infra" / "scripts" / "_m18_production_smoke.py"
    spec = importlib.util.spec_from_file_location("m18_production_smoke", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_select_catalog_model_uses_a_provider_returned_id():
    smoke = _load_smoke_module()

    selected = smoke.select_catalog_model(
        {
            "status": "AVAILABLE",
            "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
        }
    )

    assert selected == "deepseek-v4-flash"


@pytest.mark.parametrize(
    "catalog",
    [
        {"status": "AUTH_FAILED", "models": []},
        {"status": "AVAILABLE", "models": []},
    ],
)
def test_select_catalog_model_fails_closed_without_a_provider_returned_model(catalog):
    smoke = _load_smoke_module()

    with pytest.raises(ValueError, match="provider-returned DeepSeek model"):
        smoke.select_catalog_model(catalog)


def test_select_catalog_model_accepts_the_current_catalog_when_preference_changes():
    smoke = _load_smoke_module()

    selected = smoke.select_catalog_model({"status": "AVAILABLE", "models": ["deepseek-v5-pro"]})

    assert selected == "deepseek-v5-pro"
