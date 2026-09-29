from __future__ import annotations

import asyncio
import io
import tempfile
import threading
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from hc_data_platform.aligned_media.models import (
    AlignedMediaArtifactStatus,
    AlignedMediaArtifactV1,
    AlignedMediaGenerationRequestV1,
    AlignedMediaScopeV1,
    AlignedMediaTimelineV1,
)
from hc_data_platform.aligned_media.service import aligned_media_artifact_key
from hc_data_platform.alignment.engine import AlignmentEngine
from hc_data_platform.alignment.models import (
    AlignedFragmentManifestV1 as StagedManifestV1,
)
from hc_data_platform.alignment.models import (
    AlignmentInputV1,
    AlignmentProfileV1,
    ModalityKind,
    ModalityStreamV1,
    TimedSampleV1,
)
from hc_data_platform.alignment.ports import FakeFragmentWriter
from hc_data_platform.annotation.automation import (
    AutomaticAnnotationTaskService,
    InMemoryAutomaticAnnotationRepository,
)
from hc_data_platform.annotation.models import (
    TagNodeDefinition,
    TagSchemaDocument,
    TagSchemaStatus,
    TagSchemaTarget,
    TagSchemaVersion,
)
from hc_data_platform.core.context import current_request_context
from hc_data_platform.dataset_registry.models import DatasetIngestViewerTarget
from hc_data_platform.ingest.manifest import preflight_manifest
from hc_data_platform.ingest.models import (
    ManifestCameraV1,
    ManifestFileV1,
    ManifestPreflightResultV1,
    ManifestTopicV1,
    RolloutManifestV1,
)
from hc_data_platform.lance_catalog.models import (
    AlignedFragmentManifestV1 as CatalogManifestV1,
)
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot, StepRecord
from hc_data_platform.lance_catalog.ports import LanceCatalogPort
from hc_data_platform.lance_catalog.service import (
    CatalogIndexPendingError,
    InMemoryLanceCatalog,
    compute_fragment_hash,
)
from hc_data_platform.publishing.models import (
    ExportFormat,
    ExportResultV1,
    PublishDatasetRequestV1,
    PublishedDatasetManifestV1,
)
from hc_data_platform.quality.models import (
    QcReportV1,
    QualityInputV1,
    QualityProfileV1,
    QualityStatus,
)
from hc_data_platform.verification.models import (
    RawVerificationReportV1,
    VerificationStatus,
)
from hc_data_platform.workflow.activities import (
    ALL_ACTIVITIES,
    ActivityDependencies,
    CatalogFragmentAdapterPort,
    FragmentWriterFactoryPort,
    configure_activity_dependencies,
)
from hc_data_platform.workflow.models import (
    AlignmentActivityInput,
    CatalogCommitActivityInput,
    CatalogFragmentPayloadV1,
    CatalogReconciliationWorkflowInput,
    DatasetWriterWorkflowInput,
    ExportWorkflowInput,
    FrameSelectionManifestRefV1,
    IngestProjectionSourceV1,
    IngestRolloutWorkflowInput,
    JobRecord,
    JobStatus,
    ManifestActivityInput,
    ProjectionMaterializationV1,
    PublishDatasetWorkflowInput,
    PublishReconciliationWorkflowInput,
    QualityActivityInput,
    VerificationActivityInput,
    WorkflowJobPersistenceActivityInput,
    workflow_id,
)
from hc_data_platform.workflow.names import INGEST_ROLLOUT_WORKFLOW
from hc_data_platform.workflow.projection_store import LocalProjectionArtifactStore
from hc_data_platform.workflow.service import TemporalWorkflowLauncher
from hc_data_platform.workflow.temporal_workflows import (
    ALL_WORKFLOWS,
    CatalogReconciliationWorkflow,
    DatasetWriterWorkflow,
    ExportWorkflow,
    IngestRolloutWorkflow,
    PublishDatasetWorkflow,
    PublishReconciliationWorkflow,
)

SHA = "a" * 64
FOUR_CAMERAS = (
    "/camera/front/image",
    "/camera/rear/image",
    "/camera/left/image",
    "/camera/right/image",
)


def _verification_report(
    status: VerificationStatus = VerificationStatus.VERIFIED,
    *,
    rollout_id: str = "r1",
) -> RawVerificationReportV1:
    return RawVerificationReportV1.build(
        rollout_id=rollout_id,
        object_key="raw/r1.mcap",
        source_sha256=SHA,
        object_size=64,
        profile="",
        library="test",
        record_count=0,
        message_count=0,
        schemas=0,
        channels=0,
        chunks=0,
        schema_inventory=(),
        channel_inventory=(),
        topics=(),
        findings=(),
        status=status,
    )


def _quality_profile() -> QualityProfileV1:
    return QualityProfileV1(profile_id="qc-v1", required_topics=frozenset())


def _quality_report(
    status: QualityStatus,
    *,
    rollout_id: str = "r1",
    profile: QualityProfileV1 | None = None,
) -> QcReportV1:
    profile = profile or _quality_profile()
    return QcReportV1.build(
        rollout_id=rollout_id,
        source_sha256=SHA,
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        profile_sha256=profile.content_sha256(),
        engine_version=profile.engine_version,
        start_ns=0,
        end_ns=1,
        status=status,
        topic_metrics=(),
        findings=(),
    )


def _schema() -> DatasetSchemaSnapshot:
    return DatasetSchemaSnapshot.create(
        project_id="p1",
        dataset_id="d1",
        schema_snapshot_id="schema-1",
        frequency_hz=30,
        fields={"x": "float64"},
    )


