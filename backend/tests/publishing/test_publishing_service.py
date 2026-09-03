from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from hc_data_platform.annotation import (
    AnnotationActor,
    AnnotationOperation,
    InMemoryAnnotationService,
    OperationKind,
    ReviewDecision,
)
from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.lance_catalog import (
    AlignedFragmentManifestV1,
    DatasetSchemaSnapshot,
    InMemoryLanceCatalog,
    StepRecord,
    compute_fragment_hash,
)
from hc_data_platform.publishing.adapters import (
    ApprovedAnnotationSnapshotAdapter,
    CatalogRolloutStateV1,
    CatalogSnapshotAdapter,
    InMemoryCatalogRolloutState,
    StepReaderAdapter,
)
from hc_data_platform.publishing.exporters import (
    LanceSnapshotExporter,
    LeRobotV3Exporter,
)
from hc_data_platform.publishing.memory import (
    InMemoryAnnotationSnapshot,
    InMemoryArtifactSink,
    InMemoryCatalogSnapshot,
    InMemoryExportSource,
    InMemoryLanceSnapshotExporter,
    InMemoryLeRobotV3Exporter,
    InMemoryPublishedManifestRepository,
)
from hc_data_platform.publishing.models import (
    ApprovedAnnotationSnapshotV1,
    CatalogRolloutSnapshotV1,
    DerivedStatus,
    ExportFormat,
    ExportStepV1,
    PublishDatasetRequestV1,
    PublishedDatasetManifestV1,
    QualityStatus,
    StepRangeV1,
)
from hc_data_platform.publishing.s3 import S3ArtifactSink
from hc_data_platform.publishing.service import DatasetPublisher, ExportCoordinator

NOW = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)


class _ReadableS3Client:
    def get_object(self, **_arguments: object) -> dict[str, io.BytesIO]:
        return {"Body": io.BytesIO(b"immutable-export")}


class _PublicPresignClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def generate_presigned_url(self, operation: str, **arguments: object) -> str:
        self.calls.append((operation, arguments))
        return "https://downloads.example.test/hc/export?signature=opaque"


def test_s3_export_authorization_uses_public_presign_client() -> None:
    public = _PublicPresignClient()
    sink = S3ArtifactSink(
        _ReadableS3Client(),
        "private-bucket",
        prefix="exports",
        presign_client=public,
    )

    authorization = sink.get_download_uri("dataset/version/export.zip")

    assert authorization == "https://downloads.example.test/hc/export?signature=opaque"
    assert public.calls == [
        (
            "get_object",
            {
                "Params": {
                    "Bucket": "private-bucket",
                    "Key": "exports/dataset/version/export.zip",
                    "ResponseContentDisposition": 'attachment; filename="export.zip"',
                    "ResponseContentType": "application/zip",
                },
                "ExpiresIn": 900,
                "HttpMethod": "GET",
            },
        )
    ]


def rollout(
    rollout_id: str,
    *,
    quality: QualityStatus = QualityStatus.PASS,
    derived: DerivedStatus = DerivedStatus.DERIVED_READY,
    total_steps: int = 6,
) -> CatalogRolloutSnapshotV1:
    return CatalogRolloutSnapshotV1(
        rollout_id=rollout_id,
        source_mcap_sha256=hashlib.sha256(rollout_id.encode()).hexdigest(),
        total_steps=total_steps,
        quality_status=quality,
        derived_status=derived,
        quality_profile_version="quality-v2",
        alignment_profile_version="alignment-v5",
        alignment_frequency_hz=30,
        converter_version="converter-v3",
    )


def approval(rollout_id: str, ranges: tuple[StepRangeV1, ...] = ()) -> ApprovedAnnotationSnapshotV1:
    return ApprovedAnnotationSnapshotV1(
        rollout_id=rollout_id,
        annotation_revision=4,
        annotation_task_id=f"task-{rollout_id}",
        excluded_step_ranges=ranges,
    )


def request(version: str = "2026.08.14") -> PublishDatasetRequestV1:
    return PublishDatasetRequestV1(
        project_id="project-1",
        dataset_id="dataset-1",
        dataset_version=version,
        base_lance_version="lance-v7",
    )


