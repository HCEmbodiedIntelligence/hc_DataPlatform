"""Exercise actual MCAP decoding, camera materialization and Lance before export."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import zipfile
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote, urlparse

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from mcap.reader import make_reader
from PIL import Image

from hc_data_platform.aligned_media.artifact_store import LocalAlignedMediaArtifactStore
from hc_data_platform.aligned_media.encoder import FFmpegMp4Encoder
from hc_data_platform.aligned_media.memory import InMemoryAlignedMediaRepository
from hc_data_platform.aligned_media.models import (
    AlignedMediaArtifactV1,
    AlignedMediaFrameReferenceV1,
    AlignedMediaGenerationRequestV1,
    AlignedMediaScopeV1,
    AlignmentStagingArtifactV1,
)
from hc_data_platform.aligned_media.service import AlignedMediaGenerationService
from hc_data_platform.aligned_media.staging import ArrowAlignedFrameReader
from hc_data_platform.alignment import AlignmentEngine, AlignmentProfileV1
from hc_data_platform.alignment.arrow_writer import ArrowFragmentWriter
from hc_data_platform.core.config import Settings
from hc_data_platform.ingest.manifest import preflight_manifest
from hc_data_platform.ingest.models import RolloutManifestV1
from hc_data_platform.lance_catalog import (
    AlignedFragmentManifestV1,
    DatasetSchemaSnapshot,
    InMemoryCatalogRepository,
    InMemoryDatasetWriterLock,
    LanceAdapter,
    LanceCatalogService,
    compute_fragment_hash,
)
from hc_data_platform.publishing.adapters import StepReaderAdapter
from hc_data_platform.publishing.export_assets import ExportVideoSource
from hc_data_platform.publishing.exporters import LeRobotV3Exporter
from hc_data_platform.publishing.memory import InMemoryArtifactSink
from hc_data_platform.publishing.models import (
    ExportDataStage,
    ExportFormat,
    PublishedDatasetManifestV1,
    PublishedRolloutV1,
    StepRangeV1,
)
from hc_data_platform.publishing.service import ExportCoordinator
from hc_data_platform.runtime import _ArrowStepSequence, _decoder
from hc_data_platform.verification import McapVerifier
from hc_data_platform.workflow.ingest_plan import _LocalProjectionSession
from hc_data_platform.workflow.models import IngestProjectionSourceV1
from hc_data_platform.workflow.projection_store import LocalProjectionArtifactStore

FIXTURES = Path(__file__).parents[1] / "system/wave2/data"


class LocalSource:
    def __init__(self, path: Path) -> None:
        self.path = path

    def open_reader(self, object_key: str) -> BinaryIO:
        return self.path.open("rb")


class MaterializedAssets:
    def __init__(self, root: Path, artifacts: list[AlignedMediaArtifactV1]) -> None:
        self.root = root
        self.artifacts = {item.artifact_id: item for item in artifacts}

    def episode_metadata(
        self,
        manifest: PublishedDatasetManifestV1,
        rollout: PublishedRolloutV1,
        source_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return {"tags": [], "robot_type": "test-robot", "task": "MCAP conversion verification"}

    def video_source(
        self,
        manifest: PublishedDatasetManifestV1,
        rollout: PublishedRolloutV1,
        reference: AlignedMediaFrameReferenceV1,
    ) -> ExportVideoSource:
        artifact = self.artifacts[reference.artifact_id]
        assert artifact.rollout_id == rollout.rollout_id
        assert artifact.media_object_key == reference.object_key
        assert artifact.width and artifact.height
        receipt = next(item for item in artifact.objects if item.key == reference.object_key)
        return ExportVideoSource(
            receipt.key,
            receipt.sha256,
            receipt.size,
            artifact.width,
            artifact.height,
            0,
            artifact.duration_seconds,
        )

    def read_video(self, source: ExportVideoSource) -> Iterable[bytes]:
        with (self.root / source.object_key).open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                yield chunk


def export_mcap_sample(root: Path, scenario: str) -> bytes:
    raw = RolloutManifestV1.model_validate_json(
        (FIXTURES / f"manifests/{scenario}.json").read_text()
    )
    preflight = preflight_manifest(raw)
    storage = LocalSource(FIXTURES / f"packages/{scenario}.mcap")
    decoder = _decoder(Settings())
    report = McapVerifier(storage, decoder).verify(
        rollout_id=raw.rollout_id,
        object_key="source.mcap",
        source_sha256=raw.sha256,
        required_topics=set(raw.expected_topics),
    )
    assert report.status.value == "RAW_VERIFIED", report
    source = IngestProjectionSourceV1(
        organization_id="export-check",
        project_id=raw.project_id,
        region_code="local",
        session_id="session",
        rollout_id=raw.rollout_id,
        data_package_id=raw.data_package_id,
        object_key="source.mcap",
        manifest_key="manifest.json",
        source_sha256=raw.sha256,
        manifest_fingerprint=preflight.manifest_fingerprint,
    )
    store = LocalProjectionArtifactStore(root / "projection")
    profile = AlignmentProfileV1(
        profile_id="mcap-30hz", required_modalities=frozenset(raw.actual_topics)
    )
    with _LocalProjectionSession(
        source=source,
        preflight=preflight,
        storage=storage,
        decoder=decoder,
        maximum_messages=1000,
        staging_root=root / "source",
        artifact_store=store,
    ) as session:
        data = session.alignment_data
        alignment = AlignmentEngine().align_stream_to_writer(
            rollout_id=data.rollout_id,
            source_sha256=data.source_sha256,
            attempt_id=data.attempt_id,
            start_ns=data.start_ns,
            end_ns=data.end_ns,
            stream_kinds={key: value.kind for key, value in data.streams.items()},
            samples=session.alignment_samples(),
            profile=profile,
            writer=ArrowFragmentWriter(root / "alignment"),
        )
    assert alignment.row_count == 30
    staged = Path(unquote(urlparse(alignment.staging_uri).path))
    digest = hashlib.sha256(staged.read_bytes()).hexdigest()
    size = store.publish_file("aligned.arrow", staged, sha256=digest)
    now = datetime.now(timezone.utc)
    staging = AlignmentStagingArtifactV1(
        object_key="aligned.arrow",
        content_sha256=digest,
        size_bytes=size,
        row_count=30,
        alignment_version="mcap-30hz/1",
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    media_repository = InMemoryAlignedMediaRepository()
    media_root = root / "objects"
    service = AlignedMediaGenerationService(
        frame_reader=ArrowAlignedFrameReader(store),
        encoder=FFmpegMp4Encoder(root / "encode"),
        repository=media_repository,
        store=LocalAlignedMediaArtifactStore(media_root),
    )
    scope = AlignedMediaScopeV1(
        organization_id=source.organization_id,
        project_id=source.project_id,
        region_code=source.region_code,
    )
    artifacts = [
        service.generate(
            scope,
            AlignedMediaGenerationRequestV1(
                project_id=raw.project_id,
                dataset_id="dataset-mcap",
                rollout_id=raw.rollout_id,
                expected_dataset_version=1,
                camera_id=camera.topic,
                source_sha256=raw.sha256,
                alignment=staging,
            ),
        )
        for camera in raw.cameras
    ]
    assert all(item.placeholder_count == 0 for item in artifacts)
    steps = tuple(_ArrowStepSequence(alignment.staging_uri, 30, media_artifacts=artifacts))
    schema = DatasetSchemaSnapshot.create(
        project_id=raw.project_id,
        dataset_id="dataset-mcap",
        schema_snapshot_id="mcap-schema",
        frequency_hz=30,
        fields={name: "json" for name in raw.actual_topics},
    )
    catalog = LanceCatalogService(
        LanceAdapter(root / "lance"), InMemoryCatalogRepository(), InMemoryDatasetWriterLock()
    )
    catalog.register_schema(schema)
    version, _ = catalog.commit_fragment(
        AlignedFragmentManifestV1(
            project_id=raw.project_id,
            dataset_id=schema.dataset_id,
            schema_snapshot_id=schema.schema_snapshot_id,
            schema_fingerprint=schema.fingerprint,
            frequency_hz=30,
            rollout_id=raw.rollout_id,
            source_sha256=raw.sha256,
            converter_version="be07-align/1",
            attempt_id="mcap-export-check",
            fragment_uri=alignment.staging_uri,
            step_count=30,
            content_hash=compute_fragment_hash(steps),
        ),
        steps,
    )
    media_repository.mark_dataset_committed(
        scope=scope, artifact_ids=tuple(item.artifact_id for item in artifacts), now=now
    )
    manifest = PublishedDatasetManifestV1(
        project_id=raw.project_id,
        dataset_id=schema.dataset_id,
        dataset_version="mcap-v1",
        base_lance_version=str(version.version),
        created_at=now,
        data_stage=ExportDataStage.DATASET,
        content_hash=raw.sha256,
        annotations_uri="unused/annotations",
        annotations_content_sha256="a" * 64,
        training_manifest_uri="unused/training",
        training_manifest_content_sha256="b" * 64,
        rollouts=(
            PublishedRolloutV1(
                rollout_id=raw.rollout_id,
                source_mcap_sha256=raw.sha256,
                base_lance_version=str(version.version),
                annotation_revision=None,
                quality_profile_version="mcap-quality",
                alignment_profile_version=profile.profile_id,
                alignment_frequency_hz=30,
                converter_version=profile.converter_version,
                total_steps=30,
                included_step_ranges=(StepRangeV1(start_step=0, end_step=30),),
            ),
        ),
    )
    sink = InMemoryArtifactSink()
    result = ExportCoordinator(
        source=StepReaderAdapter(catalog),
        sink=sink,
        exporters=[LeRobotV3Exporter(MaterializedAssets(media_root, artifacts))],
    ).export(manifest, format=ExportFormat.LEROBOT_V3, attempt_id="mcap-export-check")
    return sink.artifacts[result.artifact_uri]


@pytest.mark.integration
@pytest.mark.parametrize("scenario,cameras", [("legal", 1), ("multi_camera", 2)])
def test_mcap_decodes_materializes_commits_and_exports_native_lerobot(
    tmp_path: Path, scenario: str, cameras: int
) -> None:
    content = export_mcap_sample(tmp_path, scenario)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        info = json.loads(archive.read("meta/info.json"))
        rows = pq.read_table(
            pa.BufferReader(archive.read("data/chunk-000/file-000.parquet"))
        ).to_pylist()
        assert len([name for name in archive.namelist() if name.endswith(".mp4")]) == cameras
        for topic, feature in info["hc.feature_mapping"].items():
            if not topic.startswith("/camera/"):
                continue
            path = tmp_path / "export.mp4"
            path.write_bytes(
                archive.read(
                    info["video_path"].format(
                        video_key=feature,
                        chunk_index=0,
                        file_index=0,
                    )
                )
            )
            decoded = subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(path),
                    "-pix_fmt",
                    "rgb24",
                    "-f",
                    "rawvideo",
                    "pipe:1",
                ],
                capture_output=True,
                check=True,
            ).stdout
            actual = np.frombuffer(decoded, dtype=np.uint8).reshape(30, 24, 32, 3).mean(axis=(1, 2))
            with (FIXTURES / f"packages/{scenario}.mcap").open("rb") as source_file:
                expected = [
                    np.asarray(
                        Image.open(
                            io.BytesIO(base64.b64decode(json.loads(message.data)["data_base64"]))
                        ).convert("RGB")
                    ).mean(axis=(0, 1))
                    for _, channel, message in make_reader(source_file).iter_messages()
                    if channel.topic == topic
                ]
            # JPEG -> CRF20 canonical H.264 -> CRF18 export is lossy. Allow
            # local codec rounding, while rejecting the systematic 11-level
            # gray shift caused by relabeling full-range JPEG as limited-range.
            np.testing.assert_allclose(actual, expected, atol=8)
            assert np.abs(actual - expected).mean() < 3
    assert info["total_frames"] == 30
    assert info["features"]["action"]["dtype"] == "float32"
    assert info["features"]["observation.state"]["shape"] == [2]
    np.testing.assert_allclose(
        [row["action"] for row in rows], [[i / 29, 1 - i / 29] for i in range(30)], atol=1e-6
    )
    np.testing.assert_allclose(
        [row["observation.state"] for row in rows],
        [[i / 100, -i / 100] for i in range(30)],
        atol=1e-6,
    )
    destination = os.environ.get("HC_MCAP_EXPORT_CHECK_OUTPUT")
    if destination:
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        (output / f"mcap-{scenario}.lerobot-v3.zip").write_bytes(content)