def _manifest_preflight(
    rollout_id: str = "r1",
    data_package_id: str | None = None,
    camera_topics: tuple[str, ...] = ("/camera/front/image",),
) -> ManifestPreflightResultV1:
    package_id = data_package_id or f"package-{rollout_id}"
    return preflight_manifest(
        RolloutManifestV1(
            project_id="p1",
            task_id="task-1",
            collection_job_id="job-1",
            rollout_id=rollout_id,
            collection_session_id="session-1",
            recording_request_id=f"request-{rollout_id}",
            data_package_id=package_id,
            sequence_no=1 if rollout_id == "r1" else 2,
            robot_id="robot-1",
            start_time=datetime(2026, 8, 14, tzinfo=timezone.utc),
            end_time=datetime(2026, 8, 14, 0, 0, 1, tzinfo=timezone.utc),
            cameras=[
                ManifestCameraV1(
                    camera_id=topic.removeprefix("/camera/").removesuffix("/image"),
                    topic=topic,
                    encoding="jpeg",
                )
                for topic in camera_topics
            ],
            topics=[
                ManifestTopicV1(
                    name=topic,
                    message_encoding="cdr",
                    schema_name="hc.camera.JpegEnvelope",
                )
                for topic in camera_topics
            ],
            expected_topics=["/camera/required"],
            actual_topics=list(camera_topics),
            files=[
                ManifestFileV1(
                    path="recording.mcap",
                    size=64,
                    sha256=SHA,
                    crc64=0,
                )
            ],
            file_size=64,
            sha256=SHA,
            crc64=0,
            compression="none",
            recorder_version="test-1.0",
        )
    )


def _manifest_input(
    rollout_id: str = "r1",
    data_package_id: str | None = None,
    camera_topics: tuple[str, ...] = ("/camera/front/image",),
) -> ManifestActivityInput:
    preflight = _manifest_preflight(rollout_id, data_package_id, camera_topics)
    return ManifestActivityInput(
        project_id="p1",
        region_code="cn",
        rollout_id=rollout_id,
        data_package_id=preflight.identifiers.data_package_id,
        manifest_key=f"raw/{rollout_id}/rollout_manifest.json",
        manifest_fingerprint=preflight.manifest_fingerprint,
        source_sha256=SHA,
    )


def _ingest_input(
    camera_topics: tuple[str, ...] = ("/camera/front/image",),
) -> IngestRolloutWorkflowInput:
    preflight = _manifest_preflight(camera_topics=camera_topics)
    source = IngestProjectionSourceV1(
        organization_id="organization-a",
        project_id="p1",
        region_code="cn",
        session_id="session-1",
        rollout_id="r1",
        data_package_id=preflight.identifiers.data_package_id,
        object_key="raw/r1.mcap",
        manifest_key="raw/r1/rollout_manifest.json",
        manifest_fingerprint=preflight.manifest_fingerprint,
        source_sha256=SHA,
    )
    return IngestRolloutWorkflowInput(
        organization_id="organization-a",
        project_id="p1",
        region_code="cn",
        dataset_id="d1",
        rollout_id="r1",
        media_task_queue="workflow-tests",
        manifest=_manifest_input(camera_topics=camera_topics),
        verification=VerificationActivityInput(
            organization_id="organization-a",
            project_id="p1",
            region_code="cn",
            rollout_id="r1",
            object_key="raw/r1.mcap",
            source_sha256=SHA,
            required_topics=frozenset(),
        ),
        quality=QualityActivityInput(
            organization_id="organization-a",
            project_id="p1",
            region_code="cn",
            source=source,
            profile=_quality_profile(),
        ),
        alignment=AlignmentActivityInput(
            organization_id="organization-a",
            project_id="p1",
            region_code="cn",
            dataset_id="d1",
            schema_snapshot_id="schema-1",
            source=source,
            profile=AlignmentProfileV1(
                profile_id="align-v1",
                frequency_hz=30,
                required_modalities=frozenset({"x"}),
            ),
        ),
    )


def test_ingest_workflow_rejects_missing_or_mismatched_persistence_scope() -> None:
    payload = _ingest_input().model_dump(mode="json")
    payload["verification"]["project_id"] = None
    with pytest.raises(ValidationError, match="verification.project_id"):
        IngestRolloutWorkflowInput.model_validate(payload)

    payload = _ingest_input().model_dump(mode="json")
    payload["quality"]["region_code"] = "eu"
    with pytest.raises(ValidationError, match="source scope"):
        IngestRolloutWorkflowInput.model_validate(payload)


class StaticVerifier:
    def __init__(self, status: VerificationStatus = VerificationStatus.VERIFIED) -> None:
        self.status = status
        self.calls = 0
        self.last_required_topics: set[str] | None = None

    def verify(
        self,
        *,
        rollout_id: str,
        object_key: str,
        source_sha256: str,
        required_topics: set[str],
        known_optional_topics: set[str] | None = None,
    ) -> RawVerificationReportV1:
        del object_key, source_sha256, known_optional_topics
        self.calls += 1
        self.last_required_topics = required_topics
        return _verification_report(self.status, rollout_id=rollout_id)

    def verify_stream(self, *, stream: object, **kwargs: object) -> RawVerificationReportV1:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
        return self.verify(**kwargs)  # type: ignore[arg-type]


class StaticManifestParser:
    def __init__(self, camera_topics: tuple[str, ...] = ("/camera/front/image",)) -> None:
        self.camera_topics = camera_topics

    def parse(self, manifest_key: str) -> ManifestPreflightResultV1:
        marker = "/rollout_manifest.json"
        if not manifest_key.startswith("raw/") or not manifest_key.endswith(marker):
            raise ValueError("unexpected manifest key")
        rollout_id = manifest_key.removeprefix("raw/").removesuffix(marker)
        return _manifest_preflight(rollout_id, camera_topics=self.camera_topics)


class StaticQuality:
    def __init__(self, status: QualityStatus, *, failures: int = 0) -> None:
        self.status = status
        self.failures = failures
        self.calls = 0

    def evaluate(self, data: QualityInputV1, profile: QualityProfileV1) -> QcReportV1:
        self.calls += 1
        if self.calls <= self.failures:
            raise OSError("temporary network failure")
        return _quality_report(self.status, rollout_id=data.rollout_id, profile=profile)

    def evaluate_stream(
        self,
        data: QualityInputV1,
        observations: object,
        profile: QualityProfileV1,
    ) -> QcReportV1:
        del observations
        return self.evaluate(data, profile)


def _alignment_data(rollout_id: str = "r1", attempt_id: str | None = None) -> AlignmentInputV1:
    return AlignmentInputV1(
        rollout_id=rollout_id,
        source_sha256=SHA,
        attempt_id=attempt_id or f"attempt-{rollout_id}-v1",
        start_ns=0,
        end_ns=1,
        streams={
            "x": ModalityStreamV1(
                kind=ModalityKind.CONTINUOUS,
                samples=(),
            )
        },
    )