def publisher(
    rollouts: list[CatalogRolloutSnapshotV1],
    approvals: list[ApprovedAnnotationSnapshotV1],
    repository: InMemoryPublishedManifestRepository | None = None,
    now: datetime = NOW,
    artifacts: InMemoryArtifactSink | None = None,
) -> DatasetPublisher:
    return DatasetPublisher(
        catalog=InMemoryCatalogSnapshot(rollouts),
        annotations=InMemoryAnnotationSnapshot(approvals),
        repository=repository or InMemoryPublishedManifestRepository(),
        artifact_sink=artifacts,
        clock=lambda: now,
    )


def test_preflight_does_not_treat_quality_as_a_dataset_version_gate() -> None:
    rollouts = [
        rollout("r1"),
        rollout("r2", quality=QualityStatus.RISK),
        rollout("r3", quality=QualityStatus.REJECT),
        rollout("r4", derived=DerivedStatus.FAILED),
        rollout("r5"),
    ]
    service = publisher(rollouts, [approval(item.rollout_id) for item in rollouts[:-1]])
    report = service.preflight(request())

    assert [item.rollout_id for item in report.eligible_rollouts] == ["r1", "r2", "r3"]
    reasons = {item.rollout_id: item.reasons for item in report.excluded_rollouts}
    assert reasons["r4"] == ("DERIVED_FAILED",)
    assert reasons["r5"] == ("ANNOTATION_NOT_APPROVED",)


def test_manifest_freezes_complete_lineage_and_effective_step_ranges() -> None:
    ranges = (
        StepRangeV1(start_step=1, end_step=3),
        StepRangeV1(start_step=2, end_step=4),
    )
    manifest = publisher([rollout("r1")], [approval("r1", ranges)]).publish(request())

    entry = manifest.rollouts[0]
    assert entry.excluded_step_ranges == (StepRangeV1(start_step=1, end_step=4),)
    assert entry.included_step_ranges == (
        StepRangeV1(start_step=0, end_step=1),
        StepRangeV1(start_step=4, end_step=6),
    )
    assert entry.source_mcap_sha256 == hashlib.sha256(b"r1").hexdigest()
    assert entry.base_lance_version == "lance-v7"
    assert entry.annotation_task_id == "task-r1"
    assert entry.annotation_revision == 4
    assert entry.quality_profile_version == "quality-v2"
    assert entry.alignment_profile_version == "alignment-v5"
    assert entry.alignment_frequency_hz == 30
    assert entry.converter_version == "converter-v3"
    assert manifest.annotations_uri.endswith("/annotations.lance")
    assert manifest.training_manifest_uri.endswith("/training-manifest.json")


def test_publication_emits_deterministic_annotations_and_training_assets() -> None:
    first_assets = InMemoryArtifactSink()
    second_assets = InMemoryArtifactSink()
    first = publisher(
        [rollout("r2"), rollout("r1")],
        [approval("r1"), approval("r2")],
        artifacts=first_assets,
    ).publish(request())
    second = publisher(
        [rollout("r1"), rollout("r2")],
        [approval("r2"), approval("r1")],
        now=NOW + timedelta(days=1),
        artifacts=second_assets,
    ).publish(request())

    assert first.content_hash == second.content_hash
    assert first.annotations_content_sha256 == second.annotations_content_sha256
    assert first.training_manifest_content_sha256 == second.training_manifest_content_sha256
    assert first_assets.artifacts == second_assets.artifacts
    annotations = first_assets.artifacts[first.annotations_uri]
    training = first_assets.artifacts[first.training_manifest_uri]
    assert hashlib.sha256(annotations).hexdigest() == first.annotations_content_sha256
    assert hashlib.sha256(training).hexdigest() == first.training_manifest_content_sha256
    annotation_payload = json.loads(annotations)
    training_payload = json.loads(training)
    assert [row["rollout_id"] for row in annotation_payload["rows"]] == ["r1", "r2"]
    assert training_payload["publication_content_hash"] == first.content_hash
    assert training_payload["annotations"]["content_sha256"] == (first.annotations_content_sha256)


