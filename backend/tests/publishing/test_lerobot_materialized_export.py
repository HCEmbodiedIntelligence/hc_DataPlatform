from __future__ import annotations

import hashlib
import io
import json
import subprocess
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from hc_data_platform.aligned_media.models import AlignedMediaFrameReferenceV1
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.publishing.export_assets import ExportVideoSource
from hc_data_platform.publishing.exporters import LeRobotV3Exporter, _zip_files
from hc_data_platform.publishing.lerobot_export import DATA_PATH, EPISODES_PATH, LeRobotArchive
from hc_data_platform.publishing.memory import InMemoryArtifactSink, InMemoryExportSource
from hc_data_platform.publishing.models import (
    ExportFormat,
    ExportStepV1,
    PublishedDatasetManifestV1,
    PublishedRolloutV1,
)
from hc_data_platform.publishing.service import ExportCoordinator, canonical_json_bytes
from tests.publishing.test_publishing_service import export_manifest, export_steps


class Assets:
    def __init__(self, video: bytes) -> None:
        self.video = video
        self.reads = 0

    def episode_metadata(
        self,
        manifest: PublishedDatasetManifestV1,
        rollout: PublishedRolloutV1,
        source_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "task": "Put clothes into the washing machine",
            "robot_type": "unitree_g1",
            "tags": [
                {
                    "annotation_id": "tag-1",
                    "tag_id": "grasp",
                    "label": "抓取",
                    "start_step": 1,
                    "end_step": 5,
                    "path": ["grasp"],
                    "attributes": {"hand": "left"},
                }
            ],
            "revision_content_hash": "a" * 64,
        }

    def video_source(
        self,
        manifest: PublishedDatasetManifestV1,
        rollout: PublishedRolloutV1,
        reference: AlignedMediaFrameReferenceV1,
    ) -> ExportVideoSource:
        return ExportVideoSource(
            reference.object_key,
            hashlib.sha256(self.video).hexdigest(),
            len(self.video),
            16,
            16,
            6 / 30,
            12 / 30,
        )

    def read_video(self, source: ExportVideoSource) -> Iterable[bytes]:
        self.reads += 1
        return (self.video,)


@pytest.fixture
def portable_source(tmp_path: Path) -> tuple[Assets, list[ExportStepV1]]:
    video = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            "16x16",
            "-r",
            "30",
            "-i",
            "pipe:0",
            "-c:v",
            "libx264",
            "-crf",
            "0",
            "-threads",
            "1",
            str(video),
        ],
        input=b"".join(bytes([frame * 15]) * (16 * 16 * 3) for frame in range(14)),
        check=True,
    )
    assets = Assets(video.read_bytes())
    steps = []
    for step in export_steps():
        index = step.step_index
        camera = {
            "schema_version": "aligned-media-frame-ref/v1",
            "camera_id": "/camera/front/image",
            "artifact_id": "video-front",
            "object_key": "raw/camera.mp4",
            "frame_index": index + 6,
            "pts": index + 6,
            "pts_time_base_numerator": 1,
            "pts_time_base_denominator": 30,
            "timestamp_ns": index * 33_333_333,
            "valid": True,
            "placeholder": False,
            "repeated": False,
            "dropped": False,
            "alignment_version": "v1",
        }
        steps.append(
            step.model_copy(
                update={
                    "modalities": {
                        "/camera/front/image": camera,
                        "/humanoid/action": {
                            "names": ["left", "right"],
                            "values": [float(index), float(index + 1)],
                        },
                        "/humanoid/observation/state": {
                            "names": ["left", "right"],
                            "positions": [float(index + 2), float(index + 3)],
                        },
                    }
                }
            )
        )
    return assets, steps