class StaticProjection:
    class Session:
        def __init__(self, source: IngestProjectionSourceV1) -> None:
            self.source = source
            self.quality_data = QualityInputV1(
                rollout_id=source.rollout_id,
                source_sha256=source.source_sha256,
                start_ns=0,
                end_ns=1,
                topic_timestamps_ns={},
            )
            self.alignment_data = _alignment_data(source.rollout_id)

        def __enter__(self) -> StaticProjection.Session:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def open_reader(self) -> io.BytesIO:
            return io.BytesIO(b"localized-raw")

        def quality_observations(self) -> tuple[object, ...]:
            return ()

        def alignment_samples(self) -> tuple[tuple[str, TimedSampleV1], ...]:
            return (("x", TimedSampleV1(timestamp_ns=0, value=1.0)),)

        def frame_selection(self) -> FrameSelectionManifestRefV1:
            return FrameSelectionManifestRefV1(
                object_key="derived/frame-selections/test.json",
                content_sha256="c" * 64,
                size_bytes=1,
                source_frame_count=1,
                selected_group_count=1,
                camera_set=("/camera/front/image",),
            )

    def open_local_session(self, source: IngestProjectionSourceV1) -> StaticProjection.Session:
        return self.Session(source)

    def project_alignment_metadata(self, source: IngestProjectionSourceV1) -> AlignmentInputV1:
        return _alignment_data(source.rollout_id)

    def materialize(self, source: IngestProjectionSourceV1) -> IngestProjectionSourceV1:
        now = datetime(2026, 8, 14, tzinfo=timezone.utc)
        return source.model_copy(
            update={
                "materialization": ProjectionMaterializationV1(
                    object_key="staging/projections/test.arrow",
                    content_sha256="b" * 64,
                    size_bytes=1,
                    created_at=now,
                    expires_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
                    frame_selection=FrameSelectionManifestRefV1(
                        object_key="staging/projections/selection.json",
                        content_sha256="c" * 64,
                        size_bytes=1,
                        source_frame_count=1,
                        selected_group_count=1,
                        camera_set=("/camera/front/image",),
                    ),
                )
            }
        )

    def cleanup(self, source: IngestProjectionSourceV1) -> None:
        del source

    def project_quality_stream(
        self, source: IngestProjectionSourceV1
    ) -> tuple[QualityInputV1, tuple[object, ...]]:
        return (
            QualityInputV1(
                rollout_id=source.rollout_id,
                source_sha256=source.source_sha256,
                start_ns=0,
                end_ns=1,
                topic_timestamps_ns={},
            ),
            (),
        )

    def project_alignment_stream(
        self, source: IngestProjectionSourceV1
    ) -> tuple[AlignmentInputV1, tuple[tuple[str, TimedSampleV1], ...]]:
        return _alignment_data(source.rollout_id), (
            ("x", TimedSampleV1(timestamp_ns=0, value=1.0)),
        )


class FileFragmentWriter(FakeFragmentWriter):
    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root

    def commit(self, *, row_count: int, content_sha256: str, schema_sha256: str) -> str:
        super().commit(
            row_count=row_count,
            content_sha256=content_sha256,
            schema_sha256=schema_sha256,
        )
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"{content_sha256}.arrow"
        path.write_bytes(b"bounded-test-alignment")
        return path.resolve().as_uri()


class Writers(FragmentWriterFactoryPort):
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="workflow-alignment-"))
        self.by_attempt: dict[str, FileFragmentWriter] = {}
        self.calls = 0

    def create(self, request: AlignmentActivityInput) -> FileFragmentWriter:
        self.calls += 1
        if request.data is None:
            raise ValueError("test writer requires projected alignment data")
        writer = FileFragmentWriter(self.root)
        self.by_attempt[request.data.attempt_id] = writer
        return writer


class CatalogAdapter(CatalogFragmentAdapterPort):
    def __init__(self, writers: Writers, schema: DatasetSchemaSnapshot) -> None:
        self.writers = writers
        self.schema = schema

    def prepare(
        self,
        request: AlignmentActivityInput,
        manifest: StagedManifestV1,
    ) -> CatalogFragmentPayloadV1:
        rows = self.writers.by_attempt[request.data.attempt_id].rows
        steps = tuple(
            StepRecord(
                rollout_id=row.rollout_id,
                step_index=row.step_index,
                timestamp_ns=row.timestamp_ns,
                modalities={name: value.value for name, value in row.modalities.items()},
                source_timestamps_ns={
                    name: value.source_timestamps_ns for name, value in row.modalities.items()
                },
                time_error_ns={name: value.time_error_ns for name, value in row.modalities.items()},
                valid={name: value.valid for name, value in row.modalities.items()},
                repeated={name: value.repeated for name, value in row.modalities.items()},
                sample_valid=row.sample_valid,
            )
            for row in rows
        )
        catalog_manifest = CatalogManifestV1(
            project_id=request.project_id,
            dataset_id=request.dataset_id,
            schema_snapshot_id=request.schema_snapshot_id,
            schema_fingerprint=self.schema.fingerprint,
            frequency_hz=manifest.frequency_hz,
            rollout_id=manifest.rollout_id,
            source_sha256=manifest.source_sha256,
            converter_version=manifest.converter_version,
            attempt_id=manifest.attempt_id,
            fragment_uri=manifest.staging_uri,
            step_count=len(steps),
            content_hash=compute_fragment_hash(steps),
        )
        return CatalogFragmentPayloadV1(manifest=catalog_manifest, steps=steps)

    def prepare_streaming(
        self,
        request: AlignmentActivityInput,
        manifest: StagedManifestV1,
        media_artifacts: Sequence[AlignedMediaArtifactV1] = (),
    ) -> tuple[CatalogManifestV1, Sequence[StepRecord]]:
        del media_artifacts
        projected = request.model_copy(
            update={
                "data": _alignment_data(manifest.rollout_id, manifest.attempt_id),
                "source": None,
            }
        )
        payload = self.prepare(projected, manifest)
        return payload.manifest, payload.steps