def test_same_frozen_input_has_deterministic_hash_and_idempotent_version() -> None:
    repository = InMemoryPublishedManifestRepository()
    assets = InMemoryArtifactSink()
    first_service = publisher([rollout("r1")], [approval("r1")], repository, NOW, assets)
    second_service = publisher(
        [rollout("r1")],
        [approval("r1")],
        repository,
        NOW + timedelta(days=1),
        assets,
    )

    first = first_service.publish(request())
    second = second_service.publish(request())

    assert first == second
    assert second.created_at == NOW
    assert len(assets.artifacts) == 2


def test_published_version_cannot_be_replaced_or_overwrite_assets() -> None:
    repository = InMemoryPublishedManifestRepository()
    assets = InMemoryArtifactSink()
    first = publisher([rollout("r1")], [approval("r1")], repository, artifacts=assets).publish(
        request()
    )
    frozen_assets = dict(assets.artifacts)
    changed = publisher(
        [rollout("r1", total_steps=7)],
        [approval("r1")],
        repository,
        artifacts=assets,
    )

    with pytest.raises(ProblemException) as captured:
        changed.publish(request())
    assert captured.value.problem.code == "DATASET_VERSION_IMMUTABLE"
    assert (
        repository.get(project_id="project-1", dataset_id="dataset-1", dataset_version="2026.08.14")
        == first
    )
    assert assets.artifacts == frozen_assets
    assert changed.publish(request("2026.08.15")).content_hash != first.content_hash

    forged_same_hash = first.model_copy(
        update={"training_manifest_uri": "published/forged/manifest.json"}
    )
    with pytest.raises(ProblemException) as forged_error:
        repository.create_immutable(forged_same_hash)
    assert forged_error.value.problem.code == "DATASET_VERSION_IMMUTABLE"


def test_no_eligible_rollout_does_not_publish_partial_manifest_or_assets() -> None:
    repository = InMemoryPublishedManifestRepository()
    assets = InMemoryArtifactSink()
    service = publisher(
        [rollout("r1", quality=QualityStatus.RISK, derived=DerivedStatus.FAILED)],
        [approval("r1")],
        repository,
        artifacts=assets,
    )

    with pytest.raises(ProblemException) as captured:
        service.publish(request())
    assert captured.value.problem.code == "NO_ELIGIBLE_ROLLOUTS"
    assert (
        repository.get(project_id="project-1", dataset_id="dataset-1", dataset_version="2026.08.14")
        is None
    )
    assert assets.artifacts == {}


def test_manifest_model_is_immutable() -> None:
    manifest = publisher([rollout("r1")], [approval("r1")]).publish(request())
    with pytest.raises(ValidationError):
        manifest.dataset_version = "changed"  # type: ignore[misc]


def _catalog_with_rollout() -> tuple[InMemoryLanceCatalog, tuple[StepRecord, ...]]:
    catalog = InMemoryLanceCatalog()
    schema = DatasetSchemaSnapshot.create(
        project_id="project-a",
        dataset_id="dataset-a",
        schema_snapshot_id="schema-a",
        frequency_hz=30,
        fields={"observation.state": "list<float32>", "action": "list<float32>"},
    )
    catalog.register_schema(schema)
    steps = tuple(
        StepRecord(
            rollout_id="rollout-a",
            step_index=index,
            timestamp_ns=index * 33_333_333,
            modalities={
                "observation.state": [float(index)],
                "action": [float(index + 1)],
            },
            source_timestamps_ns={
                "observation.state": (index * 33_333_333,),
                "action": (index * 33_333_333,),
            },
            time_error_ns={"observation.state": 0, "action": 0},
            valid={"observation.state": True, "action": True},
            repeated={"observation.state": False, "action": False},
        )
        for index in range(5)
    )
    fragment = AlignedFragmentManifestV1(
        project_id="project-a",
        dataset_id="dataset-a",
        schema_snapshot_id="schema-a",
        schema_fingerprint=schema.fingerprint,
        frequency_hz=30,
        rollout_id="rollout-a",
        source_sha256=hashlib.sha256(b"source-a").hexdigest(),
        converter_version="converter-a",
        attempt_id="attempt-a",
        fragment_uri="memory://fragment-a",
        step_count=len(steps),
        content_hash=compute_fragment_hash(steps),
    )
    catalog.commit_fragment(fragment, steps)
    return catalog, steps


