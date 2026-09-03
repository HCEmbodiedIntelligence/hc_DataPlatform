"""Prepare a native Unitree G1 recording for the platform's v2 upload API.

Native capture deliberately keeps camera video in MP4 and sensor telemetry in
MCAP.  Parquet is not required on the robot.  If a collector emits JSON Lines,
this tool can encode those JSON messages into the sensor MCAP without touching
the MP4 bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from mcap.writer import CompressionType, Writer

from hc_data_platform.continuous_recordings.asset_models import (
    CameraRecordingConfigV1,
    CreateRecordingUploadCommand,
    RecordingAssetManifestV1,
    RecordingAssetRole,
    RecordingConfigurationV1,
)
from hc_data_platform.ingest.cli import DEFAULT_PART_SIZE, effective_part_size
from hc_data_platform.ingest.ports import crc64_ecma

from .hf_unitree_g1_to_mcap import (
    ACTION_SCHEMA,
    ACTION_TOPIC,
    BASE_POSE_TOPIC,
    BASE_TARGET_TOPIC,
    CAMERAS,
    END_EFFECTOR_ACTION_TOPIC,
    END_EFFECTOR_STATE_TOPIC,
    GRIPPER_COMMAND_TOPIC,
    GRIPPER_STATE_TOPIC,
    JOINT_SCHEMA,
    JOINT_TOPIC,
    POSE_SCHEMA,
    STATE_TOPIC,
)

RECORDER_VERSION = "hc-native-unitree-g1/1.0.0"
CONFIG_PATH = "recording/config.json"
SENSOR_PATH = "sensors/robot.mcap"
UPLOAD_COMMAND_PATH = "recording-upload.json"

_ACTION_TOPICS = {
    ACTION_TOPIC,
    END_EFFECTOR_ACTION_TOPIC,
    END_EFFECTOR_STATE_TOPIC,
    GRIPPER_COMMAND_TOPIC,
    GRIPPER_STATE_TOPIC,
}
_POSE_TOPICS = {BASE_POSE_TOPIC, BASE_TARGET_TOPIC}
_JOINT_TOPICS = {JOINT_TOPIC, STATE_TOPIC}
_KNOWN_TOPIC_SCHEMAS = {
    **{topic: ("hc.robot.Action", ACTION_SCHEMA) for topic in _ACTION_TOPICS},
    **{topic: ("hc.robot.Pose", POSE_SCHEMA) for topic in _POSE_TOPICS},
    **{topic: ("hc.robot.JointState", JOINT_SCHEMA) for topic in _JOINT_TOPICS},
}


@dataclass(frozen=True, slots=True)
class CameraInput:
    camera_id: str
    source: Path


@dataclass(frozen=True, slots=True)
class VideoProbe:
    codec: str
    fps: float
    width: int
    height: int
    duration_seconds: float
    time_base_numerator: int
    time_base_denominator: int


@dataclass(frozen=True, slots=True)
class PrepareRequest:
    output_dir: Path
    project_id: str
    collection_task_id: str
    robot_id: str
    device_id: str
    capture_started_at: datetime
    telemetry: Path
    cameras: tuple[CameraInput, ...]
    collection_job_id: str | None = None
    recording_id: str | None = None
    part_size: int = DEFAULT_PART_SIZE


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _parse_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("time must be ISO-8601") from exc
    if result.tzinfo is None:
        raise argparse.ArgumentTypeError("time must include a timezone")
    return result.astimezone(timezone.utc)


def parse_camera_input(value: str) -> CameraInput:
    camera_id, separator, raw_path = value.partition("=")
    camera_id = camera_id.strip()
    if not separator or not re.fullmatch(r"[A-Za-z0-9._-]+", camera_id) or not raw_path.strip():
        raise argparse.ArgumentTypeError("camera must use CAMERA_ID=/path/to/video.mp4")
    return CameraInput(camera_id=camera_id, source=Path(raw_path.strip()))


def _positive_rational(value: str, *, field: str) -> tuple[int, int]:
    numerator_text, separator, denominator_text = value.partition("/")
    try:
        numerator = int(numerator_text)
        denominator = int(denominator_text) if separator else 1
    except ValueError as exc:
        raise RuntimeError(f"ffprobe returned an invalid {field}") from exc
    if numerator <= 0 or denominator <= 0:
        raise RuntimeError(f"ffprobe returned a non-positive {field}")
    return numerator, denominator


def probe_mp4(
    path: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> VideoProbe:
    if not path.is_file() or path.suffix.lower() != ".mp4":
        raise ValueError(f"camera input must be an existing .mp4 file: {path}")
    completed = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            (
                "format=format_name,duration:"
                "stream=codec_type,codec_name,width,height,avg_frame_rate,time_base,duration"
            ),
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        payload = json.loads(completed.stdout)
        format_info = cast(dict[str, Any], payload["format"])
        video_streams = [
            item
            for item in cast(list[dict[str, Any]], payload["streams"])
            if item.get("codec_type") == "video"
        ]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {path}") from exc
    if "mp4" not in str(format_info.get("format_name", "")).lower() or len(video_streams) != 1:
        raise ValueError(f"camera input must contain exactly one MP4 video stream: {path}")
    stream = video_streams[0]
    fps_numerator, fps_denominator = _positive_rational(
        str(stream.get("avg_frame_rate", "")), field="frame rate"
    )
    time_numerator, time_denominator = _positive_rational(
        str(stream.get("time_base", "")), field="time base"
    )
    duration_value = stream.get("duration") or format_info.get("duration")
    try:
        if not isinstance(duration_value, (str, int, float)):
            raise TypeError("duration is not numeric")
        duration = float(duration_value)
        width = int(stream["width"])
        height = int(stream["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"ffprobe omitted required video facts for {path}") from exc
    codec = str(stream.get("codec_name", "")).strip().lower()
    fps = fps_numerator / fps_denominator
    if not codec or not math.isfinite(duration) or duration <= 0 or width < 2 or height < 2:
        raise RuntimeError(f"ffprobe returned invalid video facts for {path}")
    return VideoProbe(
        codec=codec,
        fps=fps,
        width=width,
        height=height,
        duration_seconds=duration,
        time_base_numerator=time_numerator,
        time_base_denominator=time_denominator,
    )


def _telemetry_rows(path: Path) -> Iterable[tuple[int, str, dict[str, Any]]]:
    previous_offset = -1
    with path.open(encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"telemetry JSONL line {line_number} is invalid JSON") from exc
            if not isinstance(row, dict):
                raise ValueError(f"telemetry JSONL line {line_number} must be an object")
            offset_ns = row.get("offset_ns")
            topic = row.get("topic")
            data = row.get("data")
            if not isinstance(offset_ns, int) or isinstance(offset_ns, bool) or offset_ns < 0:
                raise ValueError(f"telemetry JSONL line {line_number} has invalid offset_ns")
            if offset_ns < previous_offset:
                raise ValueError("telemetry JSONL offset_ns values must be non-decreasing")
            if topic not in _KNOWN_TOPIC_SCHEMAS:
                raise ValueError(f"telemetry JSONL line {line_number} has unsupported topic")
            if not isinstance(data, dict):
                raise ValueError(f"telemetry JSONL line {line_number} data must be an object")
            previous_offset = offset_ns
            yield offset_ns, cast(str, topic), cast(dict[str, Any], data)


def write_sensor_mcap_from_jsonl(source: Path, destination: Path) -> int:
    if not source.is_file():
        raise ValueError(f"telemetry source does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    sequence_by_topic: dict[str, int] = {}
    with destination.open("wb") as output:
        writer = Writer(
            output,
            compression=CompressionType.ZSTD,
            chunk_size=8 * 1024 * 1024,
            enable_crcs=True,
            enable_data_crcs=True,
        )
        writer.start(profile="hc-native-sensors-json/v1", library=RECORDER_VERSION)
        schema_ids: dict[str, int] = {}
        channel_ids: dict[str, int] = {}
        for offset_ns, topic, data in _telemetry_rows(source):
            schema_name, schema = _KNOWN_TOPIC_SCHEMAS[topic]
            if schema_name not in schema_ids:
                schema_ids[schema_name] = writer.register_schema(
                    schema_name, "jsonschema", _json_bytes(schema)
                )
            if topic not in channel_ids:
                channel_ids[topic] = writer.register_channel(topic, "json", schema_ids[schema_name])
            sequence = sequence_by_topic.get(topic, 0) + 1
            sequence_by_topic[topic] = sequence
            writer.add_message(
                channel_ids[topic],
                log_time=offset_ns,
                publish_time=offset_ns,
                sequence=sequence,
                data=_json_bytes(data),
            )
            count += 1
        writer.finish()
    if count == 0:
        destination.unlink(missing_ok=True)
        raise ValueError("telemetry JSONL has no messages")
    return count


def _file_facts(path: Path, *, part_size: int) -> dict[str, int | str]:
    digest = hashlib.sha256()
    crc64 = 0
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
            crc64 = crc64_ecma(chunk, crc64)
    if size < 1:
        raise ValueError(f"recording asset is empty: {path}")
    selected_part_size = effective_part_size(file_size=size, requested_part_size=part_size)
    return {
        "size": size,
        "sha256": digest.hexdigest(),
        "crc64": crc64,
        "part_count": math.ceil(size / selected_part_size),
    }


def _identity(request: PrepareRequest, video_hashes: tuple[str, ...]) -> dict[str, str]:
    seed = "|".join(
        (
            request.project_id,
            request.collection_task_id,
            request.robot_id,
            request.device_id,
            request.capture_started_at.isoformat(),
            *video_hashes,
        )
    )
    digest = hashlib.sha256(seed.encode()).hexdigest()[:16]
    return {
        "recording_id": request.recording_id or f"native-g1-recording-{digest}",
        "rollout_id": f"native-g1-rollout-{digest}",
        "data_package_id": f"native-g1-package-{digest}",
        "collection_job_id": request.collection_job_id or f"native-g1-job-{digest}",
    }


def _camera_topic(camera_id: str) -> str:
    known = {camera.camera_id: camera.topic for camera in CAMERAS}
    return known.get(camera_id, f"/camera/{camera_id}/image")


def prepare_recording(
    request: PrepareRequest,
    *,
    video_probe: Callable[[Path], VideoProbe] = probe_mp4,
) -> Path:
    if request.capture_started_at.tzinfo is None:
        raise ValueError("capture_started_at must include a timezone")
    if not request.cameras:
        raise ValueError("at least one camera MP4 is required")
    camera_ids = [camera.camera_id for camera in request.cameras]
    if len(camera_ids) != len(set(camera_ids)):
        raise ValueError("camera ids must be unique")
    output_dir = request.output_dir.expanduser().resolve()
    if output_dir.exists():
        raise ValueError(f"output directory already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        probes: dict[str, VideoProbe] = {}
        video_assets: list[RecordingAssetManifestV1] = []
        video_hashes: list[str] = []
        for camera in request.cameras:
            source = camera.source.expanduser().resolve()
            probes[camera.camera_id] = video_probe(source)
            relative = f"videos/{camera.camera_id}.mp4"
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            facts = _file_facts(destination, part_size=request.part_size)
            video_hashes.append(cast(str, facts["sha256"]))
            video_assets.append(
                RecordingAssetManifestV1(
                    path=relative,
                    role=RecordingAssetRole.RAW_VIDEO,
                    camera_id=camera.camera_id,
                    media_type="video/mp4",
                    **facts,
                )
            )

        sensor_destination = staging / SENSOR_PATH
        telemetry = request.telemetry.expanduser().resolve()
        if telemetry.suffix.lower() == ".jsonl":
            write_sensor_mcap_from_jsonl(telemetry, sensor_destination)
        elif telemetry.suffix.lower() == ".mcap" and telemetry.is_file():
            sensor_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(telemetry, sensor_destination)
        else:
            raise ValueError("telemetry must be an existing .mcap or .jsonl file")

        recording_config = RecordingConfigurationV1(
            recorder_version=RECORDER_VERSION,
            primary_clock_domain="recording-monotonic",
            cameras=tuple(
                CameraRecordingConfigV1(
                    camera_id=camera.camera_id,
                    topic=_camera_topic(camera.camera_id),
                    fps=probes[camera.camera_id].fps,
                    width=probes[camera.camera_id].width,
                    height=probes[camera.camera_id].height,
                    codec=probes[camera.camera_id].codec,
                    clock_domain="recording-monotonic",
                    time_base_numerator=probes[camera.camera_id].time_base_numerator,
                    time_base_denominator=probes[camera.camera_id].time_base_denominator,
                )
                for camera in request.cameras
            ),
        )
        config_destination = staging / CONFIG_PATH
        config_destination.parent.mkdir(parents=True, exist_ok=True)
        config_destination.write_text(
            json.dumps(recording_config.model_dump(mode="json"), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        sensor_facts = _file_facts(sensor_destination, part_size=request.part_size)
        config_facts = _file_facts(config_destination, part_size=request.part_size)
        duration = max(probe.duration_seconds for probe in probes.values())
        capture_started_at = request.capture_started_at.astimezone(timezone.utc)
        identifiers = _identity(request, tuple(video_hashes))
        command = CreateRecordingUploadCommand(
            **identifiers,
            collection_task_id=request.collection_task_id,
            robot_id=request.robot_id,
            device_id=request.device_id,
            capture_started_at=capture_started_at,
            capture_ended_at=capture_started_at + timedelta(seconds=duration),
            recording_config=recording_config,
            assets=(
                *video_assets,
                RecordingAssetManifestV1(
                    path=SENSOR_PATH,
                    role=RecordingAssetRole.SENSOR_DATA,
                    media_type="application/x-mcap",
                    **sensor_facts,
                ),
                RecordingAssetManifestV1(
                    path=CONFIG_PATH,
                    role=RecordingAssetRole.RECORDING_CONFIG,
                    media_type="application/json",
                    **config_facts,
                ),
            ),
        )
        (staging / UPLOAD_COMMAND_PATH).write_text(
            json.dumps(command.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--collection-task-id", required=True)
    parser.add_argument("--collection-job-id")
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--recording-id")
    parser.add_argument("--capture-start", type=_parse_datetime, required=True)
    parser.add_argument("--telemetry", type=Path, required=True)
    parser.add_argument(
        "--camera",
        action="append",
        type=parse_camera_input,
        required=True,
        help="repeat CAMERA_ID=/path/to/video.mp4 for every camera",
    )
    parser.add_argument("--part-size-mib", type=int, default=DEFAULT_PART_SIZE // 1024**2)
    return parser


def run(args: argparse.Namespace) -> Path:
    if args.part_size_mib < 5:
        raise ValueError("--part-size-mib must be at least 5")
    return prepare_recording(
        PrepareRequest(
            output_dir=cast(Path, args.output_dir),
            project_id=cast(str, args.project_id),
            collection_task_id=cast(str, args.collection_task_id),
            collection_job_id=cast(str | None, args.collection_job_id),
            robot_id=cast(str, args.robot_id),
            device_id=cast(str, args.device_id),
            recording_id=cast(str | None, args.recording_id),
            capture_started_at=cast(datetime, args.capture_start),
            telemetry=cast(Path, args.telemetry),
            cameras=tuple(cast(list[CameraInput], args.camera)),
            part_size=int(args.part_size_mib) * 1024**2,
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = run(args)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}")
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