class CrashAfterCommitCatalog:
    def __init__(self, catalog: InMemoryLanceCatalog) -> None:
        self.catalog = catalog
        self.calls = 0

    def commit_fragment(
        self,
        manifest: CatalogManifestV1,
        steps: Sequence[StepRecord],
    ) -> tuple[object, object]:
        self.calls += 1
        result = self.catalog.commit_fragment(manifest, steps)
        if self.calls == 1:
            raise OSError("worker died after the storage commit")
        return result


class ScopedCatalogReconciler:
    def __init__(self, catalog: InMemoryLanceCatalog) -> None:
        self.catalog = catalog

    def reconcile(self, dataset_id: str, *, project_id: str | None = None) -> tuple[object, ...]:
        assert project_id is not None
        assert current_request_context().project_id == project_id
        return self.catalog.reconcile(dataset_id, project_id=project_id)


class BlockingVerifier(StaticVerifier):
    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        super().__init__()
        self.started = started
        self.release = release

    def verify(
        self,
        *,
        rollout_id: str,
        object_key: str,
        source_sha256: str,
        required_topics: set[str],
        known_optional_topics: set[str] | None = None,
    ) -> RawVerificationReportV1:
        self.started.set()
        self.release.wait(timeout=10)
        return super().verify(
            rollout_id=rollout_id,
            object_key=object_key,
            source_sha256=source_sha256,
            required_topics=required_topics,
            known_optional_topics=known_optional_topics,
        )


class PendingPublicationRecovery:
    """Models immutable assets written before the manifest transaction failed."""

    def __init__(self) -> None:
        self.temporary_assets = {
            "published/p1/d1/v1/annotations.lance",
            "published/p1/d1/v1/training-manifest.json",
        }
        self.calls = 0

    def publish(self, request: PublishDatasetRequestV1) -> PublishedDatasetManifestV1:
        assert current_request_context().project_id == request.project_id
        self.calls += 1
        return PublishedDatasetManifestV1(
            project_id=request.project_id,
            dataset_id=request.dataset_id,
            dataset_version=request.dataset_version,
            base_lance_version=request.base_lance_version,
            created_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
            content_hash="d" * 64,
            annotations_uri="published/p1/d1/v1/annotations.lance",
            annotations_content_sha256="e" * 64,
            training_manifest_uri="published/p1/d1/v1/training-manifest.json",
            training_manifest_content_sha256="f" * 64,
            rollouts=(),
        )


class StaticAlignedMedia:
    def __init__(self) -> None:
        self.requests: list[AlignedMediaGenerationRequestV1] = []
        self.committed_artifact_ids: tuple[str, ...] = ()
        self.abandoned_artifact_ids: tuple[str, ...] = ()
        self.abandon_completed = False

    def generate(
        self,
        scope: AlignedMediaScopeV1,
        request: AlignedMediaGenerationRequestV1,
        *,
        cancelled: object = None,
    ) -> AlignedMediaArtifactV1:
        del cancelled
        assert current_request_context().project_id == request.project_id
        assert scope.project_id == request.project_id
        self.requests.append(request)
        now = datetime(2026, 8, 14, tzinfo=timezone.utc)
        return AlignedMediaArtifactV1(
            artifact_id=f"media-{request.camera_id}",
            artifact_key=aligned_media_artifact_key(request),
            scope=scope,
            dataset_id=request.dataset_id,
            rollout_id=request.rollout_id,
            dataset_version=request.expected_dataset_version,
            camera_id=request.camera_id,
            profile_id=request.profile_id,
            profile_version="1",
            alignment_version=request.alignment.alignment_version,
            source_sha256=request.source_sha256,
            status=AlignedMediaArtifactStatus.READY,
            object_prefix="aligned-media/p1/test",
            media_object_key="aligned-media/p1/test/media.mp4",
            frame_count=request.alignment.row_count,
            duration_seconds=request.alignment.row_count / 30,
            content_sha256="a" * 64,
            timeline=AlignedMediaTimelineV1(
                frame_count=request.alignment.row_count,
                start_timestamp_ns=0,
            ),
            width=1280,
            height=720,
            created_at=now,
            ready_at=now,
        )

    def begin_dataset_commit(self, **kwargs: object) -> None:
        assert kwargs["artifact_ids"]

    def mark_dataset_committed(self, **kwargs: object) -> None:
        self.committed_artifact_ids = tuple(cast(Sequence[str], kwargs["artifact_ids"]))

    def begin_abandon_uncommitted(self, **kwargs: object) -> tuple[object, ...]:
        self.abandoned_artifact_ids = tuple(cast(Sequence[str], kwargs["artifact_ids"]))
        return ()

    def complete_abandon_uncommitted(self, **kwargs: object) -> None:
        del kwargs
        self.abandon_completed = True

    def delete_exact(self, objects: Sequence[object]) -> int:
        return len(objects)


class FailingAlignedMedia(StaticAlignedMedia):
    def __init__(self, failing_camera_id: str) -> None:
        super().__init__()
        self.failing_camera_id = failing_camera_id
        self.failure_calls = 0

    def generate(
        self,
        scope: AlignedMediaScopeV1,
        request: AlignedMediaGenerationRequestV1,
        *,
        cancelled: object = None,
    ) -> AlignedMediaArtifactV1:
        if request.camera_id == self.failing_camera_id:
            self.failure_calls += 1
            raise RuntimeError("injected camera encoder failure")
        return super().generate(scope, request, cancelled=cancelled)


class CommitMarkerFailsUntilCleanup(StaticAlignedMedia):
    def __init__(self) -> None:
        super().__init__()
        self.commit_marker_calls = 0

    def mark_dataset_committed(self, **kwargs: object) -> None:
        self.commit_marker_calls += 1
        if self.commit_marker_calls <= 8:
            raise ConnectionError("injected PostgreSQL commit marker failure")
        super().mark_dataset_committed(**kwargs)


class StaticDatasetIngestProjection:
    def project(self, **kwargs: object) -> DatasetIngestViewerTarget:
        version = cast(object, kwargs["version"])
        version_number = int(version.version)
        return DatasetIngestViewerTarget(
            dataset_id="dataset_d1",
            version_id=f"version_lance_{version_number}",
            episode_id="episode_r1",
            revision_id="revision_r1",
        )