def test_be08_catalog_snapshot_and_step_reader_adapters_freeze_real_contracts() -> None:
    catalog, steps = _catalog_with_rollout()
    states = InMemoryCatalogRolloutState(
        [
            CatalogRolloutStateV1(
                project_id="project-a",
                dataset_id="dataset-a",
                dataset_version=1,
                rollout_id="rollout-a",
                total_steps=5,
                quality_status=QualityStatus.PASS,
                derived_status=DerivedStatus.DERIVED_READY,
                quality_profile_version="quality-a",
                alignment_profile_version="alignment-a",
                alignment_frequency_hz=30,
            )
        ]
    )
    snapshot = CatalogSnapshotAdapter(catalog=catalog, rollout_states=states).list_rollouts(
        project_id="project-a", dataset_id="dataset-a", lance_version="v1"
    )[0]
    exported = StepReaderAdapter(catalog).read_steps(
        project_id="project-a",
        dataset_id="dataset-a",
        rollout_id="rollout-a",
        lance_version="1",
        ranges=(StepRangeV1(start_step=1, end_step=4),),
    )

    assert snapshot.source_mcap_sha256 == hashlib.sha256(b"source-a").hexdigest()
    assert snapshot.converter_version == "converter-a"
    assert snapshot.alignment_profile_version == "alignment-a"
    assert [item.step_index for item in exported] == [1, 2, 3]
    assert exported[0].modalities == steps[1].modalities
    assert exported[0].source_timestamps_ns == steps[1].source_timestamps_ns


def _annotation_actor(actor_id: str, *capabilities: str) -> AnnotationActor:
    return AnnotationActor(
        actor_id=actor_id,
        capabilities=frozenset(capabilities),
        project_ids=frozenset({"project-a"}),
    )


def test_be09_adapter_reads_only_current_approved_revision_and_effective_restore() -> None:
    annotations = InMemoryAnnotationService()
    annotations.create_task(
        task_id="task-a",
        project_id="project-a",
        dataset_id="dataset-a",
        dataset_version=1,
        rollout_id="rollout-a",
        base_step_count=10,
    )
    annotator = _annotation_actor(
        "alice",
        "annotation_task.read",
        "annotation_task.claim",
        "annotation.save",
        "annotation.submit",
    )
    annotations.claim("task-a", annotator)
    annotations.save_draft(
        "task-a",
        annotator,
        [
            AnnotationOperation(
                operation_id="exclude",
                kind=OperationKind.EXCLUDE,
                start_step=1,
                end_step=5,
            ),
            AnnotationOperation(
                operation_id="restore",
                kind=OperationKind.RESTORE,
                start_step=2,
                end_step=3,
            ),
        ],
        expected_revision=0,
        if_match=annotations.get_task("task-a").etag,
        client_mutation_id="mutation-a",
    )
    submitted = annotations.submit(
        "task-a",
        annotator,
        expected_revision=1,
        if_match=annotations.get_task("task-a").etag,
    )
    annotations.review(
        "task-a",
        _annotation_actor("bob", "annotation_task.read", "annotation.review"),
        ReviewDecision.APPROVE,
        revision=1,
        if_match=submitted.etag,
    )
    adapter = ApprovedAnnotationSnapshotAdapter(annotations=annotations)

    approved = adapter.get_approved_revision(
        project_id="project-a",
        dataset_id="dataset-a",
        rollout_id="rollout-a",
        lance_version="v1",
    )
    assert approved is not None
    assert approved.annotation_revision == 1
    assert approved.excluded_step_ranges == (
        StepRangeV1(start_step=1, end_step=2),
        StepRangeV1(start_step=3, end_step=5),
    )

    annotations.save_draft(
        "task-a",
        annotator,
        [],
        expected_revision=1,
        if_match=annotations.get_task("task-a").etag,
        client_mutation_id="mutation-b",
    )
    assert (
        adapter.get_approved_revision(
            project_id="project-a",
            dataset_id="dataset-a",
            rollout_id="rollout-a",
            lance_version="v1",
        )
        is None
    )


