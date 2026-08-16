"""Production executor for the registered ``generate_artifact`` plan node."""

from __future__ import annotations

from app.activities.execution_seam import ExecuteUnitResult, ExecutionUnit
from app.artifacts.contracts import ExportRequest, ExportScope, ExportType
from app.artifacts.service import ArtifactService
from app.domain.models import Run
from app.domain.repository import TaskRepository
from app.infra.deps import get_object_storage, get_session_factory
from app.infra.object_storage import StorageOperationError
from app.plan.executors import register_node_executor
from app.plan.nodes import NodeType


async def generate_artifact(unit: ExecutionUnit) -> ExecuteUnitResult:
    """Export the persisted Run's task without trusting plan-provided ownership."""
    session = get_session_factory()()
    try:
        run = session.get(Run, unit.run_id)
        if run is None:
            return ExecuteUnitResult(
                unit_index=unit.index,
                status="FAILED",
                error_code="RUN_NOT_FOUND",
                committed_refs={},
            )
        TaskRepository(session).get_owned(run.user_id, run.task_id)
        try:
            ref = await ArtifactService(session, get_object_storage()).export(
                user_id=run.user_id,
                task_id=run.task_id,
                request=ExportRequest(export_type=ExportType.FORMAL, scope=ExportScope.ALL),
            )
        except StorageOperationError:
            return ExecuteUnitResult(
                unit_index=unit.index,
                status="FAILED",
                error_code="STORAGE_ERROR",
                committed_refs={},
            )
        return ExecuteUnitResult(
            unit_index=unit.index,
            status="OK",
            committed_refs={
                "task_id": run.task_id,
                "run_id": run.id,
                "node_id": unit.node_id,
                "node_type": unit.node_type,
                "artifact_id": ref.artifact_id,
                "row_count": ref.row_count,
                "content_hash": ref.content_hash,
            },
        )
    finally:
        session.close()


def install_artifact_executor() -> None:
    """Register the real artifact executor during every production worker startup."""
    register_node_executor(NodeType.GENERATE_ARTIFACT, generate_artifact)