class StaticExporter:
    def preflight(
        self,
        manifest: PublishedDatasetManifestV1,
        *,
        format: ExportFormat,
    ) -> int:
        assert current_request_context().project_id == manifest.project_id
        assert format is ExportFormat.LANCE_SNAPSHOT
        return 0

    def export(
        self,
        manifest: PublishedDatasetManifestV1,
        *,
        format: ExportFormat,
        attempt_id: str,
    ) -> ExportResultV1:
        assert current_request_context().project_id == manifest.project_id
        return ExportResultV1(
            format=format,
            project_id=manifest.project_id,
            dataset_id=manifest.dataset_id,
            dataset_version=manifest.dataset_version,
            manifest_content_hash=manifest.content_hash,
            attempt_id=attempt_id,
            artifact_uri="exports/d1/v1/artifact.json",
            download_uri="https://download.invalid/artifact.json",
            artifact_content_hash="1" * 64,
            row_count=0,
            media_type="application/json",
        )

    def verify_artifact(self, result: ExportResultV1) -> None:
        assert current_request_context().project_id == result.project_id
        assert result.artifact_content_hash == "1" * 64


class WorkflowJobRecorder:
    def __init__(self) -> None:
        self.jobs: list[JobRecord] = []

    def put_job(self, request: WorkflowJobPersistenceActivityInput) -> None:
        assert request.organization_id == "organization-a"
        assert current_request_context().organization_id == request.organization_id
        assert current_request_context().project_id == request.job.project_id
        self.jobs.append(request.job)


def _dependencies(
    *,
    verifier: StaticVerifier,
    quality: StaticQuality,
    catalog: InMemoryLanceCatalog,
    aligned_media: StaticAlignedMedia | None = None,
    camera_topics: tuple[str, ...] = ("/camera/front/image",),
) -> tuple[ActivityDependencies, Writers]:
    writers = Writers()
    media = aligned_media or StaticAlignedMedia()
    workflow_jobs = WorkflowJobRecorder()
    annotations = InMemoryAutomaticAnnotationRepository()
    draft = TagSchemaVersion(
        schema_id="tag-schema-1",
        project_id="p1",
        name="test schema",
        version=1,
        document=TagSchemaDocument(
            nodes=(
                TagNodeDefinition(
                    tag_id="event",
                    code="event",
                    display_name="Event",
                ),
            )
        ),
        compatible_targets=(
            TagSchemaTarget(
                region_code="cn",
                dataset_id="d1",
                dataset_schema_snapshot_id="schema-1",
            ),
        ),
        content_hash="e" * 64,
        created_by="schema-author",
    )
    annotations.create_tag_schema_version(draft)
    annotations.publish_tag_schema_version(
        draft,
        draft.model_copy(
            update={
                "status": TagSchemaStatus.PUBLISHED,
                "published_by": "schema-publisher",
                "published_at": datetime(2026, 8, 14, tzinfo=timezone.utc),
            }
        ),
    )
    return (
        ActivityDependencies(
            manifest_parser=StaticManifestParser(camera_topics),
            verifier=verifier,
            quality=quality,
            alignment=AlignmentEngine(),
            ingest_projection=StaticProjection(),
            dataset_ingest_projection=StaticDatasetIngestProjection(),
            fragment_writers=writers,
            alignment_staging=LocalProjectionArtifactStore(
                Path(tempfile.mkdtemp(prefix="workflow-staging-"))
            ),
            catalog_fragments=CatalogAdapter(writers, _schema()),
            catalog=catalog,
            catalog_reconciler=catalog,
            aligned_media=media,
            aligned_media_repository=media,  # type: ignore[arg-type]
            aligned_media_store=media,  # type: ignore[arg-type]
            annotation_tasks=AutomaticAnnotationTaskService(annotations),
            workflow_jobs=workflow_jobs,
        ),
        writers,
    )