def export_steps() -> list[ExportStepV1]:
    return [
        ExportStepV1(
            rollout_id="r1",
            step_index=index,
            timestamp_ns=index * 33_333_333,
            modalities={
                "camera.front": f"frame-{index}",
                "action": [index, index + 1],
            },
            source_timestamps_ns={
                "camera.front": (index * 33_333_333,),
                "action": (index * 33_333_333,),
            },
            time_error_ns={"camera.front": 0, "action": 0},
            valid={"camera.front": True, "action": True},
            repeated={"camera.front": False, "action": False},
        )
        for index in range(6)
    ]


def export_manifest() -> PublishedDatasetManifestV1:
    return publisher(
        [rollout("r1")],
        [approval("r1", (StepRangeV1(start_step=2, end_step=4),))],
    ).publish(request())


def test_deterministic_lerobot_export_preserves_synchronized_modalities() -> None:
    manifest = export_manifest()
    sink = InMemoryArtifactSink()
    coordinator = ExportCoordinator(
        source=InMemoryExportSource(export_steps()),
        sink=sink,
        exporters=[InMemoryLanceSnapshotExporter(), InMemoryLeRobotV3Exporter()],
    )
    result = coordinator.export(manifest, format=ExportFormat.LEROBOT_V3)

    with zipfile.ZipFile(io.BytesIO(sink.artifacts[result.artifact_uri])) as archive:
        assert all(item.compress_type == zipfile.ZIP_DEFLATED for item in archive.infolist())
        payload = json.loads(archive.read("lerobot-v3.json"))
    exported = payload["episodes"][0]["steps"]
    assert result.row_count == 4
    assert result.artifact_uri.endswith(".zip")
    assert result.media_type == "application/zip"
    assert [step["step_index"] for step in exported] == [0, 1, 4, 5]
    assert all(set(step["modalities"]) == {"camera.front", "action"} for step in exported)
    assert payload["manifest_content_hash"] == manifest.content_hash
    assert result.download_uri == sink.get_download_uri(result.artifact_uri)


class CorruptingAttemptReader(InMemoryArtifactSink):
    def read_attempt(self, attempt_id: str) -> bytes:
        super().read_attempt(attempt_id)
        return b"corrupt staged output"


def test_failed_validation_keeps_attempt_staging_but_no_downloadable_partial() -> None:
    manifest = export_manifest()
    sink = CorruptingAttemptReader()
    coordinator = ExportCoordinator(
        source=InMemoryExportSource(export_steps()),
        sink=sink,
        exporters=[InMemoryLeRobotV3Exporter()],
    )

    with pytest.raises(ProblemException) as captured:
        coordinator.export(manifest, format=ExportFormat.LEROBOT_V3, attempt_id="attempt-1")
    assert captured.value.problem.code == "LEROBOT_VALIDATION_FAILED"
    assert list(sink.attempts) == ["attempt-1"]
    assert sink.artifacts == {}
    assert sink.download_authorizations == {}


def test_export_retry_is_idempotent_after_atomic_promotion() -> None:
    manifest = export_manifest()
    sink = InMemoryArtifactSink()
    coordinator = ExportCoordinator(
        source=InMemoryExportSource(export_steps()),
        sink=sink,
        exporters=[InMemoryLeRobotV3Exporter()],
    )

    first = coordinator.export(manifest, format=ExportFormat.LEROBOT_V3, attempt_id="attempt-1")
    replay = coordinator.export(manifest, format=ExportFormat.LEROBOT_V3, attempt_id="attempt-1")
    another_attempt = coordinator.export(
        manifest, format=ExportFormat.LEROBOT_V3, attempt_id="attempt-2"
    )

    assert replay == first
    assert another_attempt.artifact_content_hash == first.artifact_content_hash
    assert another_attempt.artifact_uri == first.artifact_uri
    assert list(sink.attempts) == ["attempt-1"]
    assert len(sink.artifacts) == 1
    assert len(sink.download_authorizations) == 1


