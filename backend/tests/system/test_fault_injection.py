from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from hc_data_platform.ingest.models import CompletedPart, RolloutManifestV1
from hc_data_platform.ingest.ports import InMemoryObjectStorage, crc64_ecma
from hc_data_platform.ingest.service import UploadSessionService
from hc_data_platform.lance_catalog import (
    AlignedFragmentManifestV1,
    CatalogIndexPendingError,
    DatasetSchemaSnapshot,
    InMemoryLanceCatalog,
    StepRecord,
    compute_fragment_hash,
)
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
from hc_data_platform.publishing.service import DatasetPublisher, ExportCoordinator
from hc_data_platform.quality import QualityEngine, QualityInputV1, QualityProfileV1
from hc_data_platform.quality.ports import (
    FakeMetadataSink,
    FakeReportSink,
    QualityPersistenceError,
)
from hc_data_platform.workflow import DatasetWriterWorkflow

FIXTURES = Path(__file__).parents[1] / "fixtures"


class _FailOnceReadStorage(InMemoryObjectStorage):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_read = True

    def read_chunks(self, key: str, chunk_size: int = 8 * 1024 * 1024) -> Iterable[bytes]:
        for chunk in super().read_chunks(key, max(1, min(chunk_size, 1024))):
            if self.fail_next_read:
                self.fail_next_read = False
                yield chunk
                raise ConnectionError("injected object-store network interruption")
            yield chunk


class _FailOnceSummarySink(FakeMetadataSink):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_summary = True

    def put_summary(self, summary: Any) -> None:
        if self.fail_next_summary:
            self.fail_next_summary = False
            raise ConnectionError("injected metadata-store interruption")
        super().put_summary(summary)


class _FailAfterPublishSink(InMemoryArtifactSink):
    def __init__(self) -> None:
        super().__init__()
        self.fail_after_publish = True

    def publish_attempt(
        self,
        *,
        attempt_id: str,
        artifact_uri: str,
        expected_sha256: str,
    ) -> str:
        result = super().publish_attempt(
            attempt_id=attempt_id,
            artifact_uri=artifact_uri,
            expected_sha256=expected_sha256,
        )
        if self.fail_after_publish:
            self.fail_after_publish = False
            raise ConnectionError("injected worker death after export promotion")
        return result


def _upload_manifest(body: bytes) -> RolloutManifestV1:
    started = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)
    return RolloutManifestV1(
        project_id="fault-project",
        task_id="fault-task",
        collection_job_id="fault-job",
        rollout_id="fault-rollout",
        collection_session_id="fault-session",
        recording_request_id="fault-request",
        data_package_id="fault-package",
        sequence_no=1,
        robot_id="robot-1",
        start_time=started,
        end_time=started + timedelta(seconds=1),
        expected_topics=["/camera/front/image"],
        actual_topics=["/camera/front/image"],
        cameras=[{"camera_id": "front", "topic": "/camera/front/image"}],
        topics=[{"name": "/camera/front/image", "required": True}],
        files=[
            {
                "path": "recording.mcap",
                "size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "crc64": crc64_ecma(body),
            }
        ],
        file_size=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        crc64=crc64_ecma(body),
        compression="none",
        recorder_version="be12-fault/1",
    )


def _catalog_fixture() -> tuple[
    InMemoryLanceCatalog,
    AlignedFragmentManifestV1,
    tuple[StepRecord, ...],
]:
    snapshot = DatasetSchemaSnapshot.create(
        project_id="fault-project",
        dataset_id="fault-dataset",
        schema_snapshot_id="schema-v1",
        frequency_hz=30,
        fields={"camera": "string"},
    )
    steps = tuple(
        StepRecord(
            rollout_id="fault-rollout",
            step_index=index,
            timestamp_ns=index * 33_333_333,
            modalities={"camera": f"frame-{index}"},
            source_timestamps_ns={"camera": (index * 33_333_333,)},
            time_error_ns={"camera": 0},
            valid={"camera": True},
            repeated={"camera": False},
        )
        for index in range(3)
    )
    manifest = AlignedFragmentManifestV1(
        project_id=snapshot.project_id,
        dataset_id=snapshot.dataset_id,
        schema_snapshot_id=snapshot.schema_snapshot_id,
        schema_fingerprint=snapshot.fingerprint,
        frequency_hz=snapshot.frequency_hz,
        rollout_id="fault-rollout",
        source_sha256="a" * 64,
        converter_version="converter-v1",
        attempt_id="attempt-1",
        fragment_uri="memory://staging/attempt-1",
        step_count=len(steps),
        content_hash=compute_fragment_hash(steps),
    )
    catalog = InMemoryLanceCatalog()
    catalog.register_schema(snapshot)
    return catalog, manifest, steps


