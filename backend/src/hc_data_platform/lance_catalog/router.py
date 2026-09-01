"""Logical, version-pinned Lance catalog HTTP boundary."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from .audit import (
    InMemoryLanceCatalogAuditRecorder,
    LanceCatalogAuditRecorder,
    LanceStepWindowAuditEvent,
)
from .models import DatasetVersionRef, RolloutLineage, StepWindow
from .ports import LanceCatalogPort

router = APIRouter(prefix="/api/v1", tags=["lance_catalog"])
_catalog: LanceCatalogPort | None = None
_audit_recorder: LanceCatalogAuditRecorder = InMemoryLanceCatalogAuditRecorder()
ColumnsQuery = Annotated[list[str] | None, Query(min_length=1)]


def configure_lance_catalog(catalog: LanceCatalogPort) -> None:
    global _catalog
    _catalog = catalog


def configure_lance_catalog_audit_recorder(recorder: LanceCatalogAuditRecorder) -> None:
    """Install the durable audit ledger for authenticated step-window reads."""

    global _audit_recorder
    _audit_recorder = recorder


def _required_catalog() -> LanceCatalogPort:
    if _catalog is None:
        raise problem(
            status=503,
            code="LANCE_CATALOG_UNAVAILABLE",
            title="Lance catalog unavailable",
            detail="The production Lance catalog is not configured.",
        )
    return _catalog


def _not_found() -> Exception:
    return problem(
        status=404,
        code="LANCE_RESOURCE_NOT_FOUND",
        title="Lance resource not found",
        detail="The requested dataset, version, rollout, or step window does not exist.",
    )


@router.get(
    "/projects/{project_id}/datasets/{dataset_id}/lance-versions",
    operation_id="listLanceCatalogVersions",
    response_model=list[DatasetVersionRef],
)
def list_dataset_versions(
    project_id: str, dataset_id: str, auth: VerifiedAuth
) -> tuple[DatasetVersionRef, ...]:
    authorize_scope(auth, project_id, "dataset_version.read")
    try:
        return _required_catalog().list_versions(dataset_id, project_id=project_id)
    except KeyError as exc:
        raise _not_found() from exc


@router.get(
    "/projects/{project_id}/datasets/{dataset_id}/lance-versions/{version}",
    operation_id="getLanceCatalogVersionSnapshot",
    response_model=DatasetVersionRef,
)
def get_dataset_version(
    project_id: str, dataset_id: str, version: int, auth: VerifiedAuth
) -> DatasetVersionRef:
    authorize_scope(auth, project_id, "dataset_version.read")
    try:
        return _required_catalog().version_snapshot(
            dataset_id, version=version, project_id=project_id
        )
    except KeyError as exc:
        raise _not_found() from exc


@router.get(
    "/projects/{project_id}/datasets/{dataset_id}/rollouts/{rollout_id}/steps",
    response_model=StepWindow,
)
def read_step_window(
    project_id: str,
    dataset_id: str,
    rollout_id: str,
    auth: VerifiedAuth,
    start_step: int = Query(ge=0),
    end_step: int = Query(ge=0),
    version: int | None = Query(default=None, ge=1),
    columns: ColumnsQuery = None,
) -> StepWindow:
    authorize_scope(auth, project_id, "dataset_version.read")
    if end_step < start_step:
        raise problem(
            status=422,
            code="STEP_WINDOW_INVALID",
            title="Step window invalid",
            detail="end_step must be greater than or equal to start_step.",
        )
    try:
        result = _required_catalog().read_steps(
            dataset_id,
            rollout_id,
            start_step,
            end_step,
            version=version,
            project_id=project_id,
            columns=columns,
        )
        _record_step_window_read(result, actor_id=auth.subject_id)
        return result
    except KeyError as exc:
        raise _not_found() from exc


def _record_step_window_read(result: StepWindow, *, actor_id: str) -> None:
    context = current_request_context()
    _audit_recorder.append_step_window_read(
        LanceStepWindowAuditEvent(
            project_id=result.project_id,
            region_code=context.region_code,
            actor_id=actor_id,
            request_id=context.request_id,
            dataset_id=result.dataset_id,
            rollout_id=result.rollout_id,
            dataset_version=result.dataset_version,
            start_step=result.start_step,
            end_step=result.end_step,
            returned_step_count=len(result.steps),
        )
    )


@router.get(
    "/projects/{project_id}/datasets/{dataset_id}/rollouts/{rollout_id}/lineage",
    response_model=RolloutLineage,
)
def get_rollout_lineage(
    project_id: str,
    dataset_id: str,
    rollout_id: str,
    auth: VerifiedAuth,
    version: int | None = Query(default=None, ge=1),
) -> RolloutLineage:
    authorize_scope(auth, project_id, "dataset_version.read")
    try:
        return _required_catalog().lineage(
            dataset_id, rollout_id, version=version, project_id=project_id
        )
    except KeyError as exc:
        raise _not_found() from exc


@router.post(
    "/projects/{project_id}/datasets/{dataset_id}/reconciliation",
    response_model=list[DatasetVersionRef],
)
def reconcile_lance_catalog(
    project_id: str, dataset_id: str, auth: VerifiedAuth
) -> tuple[DatasetVersionRef, ...]:
    authorize_scope(auth, project_id, "dataset_version.publish")
    try:
        return _required_catalog().reconcile(dataset_id, project_id=project_id)
    except KeyError as exc:
        raise _not_found() from exc