@pytest.mark.asyncio
async def test_temporal_ingest_gates_retries_duplicate_start_and_replays() -> None:
    environment = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    )
    try:
        catalog = InMemoryLanceCatalog()
        catalog.register_schema(_schema())
        verifier = StaticVerifier()
        quality = StaticQuality(QualityStatus.PASS, failures=2)
        aligned_media = StaticAlignedMedia()
        dependencies, writers = _dependencies(
            verifier=verifier,
            quality=quality,
            catalog=catalog,
            aligned_media=aligned_media,
        )
        configure_activity_dependencies(dependencies)

        async with Worker(
            environment.client,
            task_queue="workflow-tests",
            workflows=list(ALL_WORKFLOWS),
            activities=list(ALL_ACTIVITIES),
        ):
            identifier = workflow_id("ingest-rollout", "p1", "r1")
            handle = await environment.client.start_workflow(
                IngestRolloutWorkflow.run,
                _ingest_input(),
                id=identifier,
                task_queue="workflow-tests",
            )
            result = await handle.result()
            assert result.status is JobStatus.SUCCEEDED
            assert result.result is not None
            assert result.result["training_eligible"] is True
            assert quality.calls == 3
            assert verifier.last_required_topics == {"/camera/required"}
            assert writers.calls == 1
            assert len(catalog.list_versions("d1", project_id="p1")) == 1
            assert result.result["aligned_media_count"] == 1
            assert len(aligned_media.requests) == 1
            assert aligned_media.requests[0].camera_id == "/camera/front/image"
            assert aligned_media.requests[0].expected_dataset_version == 1

            history = await handle.fetch_history()
            await Replayer(
                workflows=list(ALL_WORKFLOWS),
                data_converter=pydantic_data_converter,
            ).replay_workflow(history)

            risk_verifier = StaticVerifier()
            risk_quality = StaticQuality(QualityStatus.RISK)
            risk_dependencies, risk_writers = _dependencies(
                verifier=risk_verifier,
                quality=risk_quality,
                catalog=catalog,
            )
            configure_activity_dependencies(risk_dependencies)
            risk_input = _ingest_input().model_copy(update={"rollout_id": "r-risk"})
            risk_preflight = _manifest_preflight("r-risk")
            risk_source = risk_input.quality.source.model_copy(
                update={
                    "rollout_id": "r-risk",
                    "data_package_id": risk_preflight.identifiers.data_package_id,
                    "manifest_key": "raw/r-risk/rollout_manifest.json",
                    "manifest_fingerprint": risk_preflight.manifest_fingerprint,
                }
            )
            risk_input = risk_input.model_copy(
                update={
                    "manifest": _manifest_input("r-risk"),
                    "verification": risk_input.verification.model_copy(
                        update={"rollout_id": "r-risk"}
                    ),
                    "quality": risk_input.quality.model_copy(update={"source": risk_source}),
                    "alignment": risk_input.alignment.model_copy(update={"source": risk_source}),
                }
            )
            risk_id = workflow_id("ingest-rollout", "p1", "r-risk")
            launcher = TemporalWorkflowLauncher(
                "unused",
                task_queue="workflow-tests",
                client=environment.client,
            )
            first, second = await asyncio.gather(
                launcher.start(
                    workflow_name=INGEST_ROLLOUT_WORKFLOW,
                    workflow_input=risk_input,
                    workflow_id=risk_id,
                    job_type=INGEST_ROLLOUT_WORKFLOW,
                    project_id="p1",
                    resource_id="r-risk",
                ),
                launcher.start(
                    workflow_name=INGEST_ROLLOUT_WORKFLOW,
                    workflow_input=risk_input,
                    workflow_id=risk_id,
                    job_type=INGEST_ROLLOUT_WORKFLOW,
                    project_id="p1",
                    resource_id="r-risk",
                ),
            )
            assert first.job_id == second.job_id == risk_id
            risk_result = await environment.client.get_workflow_handle(
                risk_id,
                result_type=JobRecord,
            ).result()
            assert risk_result.status is JobStatus.QUALITY_RISK
            assert risk_result.result["media_state"] == "NOT_PRODUCED"
            assert risk_result.result["training_eligible"] is False
            assert risk_verifier.calls == 1
            assert risk_writers.calls == 0
            third = await launcher.start(
                workflow_name=INGEST_ROLLOUT_WORKFLOW,
                workflow_input=risk_input,
                workflow_id=risk_id,
                job_type=INGEST_ROLLOUT_WORKFLOW,
                project_id="p1",
                resource_id="r-risk",
            )
            assert third.job_id == risk_id
            assert third.status is JobStatus.QUALITY_RISK
            assert risk_verifier.calls == 1
            recovered_job = await launcher.get(risk_id)
            assert recovered_job.status is JobStatus.QUALITY_RISK

            # A human review cannot mutate the RISK result into PASS. Only a new
            # automatic QC execution may open the alignment/Lance path.
            with pytest.raises(ValidationError, match="AUTOMATIC"):
                QualityActivityInput.model_validate(
                    {
                        **risk_input.quality.model_dump(mode="json"),
                        "decision_source": "MANUAL",
                    }
                )
            pass_quality = StaticQuality(QualityStatus.PASS)
            pass_dependencies, pass_writers = _dependencies(
                verifier=StaticVerifier(),
                quality=pass_quality,
                catalog=catalog,
            )
            configure_activity_dependencies(pass_dependencies)
            new_pass_input = risk_input.model_copy(
                update={
                    "automatic_qc_run_id": "auto-pass-2",
                }
            )
            pass_result = await environment.client.execute_workflow(
                IngestRolloutWorkflow.run,
                new_pass_input,
                id=workflow_id("ingest-rollout", "p1", "r-risk/auto-pass-2"),
                task_queue="workflow-tests",
            )
            assert pass_result.status is JobStatus.SUCCEEDED
            assert pass_result.result is not None
            assert pass_result.result["quality_decision_source"] == "AUTOMATIC"
            assert pass_result.result["automatic_qc_run_id"] == "auto-pass-2"
            assert pass_writers.calls == 1
    finally:
        await environment.shutdown()


@pytest.mark.asyncio
async def test_temporal_commit_retries_without_duplicate_version_and_reconciles() -> None:
    environment = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    )
    try:
        schema = _schema()
        catalog = InMemoryLanceCatalog()
        catalog.register_schema(schema)
        steps = (
            StepRecord(
                rollout_id="r-crash",
                step_index=0,
                timestamp_ns=0,
                modalities={"x": 1.0},
                source_timestamps_ns={"x": (0,)},
                time_error_ns={"x": 0},
                valid={"x": True},
                repeated={"x": False},
            ),
        )
        manifest = CatalogManifestV1(
            project_id="p1",
            dataset_id="d1",
            schema_snapshot_id="schema-1",
            schema_fingerprint=schema.fingerprint,
            frequency_hz=30,
            rollout_id="r-crash",
            source_sha256="b" * 64,
            converter_version="align-v1",
            attempt_id="attempt-crash",
            fragment_uri="fake://staging/r-crash",
            step_count=1,
            content_hash=compute_fragment_hash(steps),
        )
        crashing = CrashAfterCommitCatalog(catalog)
        configure_activity_dependencies(
            ActivityDependencies(
                catalog=cast(LanceCatalogPort, crashing),
                catalog_reconciler=catalog,
            )
        )
        writer_input = DatasetWriterWorkflowInput(
            project_id="p1",
            dataset_id="d1",
            resource_id="d1/r-crash",
            commit=CatalogCommitActivityInput(
                fragment=CatalogFragmentPayloadV1(manifest=manifest, steps=steps)
            ),
        )
        # The execution is durable even when submitted while no worker is polling.
        handle = await environment.client.start_workflow(
            DatasetWriterWorkflow.run,
            writer_input,
            id=workflow_id("dataset-writer", "p1", "d1/r-crash"),
            task_queue="workflow-tests",
        )

        async with Worker(
            environment.client,
            task_queue="workflow-tests",
            workflows=list(ALL_WORKFLOWS),
            activities=list(ALL_ACTIVITIES),
        ):
            result = await handle.result()
            assert result.status is JobStatus.SUCCEEDED
            assert crashing.calls == 2
            assert len(catalog.list_versions("d1", project_id="p1")) == 1

            history = await handle.fetch_history()
            await Replayer(
                workflows=list(ALL_WORKFLOWS),
                data_converter=pydantic_data_converter,
            ).replay_workflow(history)

            pending_steps = tuple(
                step.model_copy(update={"rollout_id": "r-pending"}) for step in steps
            )
            pending_manifest = manifest.model_copy(
                update={
                    "rollout_id": "r-pending",
                    "source_sha256": "c" * 64,
                    "attempt_id": "attempt-pending",
                    "content_hash": compute_fragment_hash(pending_steps),
                }
            )
            with pytest.raises(CatalogIndexPendingError, match="indexing was interrupted"):
                catalog.commit_fragment(
                    pending_manifest,
                    pending_steps,
                    simulate_catalog_failure=True,
                )
            configure_activity_dependencies(
                ActivityDependencies(catalog_reconciler=ScopedCatalogReconciler(catalog))
            )
            reconciled = await environment.client.execute_workflow(
                CatalogReconciliationWorkflow.run,
                CatalogReconciliationWorkflowInput(project_id="p1", dataset_id="d1"),
                id=workflow_id("catalog-reconciliation", "p1", "d1"),
                task_queue="workflow-tests",
            )
            assert reconciled.status is JobStatus.SUCCEEDED
            assert len(catalog.list_versions("d1", project_id="p1")) == 2

            publication = PendingPublicationRecovery()
            configure_activity_dependencies(ActivityDependencies(publisher=publication))
            publication_request = PublishDatasetRequestV1(
                project_id="p1",
                dataset_id="d1",
                dataset_version="v1",
                base_lance_version="2",
            )
            publication_result = await environment.client.execute_workflow(
                PublishReconciliationWorkflow.run,
                PublishReconciliationWorkflowInput(request=publication_request),
                id=workflow_id("publish-reconciliation", "p1", "d1/v1"),
                task_queue="workflow-tests",
            )
            assert publication_result.status is JobStatus.SUCCEEDED
            assert publication.calls == 1
            assert publication.temporary_assets == {
                "published/p1/d1/v1/annotations.lance",
                "published/p1/d1/v1/training-manifest.json",
            }

            configure_activity_dependencies(
                ActivityDependencies(
                    publisher=publication,
                    exporter=StaticExporter(),
                )
            )
            publish_job = await environment.client.execute_workflow(
                PublishDatasetWorkflow.run,
                PublishDatasetWorkflowInput(request=publication_request),
                id=workflow_id("publish-dataset", "p1", "d1/v1"),
                task_queue="workflow-tests",
            )
            assert publish_job.result is not None
            manifest_payload = publish_job.result["manifest"]
            export_job = await environment.client.execute_workflow(
                ExportWorkflow.run,
                ExportWorkflowInput(
                    manifest=PublishedDatasetManifestV1.model_validate(manifest_payload),
                    format=ExportFormat.LANCE_SNAPSHOT,
                    attempt_id="export-attempt-1",
                ),
                id=workflow_id("export", "p1", "d1/v1/lance"),
                task_queue="workflow-tests",
            )
            assert publish_job.status is JobStatus.SUCCEEDED
            assert export_job.status is JobStatus.SUCCEEDED
            assert export_job.stage == "completed"
    finally:
        await environment.shutdown()