def test_export_materializes_exact_frames_tags_statistics_and_standard_features(
    portable_source: tuple[Assets, list[ExportStepV1]], tmp_path: Path
) -> None:
    assets, steps = portable_source
    sink = InMemoryArtifactSink()
    coordinator = ExportCoordinator(
        source=InMemoryExportSource(steps), sink=sink, exporters=[LeRobotV3Exporter(assets)]
    )
    manifest = export_manifest()
    result = coordinator.export(manifest, format=ExportFormat.LEROBOT_V3, attempt_id="portable")
    assert "lerobot-materialized-v2/" in result.artifact_uri
    with zipfile.ZipFile(io.BytesIO(sink.artifacts[result.artifact_uri])) as archive:
        info = json.loads(archive.read("meta/info.json"))
        stats = json.loads(archive.read("meta/stats.json"))
        tags = json.loads(archive.read("meta/annotations.json"))["episodes"][0]["tags"]
        rows = pq.read_table(pa.BufferReader(archive.read(DATA_PATH))).to_pylist()
        episodes = pq.read_table(pa.BufferReader(archive.read(EPISODES_PATH))).to_pylist()
        tasks = pq.read_table(pa.BufferReader(archive.read("meta/tasks.parquet"))).to_pylist()
        camera = "observation.images.front"
        video_path = info["video_path"].format(video_key=camera, chunk_index=0, file_index=0)
        output = tmp_path / "export.mp4"
        output.write_bytes(archive.read(video_path))
    assert info["features"][camera]["dtype"] == "video"
    assert info["features"]["action"] == {
        "dtype": "float32",
        "shape": [2],
        "names": ["left", "right"],
    }
    assert info["robot_type"] == "unitree_g1"
    assert not any("/" in name for name in info["features"])
    assert [row["hc.source_step_index"] for row in rows] == [0, 1, 4, 5]
    assert [row["frame_index"] for row in rows] == [0, 1, 2, 3]
    assert [json.loads(row["hc.tag_labels"]) for row in rows] == [[], ["抓取"], ["抓取"], []]
    assert tags[0]["attributes"] == {"hand": "left"}
    assert tags[0]["ranges"] == [
        {"start_frame": 1, "end_frame": 2, "source_start_step": 1, "source_end_step": 2},
        {"start_frame": 2, "end_frame": 3, "source_start_step": 4, "source_end_step": 5},
    ]
    assert stats["action"]["mean"] == [2.5, 3.5]
    assert stats["action"]["count"] == stats[camera]["count"] == [4]
    assert tasks == [{"task_index": 0, "task": "Put clothes into the washing machine"}]
    assert episodes[0][f"videos/{camera}/to_timestamp"] == 4 / 30
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(output),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    actual = np.frombuffer(decoded, dtype=np.uint8).reshape(4, -1).mean(axis=1)
    np.testing.assert_allclose(actual, np.array([6, 7, 10, 11]) * 15, atol=3)
    reads = assets.reads
    replay = coordinator.export(
        manifest, format=ExportFormat.LEROBOT_V3, attempt_id="portable-again"
    )
    assert replay.artifact_content_hash == result.artifact_content_hash
    assert assets.reads == reads  # Reuse the fixed artifact without re-encoding the source.


