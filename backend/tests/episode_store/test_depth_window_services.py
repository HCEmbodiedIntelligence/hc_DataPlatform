"""Depth window -> real package/preview/collection services; no upload claim."""

import json
import os
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from hc_data_platform.episode_store.depth_window import DepthWindow, prepare_depth_window
from hc_data_platform.episode_store.models import digest
from hc_data_platform.episode_store.service import EpisodePackageService
from hc_data_platform.episode_store.validation import parquet_rows
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot, StepRecord
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_depth_window import START, forbid_quantization, make_depth
from tests.episode_store.test_writer import build_rgbd_package, identity


@pytest.mark.integration
def test_window_package_physical_read_preview_and_offline_delivery(services, tmp_path, monkeypatch):
    repository, storage = services
    ident = identity().model_copy(update={"organization_id": "depth-window-" + uuid4().hex})
    _, original, _, assets = build_rgbd_package(tmp_path, ident)
    depth = "observation.images.head_depth"
    media = make_depth(tmp_path / "parent")
    window = DepthWindow(1, 5, 4, 30, START + 40_000_000)
    forbid_quantization(monkeypatch)
    prepared = prepare_depth_window(media, window, tmp_path / "window")
    assets = replace(assets, videos={**assets.videos, depth: prepared})
    source = original.model_copy(
        update={
            "configuration_sha256": digest(
                {
                    "window": asdict(window),
                    "parent_video_sha256": media.sha256,
                    "evidence": [entry.model_dump(mode="json") for entry in media.evidence_files],
                    "materialization": json.loads(
                        (prepared.evidence_root / "report.json").read_bytes()
                    )["window"],
                }
            )
        }
    )
    provenance = list(parquet_rows(prepared.evidence_root / "frames.parquet"))
    rows = [
        StepRecord(
            rollout_id=ident.rollout_id,
            step_index=i,
            timestamp_ns=window.target_time(i),
            modalities={
                "observation.state": [0.1 + i * 1e-12],
                "action": [float(i)],
                "observation.images.head_rgb": {"frame_index": i},
                depth: {"source_frame_index": row["parent_frame_index"]},
            },
            source_timestamps_ns={depth: () if row["missing"] else (row["source_timestamp_ns"],)},
            time_error_ns={depth: row["time_error_ns"]},
            repeated={depth: row["repeated"]},
            valid={key: key != depth or not row["missing"] for key in original.required_features},
            sample_valid=not row["missing"],
        )
        for i, row in enumerate(provenance)
    ]
    schema = DatasetSchemaSnapshot.create(
        project_id=ident.project_id,
        dataset_id=ident.dataset_id,
        schema_snapshot_id=source.schema_snapshot_id,
        frequency_hz=30,
        fields=dict.fromkeys(original.required_features, "json"),
    )
    service = EpisodePackageService(repository.connections, storage, tmp_path / "service")
    try:
        with scoped(source):
            with repository.transaction() as cursor:
                cursor.execute(
                    "INSERT INTO registry.organization_projects VALUES (%s,%s,%s)",
                    (ident.organization_id, ident.project_id, "synthetic window"),
                )
            version, ready = service.write_and_publish(
                source,
                rows,
                assets,
                schema=schema,
                metadata={"task": "synthetic depth window service contract"},
            )
            assert ready.step_count == 4
            assert "lance_version" not in ready.model_dump_json()
            package = service.files.open(ident, source.data_version_id)
            for key, item in assets.videos.items():
                assert package.entries[f"videos/{key}/chunk-000/file-000.mp4"].sha256 == item.sha256
            result = service.depth.read(ident, source.data_version_id, depth, 1, unit="m")
            assert not result.flags.any()
            assert np.isnan(result.values).all()
            # Publication prepared actual SDK previews, with no visible READY
            # authorization relaxation or old catalog commit.
            with repository.transaction() as cursor:
                cursor.execute(
                    "SELECT status,receipt FROM episode_store.media_previews "
                    "WHERE organization_id=%s AND data_version_id=%s",
                    (ident.organization_id, source.data_version_id),
                )
                previews = cursor.fetchall()
            assert len(previews) == 2
            assert all(item[0] == "READY" for item in previews)
            depth_preview = next(item[1] for item in previews if item[1]["kind"] == "depth-preview")
            assert depth_preview["colorizer"]["implementation"] == "pyrealsense2.colorizer"
            reference = {
                "scope": "synthetic prepared inputs, real PG/MinIO/SDK package services",
                "data": ready.data.model_dump(mode="json"),
                "collection_version": version.version,
                "window": asdict(window),
                "selected_frames": [row["parent_frame_index"] for row in provenance],
                "source_seq": [row["source_seq"] for row in provenance],
                "source_scale_m_per_unit": 0.00025,
                "rgb_source_sha256": assets.videos["observation.images.head_rgb"].sha256,
                "depth_source_sha256": media.sha256,
                "model_depth_training_support": "NOT_VALIDATED",
            }
            if evidence_dir := os.environ.get("HC_G1_WRITER_EVIDENCE_ROOT"):
                destination = Path(evidence_dir) / "depth-window" / ready.data.data_version_id
                destination.mkdir(parents=True, exist_ok=False)
                for entry in package.manifest.files:
                    output = destination / entry.path
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with package.file(entry.path) as path:
                        output.write_bytes(path.read_bytes())
                reference["offline_package"] = str(destination)
                (Path(evidence_dir) / "depth-window-package.json").write_text(
                    json.dumps(reference, indent=2)
                )
    finally:
        service.close()
