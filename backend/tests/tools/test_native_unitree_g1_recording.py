from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from mcap.reader import make_reader

from hc_data_platform.continuous_recordings.asset_models import (
    CreateRecordingUploadCommand,
    RecordingAssetRole,
)
from hc_data_platform.tools.hf_unitree_g1_to_mcap import JOINT_TOPIC
from hc_data_platform.tools.native_unitree_g1_recording import (
    CONFIG_PATH,
    SENSOR_PATH,
    UPLOAD_COMMAND_PATH,
    CameraInput,
    PrepareRequest,
    VideoProbe,
    prepare_recording,
    write_sensor_mcap_from_jsonl,
)


def _probe(_path: Path) -> VideoProbe:
    return VideoProbe(
        codec="h264",
        fps=30.0,
        width=640,
        height=480,
        duration_seconds=2.5,
        time_base_numerator=1,
        time_base_denominator=90_000,
    )


def _telemetry(path: Path) -> Path:
    path.write_text(
        "\n".join(
            json.dumps(
                {
                    "offset_ns": index * 10_000_000,
                    "topic": JOINT_TOPIC,
                    "data": {"names": ["joint-a"], "positions": [float(index)]},
                }
            )
            for index in range(2)
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_prepares_mp4_and_mcap_without_parquet_or_video_reencoding(tmp_path: Path) -> None:
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"unchanged-native-mp4")
    output = prepare_recording(
        PrepareRequest(
            output_dir=tmp_path / "recording",
            project_id="project-a",
            collection_task_id="task-a",
            robot_id="robot-a",
            device_id="unitree-g1-a",
            capture_started_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
            telemetry=_telemetry(tmp_path / "telemetry.jsonl"),
            cameras=(CameraInput(camera_id="head_stereo_left", source=source_video),),
        ),
        video_probe=_probe,
    )

    assert (output / "videos/head_stereo_left.mp4").read_bytes() == source_video.read_bytes()
    assert not tuple(output.rglob("*.parquet"))
    command = CreateRecordingUploadCommand.model_validate_json(
        (output / UPLOAD_COMMAND_PATH).read_text(encoding="utf-8")
    )
    assert command.capture_ended_at.timestamp() - command.capture_started_at.timestamp() == 2.5
    assert command.recording_config.cameras[0].codec == "h264"
    assert {asset.role for asset in command.assets} == {
        RecordingAssetRole.RAW_VIDEO,
        RecordingAssetRole.SENSOR_DATA,
        RecordingAssetRole.RECORDING_CONFIG,
    }
    assert (output / CONFIG_PATH).is_file()
    with (output / SENSOR_PATH).open("rb") as stream:
        messages = list(make_reader(stream).iter_messages())
    assert [channel.topic for _schema, channel, _message in messages] == [
        JOINT_TOPIC,
        JOINT_TOPIC,
    ]


def test_jsonl_requires_monotonic_offsets(tmp_path: Path) -> None:
    source = tmp_path / "telemetry.jsonl"
    source.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "offset_ns": 2,
                        "topic": JOINT_TOPIC,
                        "data": {"names": [], "positions": []},
                    }
                ),
                json.dumps(
                    {
                        "offset_ns": 1,
                        "topic": JOINT_TOPIC,
                        "data": {"names": [], "positions": []},
                    }
                ),
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="non-decreasing"):
        write_sensor_mcap_from_jsonl(source, tmp_path / "sensor.mcap")


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "recording"
    output.mkdir()
    with pytest.raises(ValueError, match="already exists"):
        prepare_recording(
            PrepareRequest(
                output_dir=output,
                project_id="project-a",
                collection_task_id="task-a",
                robot_id="robot-a",
                device_id="unitree-g1-a",
                capture_started_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
                telemetry=tmp_path / "telemetry.jsonl",
                cameras=(CameraInput(camera_id="front", source=tmp_path / "front.mp4"),),
            ),
            video_probe=_probe,
        )