def _published_fixture() -> tuple[Any, tuple[ExportStepV1, ...]]:
    steps = tuple(
        ExportStepV1(
            rollout_id="fault-rollout",
            step_index=index,
            timestamp_ns=index * 33_333_333,
            modalities={"camera": f"frame-{index}", "action": [float(index)]},
            source_timestamps_ns={
                "camera": (index * 33_333_333,),
                "action": (index * 33_333_333,),
            },
            time_error_ns={"camera": 0, "action": 0},
            valid={"camera": True, "action": True},
            repeated={"camera": False, "action": False},
        )
        for index in range(3)
    )
    publisher = DatasetPublisher(
        catalog=InMemoryCatalogSnapshot(
            [
                CatalogRolloutSnapshotV1(
                    rollout_id="fault-rollout",
                    source_mcap_sha256="a" * 64,
                    total_steps=3,
                    quality_status=PublishingQualityStatus.PASS,
                    derived_status=DerivedStatus.DERIVED_READY,
                    quality_profile_version="quality-v1",
                    alignment_profile_version="alignment-v1",
                    alignment_frequency_hz=30,
                    converter_version="converter-v1",
                )
            ]
        ),
        annotations=InMemoryAnnotationSnapshot(
            [ApprovedAnnotationSnapshotV1(rollout_id="fault-rollout", annotation_revision=1)]
        ),
        repository=InMemoryPublishedManifestRepository(),
        artifact_sink=InMemoryArtifactSink(),
        clock=lambda: datetime(2026, 8, 14, 8, tzinfo=timezone.utc),
    )
    manifest = publisher.publish(
        PublishDatasetRequestV1(
            project_id="fault-project",
            dataset_id="fault-dataset",
            dataset_version="v1",
            base_lance_version="1",
        )
    )
    return manifest, steps


def test_sha_network_interruption_retries_without_duplicate_raw_or_manifest() -> None:
    body = (FIXTURES / "mcap" / "legal.mcap").read_bytes()
    manifest = _upload_manifest(body)
    storage = _FailOnceReadStorage()
    service = UploadSessionService(storage)
    session = service.create_session(
        manifest=manifest,
        region_code="cn-hz",
        idempotency_key="sha-fault",
    )
    uploaded = storage.upload_part(
        session.multipart_upload_id,
        1,
        body,
        key=session.object_key,
    )
    service.complete_upload(
        session.session_id,
        [CompletedPart(part_number=1, etag=uploaded.etag)],
    )

    with pytest.raises(ConnectionError, match="network interruption"):
        service.commit_manifest(
            organization_id="org-a", session_id=session.session_id, manifest=manifest
        )
    assert len(storage.objects) == 1

    committed = service.commit_manifest(
        organization_id="org-a", session_id=session.session_id, manifest=manifest
    )
    assert (
        service.commit_manifest(
            organization_id="org-a", session_id=session.session_id, manifest=manifest
        )
        == committed
    )
    assert set(storage.objects) == {committed.object_key, committed.manifest_key}


def test_qc_sink_interruption_reuses_one_immutable_report() -> None:
    case = json.loads((FIXTURES / "structured" / "legal_30hz.json").read_text())
    data = QualityInputV1.model_validate(case["input"])
    profile = QualityProfileV1.model_validate(case["profile"])
    reports = FakeReportSink()
    summaries = _FailOnceSummarySink()
    engine = QualityEngine(reports, summaries)

    with pytest.raises(QualityPersistenceError) as captured:
        engine.evaluate(data, profile)
    assert captured.value.stage == "metadata"
    assert len(reports.reports) == 1
    report = engine.evaluate(data, profile)
    assert len(reports.reports) == 1
    assert summaries.summaries[data.rollout_id].status == report.status
    assert summaries.summaries[data.rollout_id].report_sha256 == report.content_sha256


def test_lance_commit_then_db_registration_failure_reconciles_once() -> None:
    catalog, manifest, steps = _catalog_fixture()

    with pytest.raises(CatalogIndexPendingError):
        catalog.commit_fragment(manifest, steps, simulate_catalog_failure=True)
    assert catalog.current_version("fault-dataset") is None

    version, ready = catalog.commit_fragment(manifest, steps)
    assert (version.version, ready.step_count) == (1, 3)
    assert len(catalog.list_versions("fault-dataset")) == 1
    assert len(catalog.read_steps("fault-dataset", "fault-rollout", 0, 10).steps) == 3


def test_worker_death_after_lance_side_effect_replays_without_duplicate_steps() -> None:
    catalog, manifest, steps = _catalog_fixture()

    class CommitThenDie:
        def __init__(self) -> None:
            self.fail = True

        def commit(self, fragment: dict[str, Any]) -> dict[str, Any]:
            del fragment
            version, ready = catalog.commit_fragment(manifest, steps)
            if self.fail:
                self.fail = False
                raise ConnectionError("injected worker death after Lance commit")
            return {"version": version.version, "steps": ready.step_count}

    workflow = DatasetWriterWorkflow(CommitThenDie())
    with pytest.raises(ConnectionError, match="worker death"):
        workflow.submit("fault-rollout:a:converter-v1", {})
    result = workflow.submit("fault-rollout:a:converter-v1", {})
    assert workflow.submit("fault-rollout:a:converter-v1", {}) == result
    assert result == {"version": 1, "steps": 3}
    assert len(catalog.list_versions("fault-dataset")) == 1


def test_worker_death_after_export_promotion_recovers_one_downloadable_version() -> None:
    manifest, steps = _published_fixture()
    sink = _FailAfterPublishSink()
    coordinator = ExportCoordinator(
        source=InMemoryExportSource(steps),
        sink=sink,
        exporters=[LeRobotV3Exporter()],
    )

    with pytest.raises(ConnectionError, match="worker death"):
        coordinator.export(
            manifest,
            format=ExportFormat.LEROBOT_V3,
            attempt_id="fault-export-attempt",
        )
    assert len(sink.artifacts) == 1
    assert len(sink.download_authorizations) == 1

    recovered = coordinator.export(
        manifest,
        format=ExportFormat.LEROBOT_V3,
        attempt_id="fault-export-attempt",
    )
    assert recovered.row_count == 3
    assert len(sink.attempts) == 1
    assert len(sink.artifacts) == 1
    assert len(sink.download_authorizations) == 1
