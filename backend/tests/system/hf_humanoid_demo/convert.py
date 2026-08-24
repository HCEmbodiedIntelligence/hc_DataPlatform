"""Download one pinned Unitree G1 episode and convert it to the platform MCAP contract."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pyarrow.parquet as parquet
from mcap.reader import make_reader
from mcap.writer import Writer
from PIL import Image

from hc_data_platform.ingest.models import RolloutManifestV1
from hc_data_platform.ingest.ports import crc64_ecma

DATASET_ID = "cloudwalk-research/psi0-g1-sneaker-94ep-v1"
DATASET_REVISION = "54d28d1c3f77aaa23d9c20b0388a110b664222d1"
EPISODE_INDEX = 0
FPS = 30
STATE_TOPIC = "/humanoid/observation/state"
ACTION_TOPIC = "/humanoid/action"
CAMERA_TOPIC = "/camera/egocentric/image"
CAMERA_ID = "egocentric"
BASE_TIME = datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc)

SOURCE_FILES = {
    "episode_000000.parquet": (
        "data/chunk-000/episode_000000.parquet",
        "c0f9f17968252ee0c3bbaafd1a4fbd05a699734b6711eb36ddaad094faaddbaa",
    ),
    "info.json": (
        "meta/info.json",
        "a10a19c2cdbfae6cc62ad1ac7eb0f840340139cd2ab824417064d5ca19a8ddcb",
    ),
    "HUGGING_FACE_README.md": (
        "README.md",
        "f7e81217bac3d2f806a81668b32c2d1cbc43b1d161ff164ffe860211e12e8dd9",
    ),
    "episode_000000.mp4": (
        "videos/chunk-000/egocentric/episode_000000.mp4",
        "286eb47d292ef8311ae3c6a14413da233dd187ab5d6ab01e2759ea81d0e29d3c",
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download_sources(
    source_dir: Path, *, source_cache: Path | None = None
) -> dict[str, dict[str, object]]:
    """Download only pinned source files and reject any upstream byte drift."""

    source_dir.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, dict[str, object]] = {}
    for filename, (relative_path, expected_sha256) in SOURCE_FILES.items():
        target = source_dir / filename
        if not target.exists() or _sha256(target) != expected_sha256:
            cached = source_cache / filename if source_cache is not None else None
            if cached is not None and cached.is_file() and _sha256(cached) == expected_sha256:
                shutil.copyfile(cached, target)
            else:
                url = (
                    f"https://huggingface.co/datasets/{DATASET_ID}/resolve/"
                    f"{DATASET_REVISION}/{relative_path}?download=true"
                )
                request = Request(url, headers={"User-Agent": "hc-data-platform-hf-demo/1"})
                with (
                    urlopen(request, timeout=120) as response,
                    target.open(  # noqa: S310
                        "wb"
                    ) as output,
                ):
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
        actual_sha256 = _sha256(target)
        if actual_sha256 != expected_sha256:
            raise ValueError(f"Hugging Face byte drift for {relative_path}")
        evidence[filename] = {
            "repository_path": relative_path,
            "local_path": str(target.resolve()),
            "size": target.stat().st_size,
            "sha256": actual_sha256,
        }
    return evidence


def _json_schema(name: str, width: int) -> bytes:
    return json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": name,
            "type": "object",
            "additionalProperties": False,
            "required": ["episode_index", "frame_index", "source_timestamp_s", "values"],
            "properties": {
                "episode_index": {"type": "integer"},
                "frame_index": {"type": "integer"},
                "source_timestamp_s": {"type": "number"},
                "values": {
                    "type": "array",
                    "minItems": width,
                    "maxItems": width,
                    "items": {"type": "number"},
                },
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _message(row: dict[str, Any], field: str) -> bytes:
    return json.dumps(
        {
            "episode_index": int(row["episode_index"]),
            "frame_index": int(row["frame_index"]),
            "source_timestamp_s": float(row["timestamp"]),
            "values": [float(value) for value in row[field]],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _camera_json_schema() -> bytes:
    return json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "UnitreeG1EgocentricJpegFrame",
            "type": "object",
            "additionalProperties": False,
            "required": [
                "episode_index",
                "frame_index",
                "source_timestamp_s",
                "encoding",
                "width",
                "height",
                "jpeg_sha256",
                "data_base64",
            ],
            "properties": {
                "episode_index": {"type": "integer"},
                "frame_index": {"type": "integer"},
                "source_timestamp_s": {"type": "number"},
                "encoding": {"const": "jpeg"},
                "width": {"const": 640},
                "height": {"const": 480},
                "jpeg_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "data_base64": {"type": "string", "contentEncoding": "base64"},
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _video_probe(video_path: Path) -> dict[str, object]:
    executable = shutil.which("ffprobe")
    if executable is None:
        raise RuntimeError("ffprobe is required to validate the Hugging Face video")
    completed = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate,nb_read_frames,duration",
            "-of",
            "json",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    streams = json.loads(completed.stdout).get("streams", [])
    if len(streams) != 1:
        raise ValueError("Hugging Face MP4 must contain exactly one video stream")
    stream = streams[0]
    expected = {
        "codec_name": "h264",
        "width": 640,
        "height": 480,
        "r_frame_rate": "30/1",
        "nb_read_frames": "469",
    }
    if any(stream.get(name) != value for name, value in expected.items()):
        raise ValueError(f"unexpected Hugging Face video stream: {stream}")
    return {
        "codec": stream["codec_name"],
        "width": stream["width"],
        "height": stream["height"],
        "fps": stream["r_frame_rate"],
        "frames": int(stream["nb_read_frames"]),
        "duration_seconds": float(stream["duration"]),
    }


def extract_jpeg_frames(video_path: Path) -> tuple[bytes, ...]:
    """Decode every source MP4 frame into a self-contained JPEG message payload."""

    executable = shutil.which("ffmpeg")
    if executable is None:
        raise RuntimeError("ffmpeg is required to decode the Hugging Face video")
    completed = subprocess.run(
        [
            executable,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(video_path),
            "-map",
            "0:v:0",
            "-fps_mode",
            "passthrough",
            "-c:v",
            "mjpeg",
            "-q:v",
            "4",
            "-f",
            "image2pipe",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )
    encoded = completed.stdout
    frames: list[bytes] = []
    cursor = 0
    while cursor < len(encoded):
        start = encoded.find(b"\xff\xd8", cursor)
        if start < 0:
            break
        end = encoded.find(b"\xff\xd9", start + 2)
        if end < 0:
            raise ValueError("ffmpeg emitted a truncated JPEG frame")
        frame = encoded[start : end + 2]
        with Image.open(BytesIO(frame)) as image:
            image.verify()
        with Image.open(BytesIO(frame)) as image:
            if image.format != "JPEG" or image.size != (640, 480):
                raise ValueError("decoded camera frame is not a 640x480 JPEG")
        frames.append(frame)
        cursor = end + 2
    if cursor != len(encoded) or len(frames) != 469:
        raise ValueError(
            f"ffmpeg emitted {len(frames)} complete JPEG frames from {len(encoded)} bytes"
        )
    return tuple(frames)


def _camera_message(row: dict[str, Any], jpeg: bytes) -> bytes:
    return json.dumps(
        {
            "episode_index": int(row["episode_index"]),
            "frame_index": int(row["frame_index"]),
            "source_timestamp_s": float(row["timestamp"]),
            "encoding": "jpeg",
            "width": 640,
            "height": 480,
            "jpeg_sha256": hashlib.sha256(jpeg).hexdigest(),
            "data_base64": base64.b64encode(jpeg).decode("ascii"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def convert_episode(parquet_path: Path, video_path: Path, mcap_path: Path) -> dict[str, object]:
    table = parquet.read_table(parquet_path)
    required = {
        "states",
        "action",
        "timestamp",
        "frame_index",
        "episode_index",
    }
    if not required.issubset(table.column_names):
        raise ValueError("Hugging Face episode is missing required LeRobot columns")
    rows = table.to_pylist()
    if not rows:
        raise ValueError("Hugging Face episode contains no frames")
    if {int(row["episode_index"]) for row in rows} != {EPISODE_INDEX}:
        raise ValueError("Parquet file contains an unexpected episode")
    if [int(row["frame_index"]) for row in rows] != list(range(len(rows))):
        raise ValueError("frame_index is not contiguous")
    state_widths = {len(row["states"]) for row in rows}
    action_widths = {len(row["action"]) for row in rows}
    if state_widths != {32} or action_widths != {36}:
        raise ValueError(
            f"unexpected state/action widths: {sorted(state_widths)}/{sorted(action_widths)}"
        )
    timestamps = [float(row["timestamp"]) for row in rows]
    if timestamps[0] != 0 or any(
        left >= right for left, right in zip(timestamps, timestamps[1:], strict=False)
    ):
        raise ValueError("episode timestamps must start at zero and increase strictly")
    if any(
        abs(timestamp - frame_index / FPS) > 1e-5
        for frame_index, timestamp in enumerate(timestamps)
    ):
        raise ValueError("episode timestamps differ from the declared 30 Hz frame grid")
    video = _video_probe(video_path)
    jpeg_frames = extract_jpeg_frames(video_path)
    if int(video["frames"]) != len(rows) or len(jpeg_frames) != len(rows):
        raise ValueError("Parquet and video frame counts differ")

    mcap_path.parent.mkdir(parents=True, exist_ok=True)
    writer = Writer(str(mcap_path))
    writer.start(profile="hc-json-timeseries", library="hc-hf-humanoid-converter/1")
    state_schema = writer.register_schema(
        "cloudwalk.psi0.UnitreeG1State", "jsonschema", _json_schema("UnitreeG1State", 32)
    )
    action_schema = writer.register_schema(
        "cloudwalk.psi0.UnitreeG1Action", "jsonschema", _json_schema("UnitreeG1Action", 36)
    )
    camera_schema = writer.register_schema(
        "cloudwalk.psi0.UnitreeG1EgocentricJpegFrame",
        "jsonschema",
        _camera_json_schema(),
    )
    metadata = {
        "hf.dataset": DATASET_ID,
        "hf.revision": DATASET_REVISION,
        "hf.episode": str(EPISODE_INDEX),
        "hf.source_sha256": _sha256(parquet_path),
    }
    state_channel = writer.register_channel(
        STATE_TOPIC,
        "json",
        state_schema,
        metadata={**metadata, "hf.column": "states"},
    )
    action_channel = writer.register_channel(
        ACTION_TOPIC,
        "json",
        action_schema,
        metadata={**metadata, "hf.column": "action"},
    )
    camera_channel = writer.register_channel(
        CAMERA_TOPIC,
        "json",
        camera_schema,
        metadata={
            **metadata,
            "hf.column": "observation.images.egocentric",
            "hf.video_sha256": _sha256(video_path),
            "image.encoding": "jpeg",
            "image.width": "640",
            "image.height": "480",
        },
    )
    base_ns = int(BASE_TIME.timestamp() * 1_000_000_000)
    writer.add_attachment(
        create_time=base_ns,
        log_time=base_ns,
        name="huggingface/videos/chunk-000/egocentric/episode_000000.mp4",
        media_type="video/mp4",
        data=video_path.read_bytes(),
    )
    for sequence, row in enumerate(rows):
        # LeRobot stores timestamps as float32, so nominal 30 Hz values can lie a
        # few hundred nanoseconds on either side of the exact frame grid.  Use
        # frame_index for MCAP log time; retain that original float in the JSON
        # payload as source_timestamp_s for complete provenance.
        timestamp_ns = base_ns + sequence * 1_000_000_000 // FPS
        writer.add_message(
            state_channel,
            log_time=timestamp_ns,
            publish_time=timestamp_ns,
            sequence=sequence,
            data=_message(row, "states"),
        )
        writer.add_message(
            action_channel,
            log_time=timestamp_ns,
            publish_time=timestamp_ns,
            sequence=sequence,
            data=_message(row, "action"),
        )
        writer.add_message(
            camera_channel,
            log_time=timestamp_ns,
            publish_time=timestamp_ns,
            sequence=sequence,
            data=_camera_message(row, jpeg_frames[sequence]),
        )
    writer.finish()

    with mcap_path.open("rb") as source:
        messages = tuple(make_reader(source).iter_messages())
    observed_topics = {channel.topic for _schema, channel, _message in messages}
    if len(messages) != len(rows) * 3 or observed_topics != {
        STATE_TOPIC,
        ACTION_TOPIC,
        CAMERA_TOPIC,
    }:
        raise ValueError("generated MCAP inventory is incomplete")
    return {
        "rows": len(rows),
        "messages": len(messages),
        "camera_frames": len(jpeg_frames),
        "camera_jpeg_bytes": sum(len(frame) for frame in jpeg_frames),
        "camera_first_frame_sha256": hashlib.sha256(jpeg_frames[0]).hexdigest(),
        "camera_last_frame_sha256": hashlib.sha256(jpeg_frames[-1]).hexdigest(),
        "source_video": video,
        "source_video_path": str(video_path.resolve()),
        "source_video_sha256": _sha256(video_path),
        "source_video_embedded_as_mcap_attachment": True,
        "state_width": next(iter(state_widths)),
        "action_width": next(iter(action_widths)),
        "first_source_timestamp_s": timestamps[0],
        "last_source_timestamp_s": timestamps[-1],
        "mcap_path": str(mcap_path.resolve()),
        "mcap_size": mcap_path.stat().st_size,
        "mcap_sha256": _sha256(mcap_path),
    }


def build_manifest(
    mcap_path: Path,
    manifest_path: Path,
    *,
    project_id: str,
    collection_task_id: str,
    run_id: str,
) -> RolloutManifestV1:
    payload = mcap_path.read_bytes()
    sha256 = hashlib.sha256(payload).hexdigest()
    crc64 = crc64_ecma(payload)
    with mcap_path.open("rb") as source:
        messages = tuple(make_reader(source).iter_messages())
    frame_count = sum(1 for _schema, channel, _message in messages if channel.topic == STATE_TOPIC)
    # The manifest window is half-open.  Compute it from the declared fixed-rate
    # grid at microsecond precision so datetime serialization cannot round the
    # source timestamp residue into one extra alignment step.
    end_time = BASE_TIME + timedelta(microseconds=frame_count * 1_000_000 // FPS)
    topics = (STATE_TOPIC, ACTION_TOPIC, CAMERA_TOPIC)
    manifest = RolloutManifestV1(
        project_id=project_id,
        task_id=collection_task_id,
        collection_job_id=f"{run_id}-hf-g1-job",
        rollout_id=f"{run_id}-hf-g1-episode-0",
        collection_session_id=f"{run_id}-hf-g1-session",
        recording_request_id=f"{run_id}-hf-g1-request",
        data_package_id=f"{run_id}-hf-g1-package",
        sequence_no=1,
        robot_id="unitree-g1",
        start_time=BASE_TIME,
        end_time=end_time,
        cameras=[
            {
                "camera_id": CAMERA_ID,
                "topic": CAMERA_TOPIC,
                "frame_id": "egocentric_camera",
                "encoding": "jpeg",
            }
        ],
        topics=[
            {
                "name": topic,
                "required": True,
                "message_encoding": "json",
                "schema_name": {
                    STATE_TOPIC: "cloudwalk.psi0.UnitreeG1State",
                    ACTION_TOPIC: "cloudwalk.psi0.UnitreeG1Action",
                    CAMERA_TOPIC: "cloudwalk.psi0.UnitreeG1EgocentricJpegFrame",
                }[topic],
            }
            for topic in topics
        ],
        expected_topics=list(topics),
        actual_topics=list(topics),
        files=[
            {
                "path": mcap_path.name,
                "size": len(payload),
                "sha256": sha256,
                "crc64": crc64,
                "media_type": "application/octet-stream",
                "role": "RAW_MCAP",
            }
        ],
        file_size=len(payload),
        sha256=sha256,
        crc64=crc64,
        compression="none",
        recorder_version="hf-unitree-g1-to-mcap/1",
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def prepare_bundle(
    output_dir: Path,
    *,
    project_id: str,
    collection_task_id: str,
    run_id: str,
    source_cache: Path | None = None,
) -> dict[str, object]:
    source_dir = output_dir / "source"
    bundle_dir = output_dir / "bundle"
    source_evidence = download_sources(source_dir, source_cache=source_cache)
    mcap_path = bundle_dir / "unitree-g1-episode-000000.mcap"
    manifest_path = bundle_dir / "rollout_manifest.json"
    conversion = convert_episode(
        source_dir / "episode_000000.parquet",
        source_dir / "episode_000000.mp4",
        mcap_path,
    )
    manifest = build_manifest(
        mcap_path,
        manifest_path,
        project_id=project_id,
        collection_task_id=collection_task_id,
        run_id=run_id,
    )
    evidence = {
        "dataset": {
            "id": DATASET_ID,
            "revision": DATASET_REVISION,
            "license": "apache-2.0",
            "episode_index": EPISODE_INDEX,
        },
        "source_files": source_evidence,
        "conversion": conversion,
        "manifest_path": str(manifest_path.resolve()),
        "manifest": manifest.model_dump(mode="json"),
    }
    (output_dir / "conversion-evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--collection-task-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-cache", type=Path)
    args = parser.parse_args()
    result = prepare_bundle(
        args.output_dir,
        project_id=args.project_id,
        collection_task_id=args.collection_task_id,
        run_id=args.run_id,
        source_cache=args.source_cache,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
