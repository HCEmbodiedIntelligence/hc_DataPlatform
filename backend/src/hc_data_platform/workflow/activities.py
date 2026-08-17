"""Temporal activities that adapt orchestration contracts to stage-owned ports."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import ValidationError
from temporalio import activity
from temporalio.exceptions import ApplicationError

from hc_data_platform.alignment.models import AlignedFragmentManifestV1
from hc_data_platform.alignment.ports import AlignmentPort, FragmentWriterPort
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.observability import (
    LANCE_COMMITS,
    QC_OUTCOMES,
    WORKFLOW_FAILURES,
    locator_workflow_id,
)
from hc_data_platform.lance_catalog.models import DatasetVersionRef
from hc_data_platform.lance_catalog.ports import LanceCatalogPort
from hc_data_platform.lance_catalog.service import (
    CatalogConflictError,
    SchemaIncompatibleError,
)
from hc_data_platform.preview.models import PreviewDescriptorV1, PreviewRequestV1
from hc_data_platform.publishing.models import (
    ExportFormat,
    ExportResultV1,
    PublishDatasetRequestV1,
    PublishedDatasetManifestV1,
)
from hc_data_platform.quality.models import QcReportV1
from hc_data_platform.quality.ports import QualityEvaluationPort
from hc_data_platform.verification.models import RawVerificationReportV1
from hc_data_platform.verification.ports import RawVerificationPort

from .models import (
    AlignmentActivityInput,
    AlignmentActivityOutput,
    CatalogCommitActivityInput,
    CatalogCommitActivityOutput,
    CatalogFragmentPayloadV1,
    CatalogReconciliationActivityInput,
    CatalogReconciliationActivityOutput,
    ExportActivityInput,
    ExportActivityOutput,
    PreviewActivityInput,
    PreviewActivityOutput,
    PublishActivityInput,
    PublishActivityOutput,
    PublishReconciliationActivityInput,
    PublishReconciliationActivityOutput,
    QualityActivityInput,
    QualityActivityOutput,
    VerificationActivityInput,
    VerificationActivityOutput,
)
from .names import (
    ALIGN_FRAGMENT_ACTIVITY,
    COMMIT_FRAGMENT_ACTIVITY,
    CREATE_PREVIEW_ACTIVITY,
    EVALUATE_QUALITY_ACTIVITY,
    EXPORT_DATASET_ACTIVITY,
    PUBLISH_DATASET_ACTIVITY,
    RECONCILE_CATALOG_ACTIVITY,
    RECONCILE_PUBLICATION_ACTIVITY,
    VERIFY_RAW_ACTIVITY,
)

_HEARTBEAT_INTERVAL_SECONDS = 5.0
_T = TypeVar("_T")
logger = logging.getLogger(__name__)


class FragmentWriterFactoryPort(Protocol):
    def create(self, request: AlignmentActivityInput) -> FragmentWriterPort: ...


class CatalogFragmentAdapterPort(Protocol):
    """Translate staged alignment metadata without reimplementing alignment."""

    def prepare(
        self,
        request: AlignmentActivityInput,
        manifest: AlignedFragmentManifestV1,
    ) -> CatalogFragmentPayloadV1: ...


class PreviewGenerationPort(Protocol):
    def create(self, request: PreviewRequestV1) -> PreviewDescriptorV1: ...


class DatasetPublishingPort(Protocol):
    def publish(self, request: PublishDatasetRequestV1) -> PublishedDatasetManifestV1: ...


class DatasetExportPort(Protocol):
    def export(
        self,
        manifest: PublishedDatasetManifestV1,
        *,
        format: ExportFormat,
    ) -> ExportResultV1: ...


class CatalogReconciliationPort(Protocol):
    def reconcile(
        self,
        dataset_id: str,
        *,
        project_id: str | None = None,
    ) -> tuple[DatasetVersionRef, ...]: ...


class PublicationReconciliationPort(Protocol):
    """Finish registration of already-generated immutable publication assets."""

    def reconcile(self, request: PublishDatasetRequestV1) -> PublishedDatasetManifestV1: ...


class VerificationReportPersistencePort(Protocol):
    def put_report(
        self, *, project_id: str, region_code: str, report: RawVerificationReportV1
    ) -> None: ...


class QualityReportPersistencePort(Protocol):
    def put_report(self, *, project_id: str, region_code: str, report: QcReportV1) -> None: ...


class AlignmentManifestPersistencePort(Protocol):
    def put_ready_manifest(
        self,
        *,
        project_id: str,
        region_code: str,
        manifest: AlignedFragmentManifestV1,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ActivityDependencies:
    verifier: RawVerificationPort | None = None
    quality: QualityEvaluationPort | None = None
    alignment: AlignmentPort | None = None
    fragment_writers: FragmentWriterFactoryPort | None = None
    catalog_fragments: CatalogFragmentAdapterPort | None = None
    catalog: LanceCatalogPort | None = None
    preview: PreviewGenerationPort | None = None
    publisher: DatasetPublishingPort | None = None
    exporter: DatasetExportPort | None = None
    catalog_reconciler: CatalogReconciliationPort | None = None
    publication_reconciler: PublicationReconciliationPort | None = None
    verification_reports: VerificationReportPersistencePort | None = None
    quality_reports: QualityReportPersistencePort | None = None
    alignment_manifests: AlignmentManifestPersistencePort | None = None


class WorkflowPortNotConfigured(RuntimeError):
    code = "WORKFLOW_PORT_NOT_CONFIGURED"


_dependencies = ActivityDependencies()


def configure_activity_dependencies(dependencies: ActivityDependencies) -> None:
    """Configure worker-local port adapters before polling the task queue."""

    global _dependencies
    _dependencies = dependencies


def current_activity_dependencies() -> ActivityDependencies:
    return _dependencies


def _require(value: _T | None, name: str) -> _T:
    if value is None:
        raise WorkflowPortNotConfigured(f"workflow activity port {name!r} is not configured")
    return value


@contextmanager
def _worker_scope(
    project_id: str | None, region_code: str | None, resource_id: str
) -> Iterator[None]:
    if project_id is None:
        yield
        return
    token = bind_request_context(
        RequestContext(
            project_id=project_id,
            region_code=region_code,
            subject_id="hc-data-worker",
            request_id=locator_workflow_id("activity", project_id, resource_id),
            roles=frozenset({"admin"}),
            service_identity=True,
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def _heartbeat(stage: str, state: str) -> None:
    activity.heartbeat({"stage": stage, "state": state})


async def _with_heartbeats(stage: str, operation: Callable[[], _T]) -> _T:
    """Run a synchronous port outside the event loop and heartbeat until it returns."""

    _heartbeat(stage, "started")
    task = asyncio.create_task(asyncio.to_thread(operation))
    while True:
        done, _ = await asyncio.wait({task}, timeout=_HEARTBEAT_INTERVAL_SECONDS)
        if task in done:
            result = task.result()
            _heartbeat(stage, "completed")
            return result
        _heartbeat(stage, "running")


async def _invoke(stage: str, operation: Callable[[], _T]) -> _T:
    try:
        return await _with_heartbeats(stage, operation)
    except ApplicationError:
        WORKFLOW_FAILURES.labels(
            project_id="unknown",
            resource_id=stage,
            workflow_id=f"activity:{stage}",
            workflow_kind=stage,
            error_code="APPLICATION_ERROR",
        ).inc()
        raise
    except ProblemException as exc:
        details = exc.problem
        raise ApplicationError(
            details.detail,
            details.model_dump(mode="json"),
            type=details.code,
            non_retryable=not details.retryable,
        ) from exc
    except WorkflowPortNotConfigured as exc:
        raise ApplicationError(str(exc), type=exc.code, non_retryable=True) from exc
    except (CatalogConflictError, SchemaIncompatibleError) as exc:
        raise ApplicationError(
            str(exc),
            type=exc.code,
            non_retryable=True,
        ) from exc
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        raise ApplicationError(
            str(exc),
            type="VALIDATION_FAILED",
            non_retryable=True,
        ) from exc


@activity.defn(name=VERIFY_RAW_ACTIVITY)
async def verify_raw(request: VerificationActivityInput) -> VerificationActivityOutput:
    def verify_and_persist() -> RawVerificationReportV1:
        report = _require(_dependencies.verifier, "verification.RawVerificationPort").verify(
            rollout_id=request.rollout_id,
            object_key=request.object_key,
            source_sha256=request.source_sha256,
            required_topics=set(request.required_topics),
            known_optional_topics=set(request.known_optional_topics),
        )
        if _dependencies.verification_reports is not None:
            if request.project_id is None or request.region_code is None:
                raise ValueError("verification persistence requires project_id and region_code")
            _dependencies.verification_reports.put_report(
                project_id=request.project_id,
                region_code=request.region_code,
                report=report,
            )
        return report

    with _worker_scope(request.project_id, request.region_code, request.rollout_id):
        report = await _invoke("verification", verify_and_persist)
    return VerificationActivityOutput(report=report)


@activity.defn(name=EVALUATE_QUALITY_ACTIVITY)
async def evaluate_quality(request: QualityActivityInput) -> QualityActivityOutput:
    def evaluate_and_persist() -> QcReportV1:
        report = _require(_dependencies.quality, "quality.QualityEvaluationPort").evaluate(
            request.data, request.profile
        )
        if _dependencies.quality_reports is not None:
            if request.project_id is None or request.region_code is None:
                raise ValueError("quality persistence requires project_id and region_code")
            _dependencies.quality_reports.put_report(
                project_id=request.project_id,
                region_code=request.region_code,
                report=report,
            )
        return report

    with _worker_scope(request.project_id, request.region_code, request.data.rollout_id):
        report = await _invoke("quality", evaluate_and_persist)
    project_id = request.project_id or "unknown"
    workflow_id = locator_workflow_id("ingest-rollout", project_id, request.data.rollout_id)
    QC_OUTCOMES.labels(
        project_id=project_id,
        resource_id=request.data.rollout_id,
        workflow_id=workflow_id,
        outcome=report.status.value,
        profile_id=report.profile_id,
    ).inc()
    logger.info(
        "quality activity completed",
        extra={
            "request_id": None,
            "project_id": project_id,
            "resource_id": request.data.rollout_id,
            "workflow_id": workflow_id,
            "error_code": None,
        },
    )
    return QualityActivityOutput(report=report)


@activity.defn(name=ALIGN_FRAGMENT_ACTIVITY)
async def align_fragment(request: AlignmentActivityInput) -> AlignmentActivityOutput:
    def align_and_prepare() -> AlignmentActivityOutput:
        alignment = _require(_dependencies.alignment, "alignment.AlignmentPort")
        writers = _require(_dependencies.fragment_writers, "alignment.FragmentWriterFactoryPort")
        adapter = _require(_dependencies.catalog_fragments, "workflow.CatalogFragmentAdapterPort")
        writer = writers.create(request)
        manifest = alignment.align_to_writer(request.data, request.profile, writer)
        if _dependencies.alignment_manifests is not None:
            if request.region_code is None:
                raise ValueError("alignment persistence requires region_code")
            _dependencies.alignment_manifests.put_ready_manifest(
                project_id=request.project_id,
                region_code=request.region_code,
                manifest=manifest,
            )
        fragment = adapter.prepare(request, manifest)
        return AlignmentActivityOutput(
            staged_manifest=manifest,
            catalog_fragment=fragment,
        )

    with _worker_scope(request.project_id, request.region_code, request.data.rollout_id):
        return await _invoke("alignment", align_and_prepare)


@activity.defn(name=COMMIT_FRAGMENT_ACTIVITY)
async def commit_fragment(request: CatalogCommitActivityInput) -> CatalogCommitActivityOutput:
    def commit() -> CatalogCommitActivityOutput:
        catalog = _require(_dependencies.catalog, "lance_catalog.LanceCatalogPort")
        version, ready = catalog.commit_fragment(
            request.fragment.manifest,
            request.fragment.steps,
        )
        workflow_id = locator_workflow_id(
            "dataset-writer",
            request.fragment.manifest.project_id,
            request.fragment.manifest.dataset_id,
        )
        LANCE_COMMITS.labels(
            project_id=request.fragment.manifest.project_id,
            resource_id=request.fragment.manifest.rollout_id,
            workflow_id=workflow_id,
            outcome="success",
            dataset_id=request.fragment.manifest.dataset_id,
        ).inc()
        return CatalogCommitActivityOutput(version=version, derived_ready=ready)

    manifest = request.fragment.manifest
    with _worker_scope(manifest.project_id, None, manifest.dataset_id):
        return await _invoke("lance_commit", commit)


@activity.defn(name=CREATE_PREVIEW_ACTIVITY)
async def create_preview(request: PreviewActivityInput) -> PreviewActivityOutput:
    preview_request = request.request
    with _worker_scope(
        preview_request.project_id,
        None,
        f"{preview_request.dataset_id}/{preview_request.rollout_id}",
    ):
        descriptor = await _invoke(
            "preview",
            lambda: _require(_dependencies.preview, "preview.PreviewService").create(
                preview_request
            ),
        )
    return PreviewActivityOutput(descriptor=descriptor)


@activity.defn(name=PUBLISH_DATASET_ACTIVITY)
async def publish_dataset(request: PublishActivityInput) -> PublishActivityOutput:
    publish_request = request.request
    with _worker_scope(
        publish_request.project_id,
        None,
        f"{publish_request.dataset_id}/{publish_request.dataset_version}",
    ):
        manifest = await _invoke(
            "publishing",
            lambda: _require(_dependencies.publisher, "publishing.DatasetPublisher").publish(
                publish_request
            ),
        )
    return PublishActivityOutput(manifest=manifest)


@activity.defn(name=EXPORT_DATASET_ACTIVITY)
async def export_dataset(request: ExportActivityInput) -> ExportActivityOutput:
    manifest = request.manifest
    with _worker_scope(
        manifest.project_id,
        None,
        f"{manifest.dataset_id}/{manifest.dataset_version}",
    ):
        result = await _invoke(
            "export",
            lambda: _require(_dependencies.exporter, "publishing.ExportCoordinator").export(
                manifest, format=request.format
            ),
        )
    return ExportActivityOutput(result=result)


@activity.defn(name=RECONCILE_CATALOG_ACTIVITY)
async def reconcile_catalog(
    request: CatalogReconciliationActivityInput,
) -> CatalogReconciliationActivityOutput:
    with _worker_scope(request.project_id, None, request.dataset_id):
        versions = await _invoke(
            "catalog_reconciliation",
            lambda: _require(_dependencies.catalog_reconciler, "lance_catalog.reconcile").reconcile(
                request.dataset_id, project_id=request.project_id
            ),
        )
    return CatalogReconciliationActivityOutput(
        repaired_versions=tuple(versions),
    )


@activity.defn(name=RECONCILE_PUBLICATION_ACTIVITY)
async def reconcile_publication(
    request: PublishReconciliationActivityInput,
) -> PublishReconciliationActivityOutput:
    reconciler = _dependencies.publication_reconciler
    publisher = _dependencies.publisher
    if reconciler is None and publisher is None:
        raise ApplicationError(
            "neither the publication reconciler nor DatasetPublisher is configured",
            type="WORKFLOW_PORT_NOT_CONFIGURED",
            non_retryable=True,
        )
    publish_request = request.request
    with _worker_scope(
        publish_request.project_id,
        None,
        f"{publish_request.dataset_id}/{publish_request.dataset_version}",
    ):
        manifest = await _invoke(
            "publication_reconciliation",
            lambda: (
                reconciler.reconcile(publish_request)
                if reconciler is not None
                else _require(publisher, "publishing.DatasetPublisher").publish(publish_request)
            ),
        )
    return PublishReconciliationActivityOutput(manifest=manifest)


ALL_ACTIVITIES = (
    verify_raw,
    evaluate_quality,
    align_fragment,
    commit_fragment,
    create_preview,
    publish_dataset,
    export_dataset,
    reconcile_catalog,
    reconcile_publication,
)
