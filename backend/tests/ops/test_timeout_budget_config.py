"""Static release assertions for timeout ordering and OCI provenance."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_timeout_defaults_are_strictly_ordered() -> None:
    settings = Settings(_env_file=None)

    assert settings.provider_inference_timeout_seconds == 45
    assert settings.plan_lifecycle_timeout_seconds == 105
    assert (
        settings.provider_inference_timeout_seconds < settings.plan_lifecycle_timeout_seconds < 120
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_inference_timeout_seconds", 0),
        ("provider_inference_timeout_seconds", -1),
        ("plan_lifecycle_timeout_seconds", 0),
        ("plan_lifecycle_timeout_seconds", -1),
    ],
)
def test_timeout_settings_reject_non_positive_values(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


@pytest.mark.parametrize(
    "relative",
    [
        "infra/reverse-proxy/zz-kairos-staging-tls.conf",
        "infra/reverse-proxy/zz-kairos-production-tls.conf",
    ],
)
def test_api_proxy_timeout_covers_plan_lifecycle(relative: str) -> None:
    nginx = _read(relative)

    assert "proxy_read_timeout 120s;" in nginx
    assert "proxy_send_timeout 120s;" in nginx
    assert "proxy_read_timeout 3600s;" in nginx  # SSE behavior remains unchanged.


def test_frontend_plan_request_has_no_automatic_timeout() -> None:
    source = _read("frontend/src/features/tasks/plans.api.ts")

    assert "timeoutMs: null" in source


def test_staging_and_production_explicitly_wire_backend_budgets() -> None:
    staging = _read("infra/compose/compose.staging.yml")
    production = _read("infra/compose/compose.production.yml")

    for document in (staging, production):
        assert "KAIROS_PROVIDER_INFERENCE_TIMEOUT_SECONDS" in document
        assert "KAIROS_PLAN_LIFECYCLE_TIMEOUT_SECONDS" in document
        assert "api:" in document
        assert "worker:" in document


def test_all_image_builds_emit_immutable_oci_provenance_labels() -> None:
    workflow = _read(".github/workflows/ci-build-push.yml")

    assert workflow.count("org.opencontainers.image.source=") == 3
    assert workflow.count("org.opencontainers.image.revision=${{ github.sha }}") == 3
    assert workflow.count("org.opencontainers.image.version=${{ steps.tag.outputs.tag }}") == 3