@pytest.mark.asyncio
async def test_temporal_quality_reject_and_cancellation_never_start_alignment() -> None:
    environment = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    )
    try:
        catalog = InMemoryLanceCatalog()
        catalog.register_schema(_schema())
        reject_quality = StaticQuality(QualityStatus.REJECT)
        dependencies, writers = _dependencies(
            verifier=StaticVerifier(),
            quality=reject_quality,
            catalog=catalog,
        )
        configure_activity_dependencies(dependencies)
        async with Worker(
            environment.client,
            task_queue="workflow-tests",
            workflows=list(ALL_WORKFLOWS),
            activities=list(ALL_ACTIVITIES),
        ):
            rejected = await environment.client.execute_workflow(
                IngestRolloutWorkflow.run,
                _ingest_input(),
                id=workflow_id("ingest-rollout", "p1", "reject"),
                task_queue="workflow-tests",
            )
            assert rejected.status is JobStatus.QUALITY_REJECTED
            assert rejected.result is not None
            assert rejected.result["raw_preserved"] is True
            assert writers.calls == 0

            invalid_verifier = StaticVerifier(VerificationStatus.REJECTED)
            untouched_quality = StaticQuality(QualityStatus.PASS)
            invalid_dependencies, invalid_writers = _dependencies(
                verifier=invalid_verifier,
                quality=untouched_quality,
                catalog=catalog,
            )
            configure_activity_dependencies(invalid_dependencies)
            invalid = await environment.client.execute_workflow(
                IngestRolloutWorkflow.run,
                _ingest_input(),
                id=workflow_id("ingest-rollout", "p1", "verification-reject"),
                task_queue="workflow-tests",
            )
            assert invalid.status is JobStatus.QUALITY_REJECTED
            assert invalid.error_code == "RAW_VERIFICATION_REJECTED"
            assert invalid_verifier.calls == 1
            assert untouched_quality.calls == 0
            assert invalid_writers.calls == 0

            started = threading.Event()
            release = threading.Event()
            blocking = BlockingVerifier(started, release)
            cancel_quality = StaticQuality(QualityStatus.PASS)
            cancel_dependencies, cancel_writers = _dependencies(
                verifier=blocking,
                quality=cancel_quality,
                catalog=catalog,
            )
            configure_activity_dependencies(cancel_dependencies)
            handle = await environment.client.start_workflow(
                IngestRolloutWorkflow.run,
                _ingest_input(),
                id=workflow_id("ingest-rollout", "p1", "cancel"),
                task_queue="workflow-tests",
            )
            assert await asyncio.to_thread(started.wait, 5)
            await handle.cancel()
            release.set()
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(handle.result(), timeout=10)
            assert cancel_quality.calls == 0
            assert cancel_writers.calls == 0
            persisted_jobs = cast(WorkflowJobRecorder, cancel_dependencies.workflow_jobs).jobs
            assert persisted_jobs[-1].status is JobStatus.CANCELLED
            assert persisted_jobs[-1].stage == "cancelled"
    finally:
        await environment.shutdown()


