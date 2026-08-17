from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

import pyarrow as pa
import pyarrow.parquet as pq

from hc_data_platform.alignment import (
    AlignmentEngine,
    AlignmentInputV1,
    AlignmentProfileV1,
    ModalityKind,
    ModalityStreamV1,
    TimedSampleV1,
)
from hc_data_platform.annotation import (
    AnnotationActor,
    AnnotationOperation,
    InMemoryAnnotationService,
    OperationKind,
    ReviewDecision,
)
from hc_data_platform.ingest.models import CompletedPart, RolloutManifestV1
from hc_data_platform.ingest.persistence import InMemoryIngestPersistence
from hc_data_platform.ingest.ports import InMemoryObjectStorage, crc64_ecma
from hc_data_platform.ingest.service import UploadSessionService
from hc_data_platform.lance_catalog import (
    AlignedFragmentManifestV1 as CatalogFragmentManifest,
)
from hc_data_platform.lance_catalog import (
    DatasetSchemaSnapshot,
    InMemoryLanceCatalog,
    StepRecord,
    compute_fragment_hash,
)
from hc_data_platform.preview.memory import (
    HmacUrlSigner,
    InMemoryExclusionReader,
    InMemoryMediaEncoder,
    InMemoryPreviewCache,
)
from hc_data_platform.preview.models import PreviewFrameV1, PreviewRequestV1, ViewMode
from hc_data_platform.preview.service import PreviewService
from hc_data_platform.publishing.exporters import LeRobotV3Exporter
from hc_data_platform.publishing.memory import (
    InMemoryAnnotationSnapshot,
    InMemoryArtifactSink,
    InMemoryCatalogSnapshot,
    InMemoryExportSource,
    InMemoryPublishedManifestRepository,
)
from hc_data_platform.publishing.models import (
    ApprovedAnnotationSnapshotV1,
    CatalogRolloutSnapshotV1,
    DerivedStatus,
    ExportFormat,
    ExportStepV1,
    PublishDatasetRequestV1,
)
from hc_data_platform.publishing.models import (
    QualityStatus as PublishingQualityStatus,
)
from hc_data_platform.publishing.models import (
    StepRangeV1 as PublishingStepRange,
)
from hc_data_platform.publishing.service import DatasetPublisher, ExportCoordinator
from hc_data_platform.quality import QualityEngine, QualityInputV1, QualityProfileV1
from hc_data_platform.verification import FakeDecoderProbe, McapVerifier

FIXTURES = Path(__file__).parents[1] / "fixtures"
PROJECT_ID = "be12-project"
REGION_CODE = "cn-hz"
DATASET_ID = "be12-dataset"
ROLLOUT_ID = "be12-rollout-1"
TOPICS = {"/camera/front/image", "/joint_states", "/action", "/points"}


class _ReadableIngestStorage:
    def __init__(self, storage: InMemoryObjectStorage) -> None:
        self._storage = storage

    def open_reader(self, object_key: str) -> BinaryIO:
        return io.BytesIO(self._storage.objects[object_key])


def _nearest_source_timestamp(step: StepRecord, camera_id: str) -> int | None:
    timestamps = step.source_timestamps_ns.get(camera_id, ())
    if not timestamps:
        return None
    return min(timestamps, key=lambda value: abs(value - step.timestamp_ns))


class _CatalogPreviewReader:
    def __init__(self, catalog: InMemoryLanceCatalog) -> None:
        self._catalog = catalog

    def read_steps(
        self,
        *,
        project_id: str,
        dataset_id: str,
        rollout_id: str,
        lance_version: str,
        camera_id: str,
        start_step: int | None,
        end_step: int | None,
    ) -> Sequence[PreviewFrameV1]:
        del project_id
        window = self._catalog.read_steps(
            dataset_id,
            rollout_id,
            0 if start_step is None else start_step,
            10_000 if end_step is None else end_step,
            version=int(lance_version),
        )
        return tuple(
            PreviewFrameV1(
                rollout_id=step.rollout_id,
                step_index=step.step_index,
                timestamp_ns=step.timestamp_ns,
                source_timestamp_ns=_nearest_source_timestamp(step, camera_id),
                image_ref=f"lance://{dataset_id}/{lance_version}/{rollout_id}/{camera_id}/{step.step_index}",
                valid=step.valid.get(camera_id, False),
                invalid_reason=(
                    None if step.valid.get(camera_id, False) else "invalid aligned image"
                ),
            )
            for step in window.steps
        )


