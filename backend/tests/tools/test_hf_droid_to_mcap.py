from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

import pytest
from PIL import Image

from hc_data_platform.ingest.manifest import parse_manifest_bytes
from hc_data_platform.tools.hf_droid_to_mcap import (
    ACTION_TOPIC,
    CAMERAS,
    JOINT_TOPIC,
    RECORDING_CONFIG_FILE_NAME,
    REQUIRED_TOPICS,
    STATE_TOPIC,
    ConversionRequest,
    EpisodeData,
    write_package,
)
from hc_data_platform.verification.ports import RegisteredDecoderProbe
from hc_data_platform.workflow.ingest_plan import PostgresIngestWorkflowInputResolver


class LocalStorage:
    def open_reader(self, object_key: str) -> BinaryIO:
        return Path(object_key).open("rb")


def test_revision_url_keeps_repository_namespace_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def request_json(url: str) -> dict[str, str]:
        seen.append(url)
        return {"sha": "1234567890abcdef"}

    monkeypatch.setattr("hc_data_platform.tools.hf_droid_to_mcap._request_json", request_json)

    from hc_data_platform.tools.hf_droid_to_mcap import _resolved_revision

    assert _resolved_revision("lerobot/droid_100", "main") == "1234567890abcdef"
    assert seen == ["https://huggingface.co/api/datasets/lerobot/droid_100/revision/main"]


def test_resolved_revision_accepts_an_immutable_sha_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_request(_url: str) -> dict[str, str]:
        raise AssertionError("an immutable revision must not call the network")

    monkeypatch.setattr(
        "hc_data_platform.tools.hf_droid_to_mcap._request_json",
        unexpected_request,
    )

    from hc_data_platform.tools.hf_droid_to_mcap import _resolved_revision

    assert _resolved_revision("aractingi/droid_100", "A" * 40) == "a" * 40


def _jpeg(path: Path, color: tuple[int, int, int]) -> Path:
    Image.new("RGB", (32, 24), color=color).save(path, format="JPEG")
    return path


def test_writes_three_camera_platform_package_and_projects_real_values(tmp_path: Path) -> None:
    frames: dict[str, tuple[Path, ...]] = {}
    for camera_index, camera in enumerate(CAMERAS):
        camera_dir = tmp_path / camera.camera_id
        camera_dir.mkdir()
        frames[camera.feature_key] = tuple(
            _jpeg(camera_dir / f"{frame_index}.jpg", (camera_index * 60, frame_index * 80, 40))
            for frame_index in range(2)
        )
    episode = EpisodeData(
        fps=15.0,
        relative_timestamps_ns=(0, 66_666_667),
        states=((0.1, 0.2, 0.3), (0.4, 0.5, 0.6)),
        actions=((1.0, 2.0, 3.0), (4.0, 5.0, 6.0)),
        source_indexes=(0, 1),
        task_index=7,
    )
    request = ConversionRequest(
        project_id="project-a",
        collection_task_id="14d16ba1-d95a-5ee3-aaa7-7b7d78091b52",
        episode_index=0,
        repository="lerobot/droid_100",
        requested_revision="main",
        resolved_revision="a" * 40,
        robot_id="droid-franka",
        output_root=tmp_path / "output",
        capture_start=datetime(2024, 3, 20, tzinfo=timezone.utc),
    )

    package = write_package(request=request, episode=episode, camera_frames=frames)
    manifest_path = package / "rollout_manifest.json"
    raw_path = package / "recording.mcap"
    recording_config_path = package / RECORDING_CONFIG_FILE_NAME
    preflight = parse_manifest_bytes(manifest_path.read_bytes())
    manifest = preflight.manifest

    assert manifest.task_id == request.collection_task_id
    assert manifest.project_id == request.project_id
    assert manifest.source_recording is not None
    assert manifest.source_recording.repository == request.repository
    assert manifest.source_recording.resolved_revision == request.resolved_revision
    assert manifest.source_recording.episode_index == request.episode_index
    assert preflight.source_fingerprint == manifest.source_fingerprint
    assert tuple(manifest.expected_topics) == REQUIRED_TOPICS
    assert [camera.camera_id for camera in manifest.cameras] == [
        "wrist",
        "exterior_1",
        "exterior_2",
    ]
    assert {camera.topic for camera in manifest.cameras}.issubset(manifest.actual_topics)
    assert JOINT_TOPIC in manifest.actual_topics
    recording_config = json.loads(recording_config_path.read_text(encoding="utf-8"))
    assert recording_config["clock"] == {
        "basis": "source_episode_timestamps",
        "nominal_frequency_hz": 15.0,
        "frame_count": 2,
        "start_offset_ns": 0,
        "last_sample_offset_ns": 66_666_667,
        "observed_interval_ns": {
            "minimum": 66_666_667,
            "median": 66_666_667,
            "maximum": 66_666_667,
        },
    }
    assert recording_config["robot_model"] == {
        "source_robot_type": None,
        "dataset_declares_urdf": False,
        "urdf": None,
        "status": "NOT_PRESENT_IN_SOURCE_RECORDING_METADATA",
    }
    assert any(
        item.path == RECORDING_CONFIG_FILE_NAME and item.role == "AUXILIARY"
        for item in manifest.files
    )

    decoder = RegisteredDecoderProbe(
        {("json", "jsonschema"): lambda _schema, message: json.loads(message)}
    )
    resolver = PostgresIngestWorkflowInputResolver(
        lambda: None,
        LocalStorage(),
        None,  # type: ignore[arg-type]
        decoder=decoder,
    )
    quality, alignment = resolver._project_mcap(  # noqa: SLF001
        object_key=str(raw_path),
        rollout_id=manifest.rollout_id,
        source_sha256=manifest.sha256,
        preflight=preflight,
    )

    assert set(quality.images) == {camera.topic for camera in CAMERAS}
    assert all(len(samples) == 2 for samples in quality.images.values())
    assert alignment.streams[JOINT_TOPIC].samples[0].value == {
        "names": ["joint_0", "joint_1", "joint_2"],
        "positions": [0.1, 0.2, 0.3],
    }
    assert alignment.streams[STATE_TOPIC].samples[1].value == {
        "names": ["joint_0", "joint_1", "joint_2"],
        "positions": [0.4, 0.5, 0.6],
    }
    assert alignment.streams[ACTION_TOPIC].samples[1].value == {
        "names": ["action_0", "action_1", "action_2"],
        "values": [4.0, 5.0, 6.0],
    }