@pytest.mark.asyncio
async def test_four_camera_bundle_commits_before_annotation_ready() -> None:
    environment = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    )
    try:
        catalog = InMemoryLanceCatalog()
        catalog.register_schema(_schema())
        aligned_media = StaticAlignedMedia()
        dependencies, _writers = _dependencies(
            verifier=StaticVerifier(),
            quality=StaticQuality(QualityStatus.PASS),
            catalog=catalog,
            aligned_media=aligned_media,
            camera_topics=FOUR_CAMERAS,
        )
        configure_activity_dependencies(dependencies)

        async with Worker(
            environment.client,
            task_queue="four-camera-workflow-tests",
            workflows=list(ALL_WORKFLOWS),
            activities=list(ALL_ACTIVITIES),
        ):
            request = _ingest_input(FOUR_CAMERAS).model_copy(
                update={"media_task_queue": "four-camera-workflow-tests"}
            )
            result = await environment.client.execute_workflow(
                IngestRolloutWorkflow.run,
                request,
                id=workflow_id("ingest-rollout", "p1", "four-camera-ready"),
                task_queue="four-camera-workflow-tests",
            )

        assert result.status is JobStatus.SUCCEEDED
        assert result.result is not None
        assert result.result["aligned_media_count"] == 4
        assert result.result["training_eligible"] is True
        assert result.result["annotation_task"]["status"] == "CREATED"
        assert {request.camera_id for request in aligned_media.requests} == set(FOUR_CAMERAS)
        assert set(aligned_media.committed_artifact_ids) == {
            f"media-{camera}" for camera in FOUR_CAMERAS
        }
        assert len(catalog.list_versions("d1", project_id="p1")) == 1
    finally:
        await environment.shutdown()


@pytest.mark.asyncio
async def test_visible_lance_commit_is_reconciled_instead_of_deleting_media() -> None:
    environment = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    )
    try:
        catalog = InMemoryLanceCatalog()
        catalog.register_schema(_schema())
        aligned_media = CommitMarkerFailsUntilCleanup()
        dependencies, _writers = _dependencies(
            verifier=StaticVerifier(),
            quality=StaticQuality(QualityStatus.PASS),
            catalog=catalog,
            aligned_media=aligned_media,
        )
        configure_activity_dependencies(dependencies)

        async with Worker(
            environment.client,
            task_queue="commit-reconciliation-workflow-tests",
            workflows=list(ALL_WORKFLOWS),
            activities=list(ALL_ACTIVITIES),
        ):
            request = _ingest_input().model_copy(
                update={"media_task_queue": "commit-reconciliation-workflow-tests"}
            )
            result = await environment.client.execute_workflow(
                IngestRolloutWorkflow.run,
                request,
                id=workflow_id("ingest-rollout", "p1", "commit-marker-reconciliation"),
                task_queue="commit-reconciliation-workflow-tests",
            )

        assert result.status is JobStatus.TECHNICAL_FAILED
        assert len(catalog.list_versions("d1", project_id="p1")) == 1
        assert aligned_media.commit_marker_calls == 9
        assert aligned_media.committed_artifact_ids == ("media-/camera/front/image",)
        assert aligned_media.abandoned_artifact_ids == ()
        assert aligned_media.abandon_completed is False
    finally:
        await environment.shutdown()


@pytest.mark.asyncio
async def test_one_failed_camera_keeps_lance_and_annotation_closed_and_cleans_siblings() -> None:
    environment = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    )
    try:
        catalog = InMemoryLanceCatalog()
        catalog.register_schema(_schema())
        aligned_media = FailingAlignedMedia("/camera/right/image")
        dependencies, _writers = _dependencies(
            verifier=StaticVerifier(),
            quality=StaticQuality(QualityStatus.PASS),
            catalog=catalog,
            aligned_media=aligned_media,
            camera_topics=FOUR_CAMERAS,
        )
        configure_activity_dependencies(dependencies)

        async with Worker(
            environment.client,
            task_queue="failed-camera-workflow-tests",
            workflows=list(ALL_WORKFLOWS),
            activities=list(ALL_ACTIVITIES),
        ):
            request = _ingest_input(FOUR_CAMERAS).model_copy(
                update={"media_task_queue": "failed-camera-workflow-tests"}
            )
            result = await environment.client.execute_workflow(
                IngestRolloutWorkflow.run,
                request,
                id=workflow_id("ingest-rollout", "p1", "one-camera-failed"),
                task_queue="failed-camera-workflow-tests",
            )

        assert result.status is JobStatus.TECHNICAL_FAILED
        assert result.result is None
        assert len(catalog.list_versions("d1", project_id="p1")) == 0
        assert aligned_media.committed_artifact_ids == ()
        assert set(aligned_media.abandoned_artifact_ids) == {
            f"media-{camera}" for camera in FOUR_CAMERAS[:-1]
        }
        assert aligned_media.abandon_completed is True
        assert aligned_media.failure_calls > 0
    finally:
        await environment.shutdown()


@pytest.mark.asyncio
async def test_legacy_signal_sampling_is_repaired_without_second_dataset_commit():
    from dataclasses import replace

    class LegacyProjection(StaticProjection):
        repairs = 0
        cleanups = 0

        class Session(StaticProjection.Session):
            def frame_selection(self):
                return super().frame_selection().model_copy(update={"selected_group_count": 2})

        def materialize(self, source):
            self.repairs += 1
            return super().materialize(source)

        def cleanup(self, source):
            assert source.materialization is not None
            self.cleanups += 1

    environment = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    )
    try:
        catalog = InMemoryLanceCatalog()
        catalog.register_schema(_schema())
        dependencies, writers = _dependencies(
            verifier=StaticVerifier(), quality=StaticQuality(QualityStatus.PASS), catalog=catalog
        )
        projection = LegacyProjection()
        configure_activity_dependencies(replace(dependencies, ingest_projection=projection))
        async with Worker(
            environment.client,
            task_queue="sampling-repair-tests",
            workflows=list(ALL_WORKFLOWS),
            activities=list(ALL_ACTIVITIES),
        ):
            request = _ingest_input().model_copy(
                update={"media_task_queue": "sampling-repair-tests"}
            )
            handle = await environment.client.start_workflow(
                IngestRolloutWorkflow.run,
                request,
                id=workflow_id("ingest-rollout", "p1", "sampling-repair"),
                task_queue="sampling-repair-tests",
            )
            result = await handle.result()
            assert result.status is JobStatus.SUCCEEDED
            assert projection.repairs == projection.cleanups == 1
            assert writers.calls == 1
            assert len(catalog.list_versions("d1", project_id="p1")) == 1
            history = await handle.fetch_history()
        await Replayer(
            workflows=list(ALL_WORKFLOWS), data_converter=pydantic_data_converter
        ).replay_workflow(history)
    finally:
        await environment.shutdown()