@pytest.mark.parametrize("broken", ["video", "tags", "stats", "frame_tags", "episode_stats"])
def test_validation_rejects_missing_video_tags_or_statistics(
    portable_source: tuple[Assets, list[ExportStepV1]], broken: str
) -> None:
    assets, steps = portable_source
    manifest = export_manifest()
    selected = [step for step in steps if step.step_index in (0, 1, 4, 5)]
    exporter = LeRobotArchive(manifest, selected, assets)
    with zipfile.ZipFile(io.BytesIO(exporter.build())) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    if broken == "video":
        files.pop(next(name for name in files if name.endswith(".mp4")))
    elif broken == "tags":
        document = json.loads(files["meta/annotations.json"])
        document["episodes"][0]["tags"] = []
        files["meta/annotations.json"] = canonical_json_bytes(document)
    elif broken == "stats":
        files["meta/stats.json"] = b"{}"
    else:
        name = DATA_PATH if broken == "frame_tags" else EPISODES_PATH
        rows = pq.read_table(pa.BufferReader(files[name])).to_pylist()
        if broken == "frame_tags":
            rows[1]["hc.tag_labels"] = "[]"
        else:
            rows[0]["stats/action/mean"] = [0, 0]
        output = io.BytesIO()
        pq.write_table(pa.Table.from_pylist(rows), output)
        files[name] = output.getvalue()
    receipt = json.loads(files.pop("hc-publication-manifest.json"))
    receipt["files"] = {
        name: {"size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        for name, body in files.items()
    }
    files["hc-publication-manifest.json"] = canonical_json_bytes(receipt)
    with pytest.raises(ProblemException, match="LEROBOT_VALIDATION_FAILED"):
        exporter.validate(_zip_files(files))


def test_video_hash_mismatch_is_not_promoted(
    portable_source: tuple[Assets, list[ExportStepV1]], monkeypatch: pytest.MonkeyPatch
) -> None:
    assets, steps = portable_source
    monkeypatch.setattr(assets, "read_video", lambda source: (b"wrong",))
    sink = InMemoryArtifactSink()
    coordinator = ExportCoordinator(
        source=InMemoryExportSource(steps), sink=sink, exporters=[LeRobotV3Exporter(assets)]
    )
    with pytest.raises(ProblemException, match="EXPORT_SOURCE_HASH_MISMATCH"):
        coordinator.export(export_manifest(), format=ExportFormat.LEROBOT_V3)
    assert sink.artifacts == {}


def test_multiple_episodes_repeat_frames_and_exclude_unapproved_tags(
    portable_source: tuple[Assets, list[ExportStepV1]], tmp_path: Path
) -> None:
    assets, steps = portable_source
    manifest = export_manifest()
    approved = manifest.rollouts[0]
    unannotated = approved.model_copy(
        update={
            "rollout_id": "r2",
            "annotation_revision": None,
            "annotation_task_id": None,
            "annotation_submission_id": None,
        }
    )
    manifest = manifest.model_copy(update={"rollouts": (approved, unannotated)})
    # A lower-rate camera can legitimately repeat a frame at adjacent aligned steps.
    steps[1] = steps[1].model_copy(
        update={
            "modalities": {
                **steps[1].modalities,
                "/camera/front/image": {
                    **steps[0].modalities["/camera/front/image"],
                    "repeated": True,
                },
            }
        }
    )
    selected = [step for step in steps if step.step_index in (0, 1, 4, 5)]
    selected += [step.model_copy(update={"rollout_id": "r2"}) for step in selected]
    exporter = LeRobotArchive(manifest, selected, assets)
    content = exporter.build()
    exporter.validate(content)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        info = json.loads(archive.read("meta/info.json"))
        tags = json.loads(archive.read("meta/annotations.json"))["episodes"]
        rows = pq.read_table(pa.BufferReader(archive.read(DATA_PATH))).to_pylist()
        episodes = pq.read_table(pa.BufferReader(archive.read(EPISODES_PATH))).to_pylist()
        video = tmp_path / "repeated.mp4"
        video.write_bytes(archive.read("videos/observation.images.front/chunk-000/file-001.mp4"))
    assert info["total_episodes"] == 2 and info["total_frames"] == 8
    assert tags[0]["tags"] and tags[1]["tags"] == []
    assert all(row["hc.tag_labels"] == "[]" for row in rows[4:])
    assert [episode["dataset_from_index"] for episode in episodes] == [0, 4]
    assert [episode["dataset_to_index"] for episode in episodes] == [4, 8]
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(video),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    actual = np.frombuffer(decoded, dtype=np.uint8).reshape(4, -1).mean(axis=1)
    np.testing.assert_allclose(actual, np.array([6, 6, 10, 11]) * 15, atol=3)