def test_incomplete_export_source_leaves_no_downloadable_artifact() -> None:
    manifest = publisher([rollout("r1")], [approval("r1")]).publish(request())
    sink = InMemoryArtifactSink()
    coordinator = ExportCoordinator(
        source=InMemoryExportSource(export_steps()[:-1]),
        sink=sink,
        exporters=[InMemoryLeRobotV3Exporter()],
    )

    with pytest.raises(ProblemException) as captured:
        coordinator.export(manifest, format=ExportFormat.LEROBOT_V3)
    assert captured.value.problem.code == "EXPORT_SOURCE_INCOMPLETE"
    assert sink.artifacts == {}
    assert sink.download_authorizations == {}


def test_unconfigured_exporter_is_a_retryable_worker_outage_not_a_product_501() -> None:
    manifest = export_manifest()
    with pytest.raises(ProblemException) as captured:
        ExportCoordinator(
            source=InMemoryExportSource(export_steps()),
            sink=InMemoryArtifactSink(),
            exporters=[],
        ).export(manifest, format=ExportFormat.LANCE_SNAPSHOT)
    assert captured.value.problem.status == 503
    assert captured.value.problem.code == "EXPORTER_UNAVAILABLE"


def test_phase_one_does_not_expose_hdf5_parquet_vlm_or_permanent_mp4() -> None:
    assert {item.value for item in ExportFormat} == {"lance_snapshot", "lerobot_v3"}


