"""Production executor capability manifest contracts."""

from __future__ import annotations

from app.activities.execution_seam import ExecuteUnitResult, ExecutionUnit
from app.crawling.executors import install_fetch_executors
from app.discovery.executors import install_discovery_executors
from app.extraction.executors import install_extraction_executors
from app.plan.capabilities import assert_runtime_executor_manifest, supported_node_types
from app.plan.executors import register_node_executor
from app.plan.nodes import NodeRegistry, NodeType
from app.plan.staging_fixture import install_staging_fixture
from app.validation.executors import install_validation_executors


def test_manifest_covers_every_generated_node_type() -> None:
    generated = {definition.node_type for definition in NodeRegistry().all()}
    assert generated == supported_node_types()


def test_fixture_registration_does_not_change_production_manifest() -> None:
    before = supported_node_types()
    install_staging_fixture()
    assert supported_node_types() == before


def install_all_real_executors_for_manifest_test() -> None:
    install_discovery_executors()
    install_fetch_executors()
    install_extraction_executors()
    install_validation_executors()

    async def artifact_executor(unit: ExecutionUnit) -> ExecuteUnitResult:
        return ExecuteUnitResult(unit_index=unit.index, committed_refs={}, status="OK")

    register_node_executor(NodeType.GENERATE_ARTIFACT, artifact_executor)


def test_runtime_registry_matches_manifest_after_real_installers() -> None:
    install_all_real_executors_for_manifest_test()
    assert_runtime_executor_manifest()