def _manifest(body: bytes) -> RolloutManifestV1:
    started = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)
    return RolloutManifestV1(
        project_id=PROJECT_ID,
        task_id="collection-task",
        collection_job_id="collection-job",
        rollout_id=ROLLOUT_ID,
        sequence_no=1,
        robot_id="robot-1",
        start_time=started,
        end_time=started + timedelta(seconds=1),
        expected_topics=sorted(TOPICS),
        actual_topics=sorted(TOPICS),
        file_size=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        crc64=crc64_ecma(body),
        compression="none",
        recorder_version="be12-e2e/1",
    )


def _quality_input(source_sha256: str) -> tuple[QualityInputV1, QualityProfileV1]:
    case = json.loads((FIXTURES / "structured" / "legal_30hz.json").read_text())
    data = QualityInputV1.model_validate(
        {
            **case["input"],
            "rollout_id": ROLLOUT_ID,
            "source_sha256": source_sha256,
        }
    )
    return data, QualityProfileV1.model_validate(case["profile"])


def _alignment_input(source_sha256: str) -> tuple[AlignmentInputV1, AlignmentProfileV1]:
    timestamps = [(index * 1_000_000_000) // 30 for index in range(30)]

    def stream(kind: ModalityKind, values: Sequence[Any]) -> ModalityStreamV1:
        return ModalityStreamV1(
            kind=kind,
            samples=tuple(
                TimedSampleV1(timestamp_ns=timestamp, value=value)
                for timestamp, value in zip(timestamps, values, strict=True)
            ),
        )

    streams = {
        "/camera/front/image": stream(
            ModalityKind.IMAGE,
            [f"lance://camera/frame-{index:02d}" for index in range(30)],
        ),
        "/joint_states": stream(
            ModalityKind.CONTINUOUS,
            [[float(index) / 30.0] for index in range(30)],
        ),
        "/action": stream(ModalityKind.ACTION, [[float(index)] for index in range(30)]),
        "/points": stream(
            ModalityKind.POINT_CLOUD,
            [f"lance://points/cloud-{index:02d}" for index in range(30)],
        ),
    }
    return (
        AlignmentInputV1(
            rollout_id=ROLLOUT_ID,
            source_sha256=source_sha256,
            attempt_id="alignment-attempt-1",
            start_ns=0,
            end_ns=1_000_000_000,
            streams=streams,
        ),
        AlignmentProfileV1(
            profile_id="alignment-30hz-v1",
            converter_version="be12-system-adapter/1",
            frequency_hz=30,
            required_modalities=frozenset(streams),
            default_tolerance_ns=20_000_000,
        ),
    )


def _catalog_steps(rows: Sequence[Any]) -> tuple[StepRecord, ...]:
    result = []
    for row in rows:
        assert all(
            len(value.source_timestamps_ns) == 1 for value in row.modalities.values() if value.valid
        )
        result.append(
            StepRecord(
                rollout_id=row.rollout_id,
                step_index=row.step_index,
                timestamp_ns=row.timestamp_ns,
                modalities={name: value.value for name, value in row.modalities.items()},
                source_timestamps_ns={
                    name: value.source_timestamps_ns[0] if value.valid else None
                    for name, value in row.modalities.items()
                },
                time_error_ns={name: value.time_error_ns for name, value in row.modalities.items()},
                valid={name: value.valid for name, value in row.modalities.items()},
                repeated={name: value.repeated for name, value in row.modalities.items()},
                sample_valid=row.sample_valid,
            )
        )
    return tuple(result)


def _export_steps(steps: Sequence[StepRecord]) -> tuple[ExportStepV1, ...]:
    return tuple(ExportStepV1.model_validate(step.model_dump(mode="python")) for step in steps)


def test_complete_pipeline_and_duplicate_recovery_invariants() -> None:
    raw = (FIXTURES / "mcap" / "legal.mcap").read_bytes()
    manifest = _manifest(raw)

    storage = InMemoryObjectStorage()
    persistence = InMemoryIngestPersistence()
    upload = UploadSessionService(storage, persistence)
    session = upload.create_session(
        manifest=manifest,
        region_code=REGION_CODE,
        idempotency_key="e2e-upload",
    )
    replayed_session = upload.create_session(
        manifest=manifest,
        region_code=REGION_CODE,
        idempotency_key="e2e-upload-duplicate-message",
    )
    assert replayed_session == session
    assert persistence.get_rollout(PROJECT_ID, ROLLOUT_ID) is not None
    assert persistence.find_session(PROJECT_ID, ROLLOUT_ID) == session
    uploaded = storage.upload_part(
        session.multipart_upload_id,
        1,
        raw,
        key=session.object_key,
    )
    upload.complete_upload(
        session.session_id,
        [CompletedPart(part_number=1, etag=uploaded.etag)],
    )
    committed = upload.commit_manifest(session_id=session.session_id, manifest=manifest)
    assert upload.commit_manifest(session_id=session.session_id, manifest=manifest) == committed
    assert len(storage.objects) == 2  # immutable Raw plus the manifest commit marker

    verification = McapVerifier(
        _ReadableIngestStorage(storage),
        FakeDecoderProbe(),
    ).verify(
        rollout_id=ROLLOUT_ID,
        object_key=committed.object_key,
        source_sha256=committed.sha256,
        required_topics=TOPICS,
        known_optional_topics=set(),
    )
    assert verification.status.value == "RAW_VERIFIED"

    quality_data, quality_profile = _quality_input(committed.sha256)
    quality = QualityEngine().evaluate(quality_data, quality_profile)
    assert quality.status.value == "PASS"

    alignment_data, alignment_profile = _alignment_input(committed.sha256)
    staged, aligned_rows = AlignmentEngine().align_in_memory(alignment_data, alignment_profile)
    assert staged.row_count == 30
    assert all(row.sample_valid for row in aligned_rows)

    steps = _catalog_steps(aligned_rows)
    snapshot = DatasetSchemaSnapshot.create(
        project_id=PROJECT_ID,
        dataset_id=DATASET_ID,
        schema_snapshot_id="schema-v1",
        frequency_hz=30,
        fields={
            "/camera/front/image": "string",
            "/joint_states": "list<float64>",
            "/action": "list<float64>",
            "/points": "string",
        },
    )
    catalog_manifest = CatalogFragmentManifest(
        project_id=PROJECT_ID,
        dataset_id=DATASET_ID,
        schema_snapshot_id=snapshot.schema_snapshot_id,
        schema_fingerprint=snapshot.fingerprint,
        frequency_hz=30,
        rollout_id=ROLLOUT_ID,
        source_sha256=committed.sha256,
        converter_version=staged.converter_version,
        attempt_id=staged.attempt_id,
        fragment_uri=staged.staging_uri,
        step_count=len(steps),
        content_hash=compute_fragment_hash(steps),
    )
    catalog = InMemoryLanceCatalog()
    catalog.register_schema(snapshot)
    version, ready = catalog.commit_fragment(catalog_manifest, steps)
    replay_version, replay_ready = catalog.commit_fragment(
        catalog_manifest.model_copy(
            update={"attempt_id": "alignment-retry", "fragment_uri": "fake://retry/fragment"}
        ),
        steps,
    )
    assert (replay_version, replay_ready) == (version, ready)
    assert len(catalog.list_versions(DATASET_ID)) == 1
    assert len(catalog.read_steps(DATASET_ID, ROLLOUT_ID, 0, 100).steps) == 30

    preview_encoder = InMemoryMediaEncoder()
    preview = PreviewService(
        step_reader=_CatalogPreviewReader(catalog),
        exclusions=InMemoryExclusionReader(),
        encoder=preview_encoder,
        cache=InMemoryPreviewCache(),
        signer=HmacUrlSigner(b"be12-preview-test-secret"),
        clock=lambda: datetime(2026, 8, 14, 8, tzinfo=timezone.utc),
    ).create(
        PreviewRequestV1(
            project_id=PROJECT_ID,
            dataset_id=DATASET_ID,
            rollout_id=ROLLOUT_ID,
            lance_version=str(version.version),
            annotation_revision=0,
            camera_id="/camera/front/image",
            view_mode=ViewMode.ORIGINAL,
            start_step=0,
            end_step=30,
        )
    )
    assert preview.frame_count == 30
    assert preview.placeholder_count == 0

    annotations = InMemoryAnnotationService()
    task = annotations.create_task(
        task_id="annotation-task-1",
        project_id=PROJECT_ID,
        dataset_id=DATASET_ID,
        dataset_version=version.version,
        rollout_id=ROLLOUT_ID,
    )
    annotator = AnnotationActor(
        actor_id="alice",
        roles=frozenset({"annotator"}),
        project_ids=frozenset({PROJECT_ID}),
    )
    reviewer = AnnotationActor(
        actor_id="bob",
        roles=frozenset({"reviewer"}),
        project_ids=frozenset({PROJECT_ID}),
    )
    task = annotations.claim(task.task_id, annotator)
    operation = AnnotationOperation(
        operation_id="exclude-2-4",
        kind=OperationKind.EXCLUDE,
        start_step=2,
        end_step=4,
        reason="golden operator exclusion",
    )
    revision = annotations.save_draft(
        task.task_id,
        annotator,
        [operation],
        expected_revision=0,
        if_match=task.etag,
        mutation_id="annotation-mutation-1",
    )
    replay_revision = annotations.save_draft(
        task.task_id,
        annotator,
        [operation],
        expected_revision=0,
        if_match=task.etag,
        mutation_id="annotation-mutation-1",
    )
    assert replay_revision == revision
    assert [item.revision for item in annotations.list_revisions(task.task_id, annotator)] == [
        0,
        1,
    ]
    submitted = annotations.submit(
        task.task_id,
        annotator,
        expected_revision=revision.revision,
        if_match=annotations.get_task(task.task_id).etag,
    )
    approved = annotations.review(
        task.task_id,
        reviewer,
        ReviewDecision.APPROVE,
        revision=revision.revision,
        if_match=submitted.etag,
    )
    assert approved is not None
    assert len(annotations.list_reviews(task.task_id, reviewer)) == 1

    excluded = tuple(
        PublishingStepRange(start_step=item.start_step, end_step=item.end_step)
        for item in annotations.effective_exclusions(task.task_id, revision=revision.revision)
    )
    publication_sink = InMemoryArtifactSink()
    repository = InMemoryPublishedManifestRepository()
    publisher = DatasetPublisher(
        catalog=InMemoryCatalogSnapshot(
            [
                CatalogRolloutSnapshotV1(
                    rollout_id=ROLLOUT_ID,
                    source_mcap_sha256=committed.sha256,
                    total_steps=ready.step_count,
                    quality_status=PublishingQualityStatus.PASS,
                    derived_status=DerivedStatus.DERIVED_READY,
                    quality_profile_version=quality.profile_id,
                    alignment_profile_version=staged.profile_id,
                    alignment_frequency_hz=staged.frequency_hz,
                    converter_version=staged.converter_version,
                )
            ]
        ),
        annotations=InMemoryAnnotationSnapshot(
            [
                ApprovedAnnotationSnapshotV1(
                    rollout_id=ROLLOUT_ID,
                    annotation_revision=revision.revision,
                    annotation_task_id=task.task_id,
                    excluded_step_ranges=excluded,
                )
            ]
        ),
        repository=repository,
        artifact_sink=publication_sink,
        clock=lambda: datetime(2026, 8, 14, 8, tzinfo=timezone.utc),
    )
    publish_request = PublishDatasetRequestV1(
        project_id=PROJECT_ID,
        dataset_id=DATASET_ID,
        dataset_version="pilot-v1",
        base_lance_version=str(version.version),
    )
    published = publisher.publish(publish_request)
    assert publisher.publish(publish_request) == published
    assert (
        repository.get(
            project_id=PROJECT_ID,
            dataset_id=DATASET_ID,
            dataset_version="pilot-v1",
        )
        == published
    )
    assert len(publication_sink.artifacts) == 2
    assert published.rollouts[0].included_step_ranges == (
        PublishingStepRange(start_step=0, end_step=2),
        PublishingStepRange(start_step=4, end_step=30),
    )

    export_sink = InMemoryArtifactSink()
    coordinator = ExportCoordinator(
        source=InMemoryExportSource(_export_steps(steps)),
        sink=export_sink,
        exporters=[LeRobotV3Exporter()],
    )
    exported = coordinator.export(
        published,
        format=ExportFormat.LEROBOT_V3,
        attempt_id="export-attempt-1",
    )
    replay_export = coordinator.export(
        published,
        format=ExportFormat.LEROBOT_V3,
        attempt_id="export-attempt-1",
    )
    assert replay_export == exported
    assert exported.row_count == 28
    assert len(export_sink.artifacts) == 1
    assert len(export_sink.download_authorizations) == 1

    artifact = export_sink.artifacts[exported.artifact_uri]
    assert artifact.startswith(b"PK\x03\x04"), (exported, artifact[:80])
    with zipfile.ZipFile(io.BytesIO(artifact)) as archive:
        info = json.loads(archive.read("meta/info.json"))
        table = pq.read_table(pa.BufferReader(archive.read("data/chunk-000/file-000.parquet")))
    assert info["total_frames"] == 28
    assert table.num_rows == 28
    assert table.column("hc.source_step_index").to_pylist() == [*range(2), *range(4, 30)]