def test_openapi_fragment_has_resolved_export_and_publication_schemas() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    document = yaml.safe_load(
        (backend_root / "openapi" / "publishing.yaml").read_text(encoding="utf-8")
    )
    schemas = document["components"]["schemas"]
    references: set[str] = set()

    def collect(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if (
                    key == "$ref"
                    and isinstance(item, str)
                    and item.startswith("#/components/schemas/")
                ):
                    references.add(item.rsplit("/", 1)[-1])
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(document)
    assert references <= schemas.keys()
    export_path = "/api/v1/datasets/{dataset_id}/versions/{dataset_version}/exports"
    assert export_path in document["paths"]
    create_export = document["paths"][export_path]["post"]
    assert create_export["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ExportJobV1"
    }
    assert "501" not in create_export["responses"]
    for suffix, method, operation_id in (
        ("/{job_id}", "get", "getPublishedDatasetExportJob"),
        ("/{job_id}:cancel", "post", "cancelPublishedDatasetExportJob"),
        ("/{job_id}:retry", "post", "retryPublishedDatasetExportJob"),
        ("/{job_id}/download", "get", "authorizePublishedDatasetExportDownload"),
    ):
        operation = document["paths"][f"{export_path}{suffix}"][method]
        assert operation["operationId"] == operation_id
    assert "ExportResultV1" not in schemas
    assert schemas["ExportDatasetRequestV1"]["properties"]["format"]["enum"] == [
        "lance_snapshot",
        "lerobot_v3",
    ]


def test_runtime_export_job_contract_has_no_direct_result_or_501_response() -> None:
    runtime = create_app(settings=Settings(environment="test", runtime_backend="memory")).openapi()
    backend_root = Path(__file__).resolve().parents[2]
    formal = yaml.safe_load(
        (backend_root / "openapi" / "publishing.yaml").read_text(encoding="utf-8")
    )
    root = "/api/v1/datasets/{dataset_id}/versions/{dataset_version}/exports"
    expected = {
        root: ("post", "exportPublishedDatasetVersion", "202"),
        f"{root}/{{job_id}}": ("get", "getPublishedDatasetExportJob", "200"),
        f"{root}/{{job_id}}:cancel": ("post", "cancelPublishedDatasetExportJob", "200"),
        f"{root}/{{job_id}}:retry": ("post", "retryPublishedDatasetExportJob", "202"),
        f"{root}/{{job_id}}/download": ("get", "authorizePublishedDatasetExportDownload", "200"),
    }
    for path, (method, operation_id, success) in expected.items():
        operation = runtime["paths"][path][method]
        assert operation["operationId"] == operation_id
        assert success in operation["responses"]
        assert "501" not in operation["responses"]
        headers = operation["responses"][success].get("headers", {})
        assert "Cache-Control" in headers
    create_parameters = runtime["paths"][root]["post"]["parameters"]
    assert any(parameter["name"] == "Idempotency-Key" for parameter in create_parameters)
    create_schema = runtime["paths"][root]["post"]["responses"]["202"]["content"][
        "application/json"
    ]["schema"]
    assert create_schema == {"$ref": "#/components/schemas/ExportJobV1"}
    assert "ExportResultV1" not in runtime["components"]["schemas"]
    for schema_name in (
        "ExportDatasetRequestV1",
        "ExportJobResultV1",
        "ExportJobProgressV1",
        "ExportJobV1",
        "ExportDownloadAuthorizationV1",
    ):
        assert formal["components"]["schemas"][schema_name].get("required", []) == runtime[
            "components"
        ]["schemas"][schema_name].get("required", [])
    assert formal["components"]["schemas"]["ExportJobV1"]["properties"]["progress"] == {
        "$ref": "#/components/schemas/ExportJobProgressV1"
    }


def test_migration_enforces_append_only_publication_and_validated_promotion() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    migration = (backend_root / "migrations" / "publishing" / "0001_publishing.sql").read_text(
        encoding="utf-8"
    )

    assert "dataset_versions_immutable" in migration
    assert "publication_assets_immutable" in migration
    assert "published_exports_immutable" in migration
    assert "published_exports_require_validated_attempt" in migration
    assert "export attempt must be validated before promotion" in migration

    lineage = (
        backend_root / "migrations" / "publishing" / "0002_rollout_publication_region_lineage.sql"
    ).read_text(encoding="utf-8")
    assert "rollout_publication_lineage_immutable" in lineage
    assert "materialize_rollout_publication_lineage" in lineage
    assert "dashboard_publication_lineage_summary" in lineage
    assert "lineage_source IN ('FORWARD', 'BACKFILL')" in lineage


@pytest.mark.integration
def test_native_lerobot_v3_archive_reloads_with_real_parquet_reader() -> None:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    manifest = export_manifest()
    sink = InMemoryArtifactSink()
    result = ExportCoordinator(
        source=InMemoryExportSource(export_steps()),
        sink=sink,
        exporters=[LeRobotV3Exporter()],
    ).export(manifest, format=ExportFormat.LEROBOT_V3, attempt_id="native-lerobot")

    with zipfile.ZipFile(io.BytesIO(sink.artifacts[result.artifact_uri])) as archive:
        info = json.loads(archive.read("meta/info.json"))
        table = pq.read_table(pa.BufferReader(archive.read("data/chunk-000/file-000.parquet")))
        episodes = pq.read_table(
            pa.BufferReader(archive.read("meta/episodes/chunk-000/file-000.parquet"))
        )
    rows = table.to_pylist()
    assert info["codebase_version"] == "v3.0"
    assert info["video_path"] is None
    assert episodes.to_pylist()[0]["length"] == 4
    assert [row["frame_index"] for row in rows] == [0, 1, 2, 3]
    assert [row["hc.source_step_index"] for row in rows] == [0, 1, 4, 5]
    assert all(
        row["camera.front"] == f"frame-{source_step}"
        and row["action"] == [source_step, source_step + 1]
        for row, source_step in zip(rows, [0, 1, 4, 5], strict=True)
    )


@pytest.mark.integration
def test_native_lance_snapshot_reloads_with_real_lance_reader(tmp_path: Path) -> None:
    lance = pytest.importorskip("lance")
    manifest = export_manifest()
    sink = InMemoryArtifactSink()
    result = ExportCoordinator(
        source=InMemoryExportSource(export_steps()),
        sink=sink,
        exporters=[LanceSnapshotExporter()],
    ).export(manifest, format=ExportFormat.LANCE_SNAPSHOT, attempt_id="native-lance")

    with zipfile.ZipFile(io.BytesIO(sink.artifacts[result.artifact_uri])) as archive:
        archive.extractall(tmp_path)
    rows = lance.dataset(str(tmp_path / "aligned_steps.lance")).to_table().to_pylist()
    assert [(row["rollout_id"], row["step_index"]) for row in rows] == [
        ("r1", 0),
        ("r1", 1),
        ("r1", 4),
        ("r1", 5),
    ]
