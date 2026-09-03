"""Convert one Hugging Face LeRobot DROID episode into an upload-ready package.

The produced directory contains the P03 ``rollout_manifest.json``, its raw
``recording.mcap``, and a Manifest-declared ``recording-config.json`` auxiliary
file.  The recording configuration preserves the source clock and stream facts;
it is deliberately separate from any robot-model configuration.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, cast
from xml.etree import ElementTree

from mcap.reader import make_reader
from mcap.writer import CompressionType, Writer
from PIL import Image

from hc_data_platform.ingest.manifest import parse_manifest_bytes
from hc_data_platform.ingest.ports import crc64_ecma
from hc_data_platform.verification.engine import McapVerifier
from hc_data_platform.verification.models import VerificationStatus
from hc_data_platform.verification.ports import RegisteredDecoderProbe

# This 100-episode conversion retains DROID's real 7-axis joint positions.
# lerobot/droid_100 only exposes Cartesian state as observation.state, so it
# cannot truthfully drive a Franka URDF.
DEFAULT_REPOSITORY = "aractingi/droid_100"
DEFAULT_REVISION = "main"
DEFAULT_SOURCE_LICENSE = "Apache-2.0"
ROBOT_ASSET_REPOSITORY = "generative-skill-chaining/gsc-code"
ROBOT_ASSET_REVISION = "b8f5bc0d44bd453827f3f254ca1b03ec049849c5"
ROBOT_ASSET_PREFIX = "configs/pybullet/envs/assets/franka_panda"
ROBOT_MODEL_FORMAT_VERSION = 3
PANDA_ARM_JOINTS = frozenset(f"joint{index}" for index in range(1, 8))
DEFAULT_CAPTURE_START = datetime(2024, 3, 20, tzinfo=timezone.utc)
STATE_TOPIC = "/humanoid/observation/state"
JOINT_TOPIC = "/robot/joint_states"
ACTION_TOPIC = "/humanoid/action"
SOURCE_TOPIC = "/metadata/source"
RAW_FILE_NAME = "recording.mcap"
RECORDING_CONFIG_FILE_NAME = "recording-config.json"
MANIFEST_FILE_NAME = "rollout_manifest.json"
RECORDER_VERSION = "hf-lerobot-mcap/1.2.0"


@dataclass(frozen=True)
class CameraSpec:
    camera_id: str
    feature_key: str
    topic: str
    frame_id: str


CAMERAS = (
    CameraSpec(
        camera_id="wrist",
        feature_key="observation.image.wrist_image_left",
        topic="/camera/egocentric/image",
        frame_id="droid_wrist_optical",
    ),
    CameraSpec(
        camera_id="exterior_1",
        feature_key="observation.image.exterior_image_1_left",
        topic="/camera/exterior_1/image",
        frame_id="droid_exterior_1_optical",
    ),
    CameraSpec(
        camera_id="exterior_2",
        feature_key="observation.image.exterior_image_2_left",
        topic="/camera/exterior_2/image",
        frame_id="droid_exterior_2_optical",
    ),
)
REQUIRED_TOPICS = (
    *(camera.topic for camera in CAMERAS),
    ACTION_TOPIC,
    JOINT_TOPIC,
    SOURCE_TOPIC,
    STATE_TOPIC,
)


@dataclass(frozen=True)
class EpisodeData:
    fps: float
    relative_timestamps_ns: tuple[int, ...]
    states: tuple[tuple[float, ...], ...]
    actions: tuple[tuple[float, ...], ...]
    source_indexes: tuple[int, ...]
    task_index: int

    @property
    def frame_count(self) -> int:
        return len(self.relative_timestamps_ns)


@dataclass(frozen=True)
class ConversionRequest:
    project_id: str
    collection_task_id: str
    episode_index: int
    repository: str
    requested_revision: str
    resolved_revision: str
    robot_id: str
    output_root: Path
    capture_start: datetime


@dataclass(frozen=True)
class SourceLayout:
    info: dict[str, Any]
    data_file: Path
    video_files: dict[str, Path]


class _LocalFileStorage:
    def open_reader(self, object_key: str) -> BinaryIO:
        return Path(object_key).open("rb")


def _safe_archive_relative(name: str, prefix: str) -> Path | None:
    marker = f"/{prefix}/"
    if marker not in name:
        return None
    relative = Path(name.split(marker, 1)[1])
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError(f"unsafe robot asset archive member: {name}")
    return relative


def _primitive_size(link_name: str) -> str:
    if link_name == "link0":
        return "0.18 0.18 0.16"
    if link_name.startswith("link"):
        return "0.10 0.10 0.22"
    if "finger" in link_name:
        return "0.018 0.025 0.075"
    if "knuckle" in link_name:
        return "0.025 0.035 0.045"
    return "0.09 0.12 0.08"


def _write_platform_preview_urdf(source: Path, destination: Path) -> None:
    tree = ElementTree.parse(source)
    root = tree.getroot()
    for link in root.findall("link"):
        link_name = link.attrib.get("name", "")
        for visual in link.findall("visual"):
            geometry = visual.find("geometry")
            if geometry is None or geometry.find("mesh") is None:
                continue
            for child in list(geometry):
                geometry.remove(child)
            ElementTree.SubElement(geometry, "box", {"size": _primitive_size(link_name)})
    # DROID's public state exposes the seven Panda arm joints, not the Robotiq
    # linkage positions. Keep the gripper geometry in a deterministic open pose
    # while leaving exactly the seven data-driven joints actuated.
    for joint in root.findall("joint"):
        if joint.attrib.get("name") in PANDA_ARM_JOINTS:
            continue
        if joint.attrib.get("type") in {"fixed", "floating"}:
            continue
        joint.attrib["type"] = "fixed"
        for child_name in (
            "axis",
            "calibration",
            "dynamics",
            "limit",
            "mimic",
            "safety_controller",
        ):
            optional_child = joint.find(child_name)
            if optional_child is not None:
                joint.remove(optional_child)
    tree.write(destination, encoding="utf-8", xml_declaration=True)


def _robot_mapping_document() -> dict[str, object]:
    return {
        "format_version": ROBOT_MODEL_FORMAT_VERSION,
        "robot_id": "droid-franka",
        "source_repository": ROBOT_ASSET_REPOSITORY,
        "source_revision": ROBOT_ASSET_REVISION,
        "asset_classification": "THIRD_PARTY_COMPATIBILITY_PREVIEW",
        "dataset_supplied": False,
        "official_droid_urdf": False,
        "entry_urdf": "droid_franka_robotiq_platform.urdf",
        "mappings": [
            {
                "source_joint_name": f"joint_{index}",
                "target_joint_name": f"joint{index + 1}",
                "direction": "SAME",
            }
            for index in range(7)
        ],
    }


def _write_robot_mapping(path: Path) -> None:
    path.write_text(
        json.dumps(_robot_mapping_document(), indent=2) + "\n",
        encoding="utf-8",
    )


def _write_robot_provenance(path: Path) -> None:
    path.write_text(
        "# Robot model provenance\n\n"
        "This directory is **not** a URDF supplied by the Hugging Face DROID recording.\n"
        "The retained source bundle comes from the pinned third-party GSC repository, and\n"
        "`droid_franka_robotiq_platform.urdf` is a primitive-geometry compatibility preview\n"
        "generated for the HC annotation viewer. It must not be treated as an official DROID,\n"
        "Franka, or Robotiq kinematic/calibration artifact.\n",
        encoding="utf-8",
    )


def acquire_robot_model_assets(*, cache_root: Path, output_root: Path) -> Path:
    destination = output_root / "_robot_model" / "droid_franka_robotiq"
    preview_urdf = destination / "droid_franka_robotiq_platform.urdf"
    mapping_path = destination / "joint-mapping.json"
    provenance_path = destination / "MODEL_PROVENANCE.md"
    if preview_urdf.is_file() and mapping_path.is_file():
        try:
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            mapping = None
        if isinstance(mapping, dict) and mapping.get("format_version") == (
            ROBOT_MODEL_FORMAT_VERSION
        ):
            return destination
        retained_source = destination / "source" / "franka_panda_robotiq.urdf"
        if not retained_source.is_file():
            raise RuntimeError(f"robot model assets cannot be upgraded in place: {destination}")
        _write_platform_preview_urdf(retained_source, preview_urdf)
        _write_robot_mapping(mapping_path)
        _write_robot_provenance(provenance_path)
        return destination
    archive = _download_file(
        f"https://github.com/{ROBOT_ASSET_REPOSITORY}/archive/{ROBOT_ASSET_REVISION}.tar.gz",
        cache_root / "robot-model" / f"gsc-code-{ROBOT_ASSET_REVISION}.tar.gz",
    )
    staging_parent = output_root / "_robot_model"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".droid-franka-", dir=staging_parent))
    try:
        source_root = staging / "source"
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                relative = _safe_archive_relative(member.name, ROBOT_ASSET_PREFIX)
                if relative is None or not member.isfile():
                    continue
                extracted = bundle.extractfile(member)
                if extracted is None:
                    raise RuntimeError(f"could not extract robot asset {member.name}")
                target = source_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(extracted.read())
            license_member = next(
                (
                    item
                    for item in bundle.getmembers()
                    if item.isfile()
                    and item.name.count("/") == 1
                    and item.name.endswith("/LICENSE")
                ),
                None,
            )
            if license_member is None:
                raise RuntimeError("robot asset archive does not contain its license")
            extracted_license = bundle.extractfile(license_member)
            if extracted_license is None:
                raise RuntimeError("could not extract robot asset license")
            (staging / "LICENSE").write_bytes(extracted_license.read())
        source_urdf = source_root / "franka_panda_robotiq.urdf"
        if not source_urdf.is_file():
            raise RuntimeError("robot asset archive does not contain the DROID URDF")
        _write_platform_preview_urdf(source_urdf, staging / preview_urdf.name)
        mapping_path_in_staging = staging / mapping_path.name
        _write_robot_mapping(mapping_path_in_staging)
        _write_robot_provenance(staging / provenance_path.name)
        if destination.exists():
            raise RuntimeError(f"incomplete robot model directory already exists: {destination}")
        os.replace(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _schema_bytes(value: object) -> bytes:
    return _json_bytes(value)


CAMERA_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["data_base64", "encoding", "height", "jpeg_sha256", "width"],
    "properties": {
        "data_base64": {"type": "string", "contentEncoding": "base64"},
        "encoding": {"const": "jpeg"},
        "height": {"type": "integer", "minimum": 1},
        "jpeg_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "width": {"type": "integer", "minimum": 1},
    },
}
JOINT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["names", "positions"],
    "properties": {
        "names": {"type": "array", "items": {"type": "string"}},
        "positions": {"type": "array", "items": {"type": "number"}},
    },
}
ACTION_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["names", "values"],
    "properties": {
        "names": {"type": "array", "items": {"type": "string"}},
        "values": {"type": "array", "items": {"type": "number"}},
    },
}
SOURCE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "episode_index",
        "license",
        "repository",
        "requested_revision",
        "resolved_revision",
        "source_format",
        "task_index",
        "nominal_frequency_hz",
    ],
    "properties": {
        "episode_index": {"type": "integer", "minimum": 0},
        "license": {"type": "string"},
        "repository": {"type": "string"},
        "requested_revision": {"type": "string"},
        "resolved_revision": {"type": "string"},
        "source_format": {"type": "string"},
        "task_index": {"type": "integer", "minimum": 0},
        "nominal_frequency_hz": {"type": "number", "exclusiveMinimum": 0},
    },
}


def _parse_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("capture start must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("capture start must include a timezone")
    return parsed.astimezone(timezone.utc)


def _request_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "hc-data-platform/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            decoded = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"failed to read {url}") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"expected a JSON object from {url}")
    return cast(dict[str, Any], decoded)


def _resolved_revision(repository: str, revision: str) -> str:
    if len(revision) == 40 and all(character in "0123456789abcdefABCDEF" for character in revision):
        return revision.lower()
    # Hugging Face treats the owner/name slash as a path separator on this API.
    encoded_repo = urllib.parse.quote(repository, safe="/")
    encoded_revision = urllib.parse.quote(revision, safe="")
    payload = _request_json(
        f"https://huggingface.co/api/datasets/{encoded_repo}/revision/{encoded_revision}"
    )
    value = payload.get("sha")
    if not isinstance(value, str) or len(value) < 7:
        raise RuntimeError("Hugging Face did not return a resolved dataset revision")
    return value


def _download_url(repository: str, revision: str, relative_path: str) -> str:
    encoded_path = urllib.parse.quote(relative_path, safe="/")
    encoded_revision = urllib.parse.quote(revision, safe="")
    return f"https://huggingface.co/datasets/{repository}/resolve/{encoded_revision}/{encoded_path}"


def _remote_size(url: str) -> int | None:
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "hc-data-platform/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            value = response.headers.get("Content-Length") or response.headers.get("X-Linked-Size")
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(f"failed to inspect {url}") from exc
    return int(value) if value else None


def _download_file(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        total_size = _remote_size(url)
    except RuntimeError:
        if destination.is_file():
            print(
                f"reuse pinned cached file without remote HEAD: {destination.name}",
                file=sys.stderr,
            )
            return destination
        raise
    if destination.is_file():
        if total_size is None or destination.stat().st_size == total_size:
            return destination
        partial_from_previous_run = destination.with_suffix(destination.suffix + ".part")
        if partial_from_previous_run.exists():
            raise RuntimeError(
                f"both incomplete destination and partial download exist: {destination}"
            )
        os.replace(destination, partial_from_previous_run)
    partial = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(1, 9):
        offset = partial.stat().st_size if partial.exists() else 0
        if total_size is not None and offset > total_size:
            raise RuntimeError(f"partial download is larger than its source: {partial}")
        headers = {"User-Agent": "hc-data-platform/1.0"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                append = offset > 0 and getattr(response, "status", None) == 206
                mode = "ab" if append else "wb"
                if not append:
                    offset = 0
                print(
                    f"download {destination.name} (attempt {attempt}/8)",
                    file=sys.stderr,
                    flush=True,
                )
                with partial.open(mode) as output:
                    copied = offset
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        copied += len(chunk)
                        if total_size:
                            percent = min(100, copied * 100 // total_size)
                            print(
                                f"\r  {copied / 1024**2:.1f} / "
                                f"{total_size / 1024**2:.1f} MiB ({percent}%)",
                                end="",
                                file=sys.stderr,
                                flush=True,
                            )
                if total_size:
                    print(file=sys.stderr)
        except (OSError, urllib.error.URLError) as exc:
            if attempt == 8:
                raise RuntimeError(f"failed to download {url}") from exc
            print(f"retry after transport error: {exc}", file=sys.stderr, flush=True)
            continue
        copied = partial.stat().st_size
        if total_size is None or copied == total_size:
            break
        if attempt == 8:
            raise RuntimeError(
                f"download remained incomplete after 8 attempts: {copied}/{total_size} bytes"
            )
        print(
            f"retry incomplete transfer: {copied}/{total_size} bytes",
            file=sys.stderr,
            flush=True,
        )
    os.replace(partial, destination)
    return destination


def _format_source_path(template: str, episode_index: int, video_key: str = "") -> str:
    values: dict[str, object] = {
        "chunk_index": episode_index // 1000,
        "episode_chunk": episode_index // 1000,
        "episode_index": episode_index,
        "file_index": 0,
        "video_key": video_key,
    }
    try:
        return template.format(**values)
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"unsupported LeRobot path template: {template}") from exc


def acquire_source(
    *,
    repository: str,
    revision: str,
    episode_index: int,
    cache_root: Path,
) -> SourceLayout:
    root = cache_root / repository.replace("/", "--") / revision
    info_path = _download_file(
        _download_url(repository, revision, "meta/info.json"), root / "meta/info.json"
    )
    info = cast(dict[str, Any], json.loads(info_path.read_text(encoding="utf-8")))
    total_episodes = info.get("total_episodes")
    if not isinstance(total_episodes, int) or not 0 <= episode_index < total_episodes:
        raise RuntimeError(
            f"episode {episode_index} is outside the dataset range 0:{total_episodes}"
        )
    data_template = info.get("data_path")
    video_template = info.get("video_path")
    if not isinstance(data_template, str) or not isinstance(video_template, str):
        raise RuntimeError("LeRobot info.json does not declare data_path and video_path")
    data_relative = _format_source_path(data_template, episode_index)
    data_file = _download_file(
        _download_url(repository, revision, data_relative), root / data_relative
    )
    video_files: dict[str, Path] = {}
    for camera in CAMERAS:
        relative = _format_source_path(video_template, episode_index, camera.feature_key)
        video_files[camera.feature_key] = _download_file(
            _download_url(repository, revision, relative), root / relative
        )
    return SourceLayout(info=info, data_file=data_file, video_files=video_files)


def _finite_vector(value: object, *, field: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        to_list = getattr(value, "tolist", None)
        if not callable(to_list):
            raise RuntimeError(f"{field} is not a non-empty numeric vector")
        value = to_list()
    if not isinstance(value, (list, tuple)):
        raise RuntimeError(f"{field} is not a numeric vector")
    result = tuple(float(item) for item in value)
    if not result or len(result) > 64 or any(not math.isfinite(item) for item in result):
        raise RuntimeError(f"{field} contains invalid numeric values")
    return result


def load_episode_data(data_file: Path, episode_index: int, fps: float) -> EpisodeData:
    try:
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("install the backend data extra to read LeRobot Parquet") from exc
    requested = (
        "episode_index",
        "frame_index",
        "index",
        "timestamp",
        "task_index",
        "observation.state.joint_position",
        "action",
    )
    table = pq.read_table(data_file, columns=list(requested))
    episode = table.filter(pc.equal(table["episode_index"], episode_index))
    rows = cast(list[dict[str, object]], episode.to_pylist())
    if not rows:
        raise RuntimeError(f"episode {episode_index} has no rows in {data_file}")
    rows.sort(key=lambda item: int(cast(int, item["frame_index"])))
    frame_indexes = tuple(int(cast(int, row["frame_index"])) for row in rows)
    if frame_indexes != tuple(range(len(rows))):
        raise RuntimeError("episode frame_index values are not contiguous from zero")
    source_indexes = tuple(int(cast(int, row["index"])) for row in rows)
    timestamps = tuple(float(cast(float, row["timestamp"])) for row in rows)
    if any(not math.isfinite(item) for item in timestamps):
        raise RuntimeError("episode timestamps contain non-finite values")
    origin = timestamps[0]
    relative = tuple(max(0, round((item - origin) * 1_000_000_000)) for item in timestamps)
    if any(left >= right for left, right in zip(relative, relative[1:], strict=False)):
        relative = tuple(round(index * 1_000_000_000 / fps) for index in frame_indexes)
    states = tuple(
        _finite_vector(
            row["observation.state.joint_position"],
            field="observation.state.joint_position",
        )
        for row in rows
    )
    actions = tuple(_finite_vector(row["action"], field="action") for row in rows)
    if len({len(item) for item in states}) != 1 or len({len(item) for item in actions}) != 1:
        raise RuntimeError("episode state or action dimensions are inconsistent")
    task_indexes = {int(cast(int, row["task_index"])) for row in rows}
    if len(task_indexes) != 1:
        raise RuntimeError("episode contains more than one task_index")
    return EpisodeData(
        fps=fps,
        relative_timestamps_ns=relative,
        states=states,
        actions=actions,
        source_indexes=source_indexes,
        task_index=task_indexes.pop(),
    )


def _extract_frames(
    video: Path,
    *,
    start_frame: int,
    frame_count: int,
    fps: float,
    destination: Path,
) -> tuple[Path, ...]:
    destination.mkdir(parents=True, exist_ok=True)
    start_seconds = start_frame / fps
    command = (
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start_seconds:.9f}",
        "-i",
        str(video),
        "-an",
        "-frames:v",
        str(frame_count),
        "-fps_mode",
        "passthrough",
        "-q:v",
        "3",
        str(destination / "frame_%06d.jpg"),
    )
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required to decode LeRobot video files") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffmpeg could not decode {video}") from exc
    frames = tuple(sorted(destination.glob("frame_*.jpg")))
    if len(frames) != frame_count:
        raise RuntimeError(f"decoded {len(frames)} frames from {video}; expected {frame_count}")
    return frames


def extract_camera_frames(
    layout: SourceLayout,
    episode: EpisodeData,
    temp_root: Path,
) -> dict[str, tuple[Path, ...]]:
    start_frame = episode.source_indexes[0]
    if "episode_" in layout.data_file.name:
        start_frame = 0
    result: dict[str, tuple[Path, ...]] = {}
    for camera in CAMERAS:
        result[camera.feature_key] = _extract_frames(
            layout.video_files[camera.feature_key],
            start_frame=start_frame,
            frame_count=episode.frame_count,
            fps=episode.fps,
            destination=temp_root / camera.camera_id,
        )
    return result


def _register_channels(writer: Writer) -> dict[str, int]:
    camera_schema = writer.register_schema(
        "hc.camera.JpegEnvelope", "jsonschema", _schema_bytes(CAMERA_SCHEMA)
    )
    joint_schema = writer.register_schema(
        "hc.robot.JointState", "jsonschema", _schema_bytes(JOINT_SCHEMA)
    )
    action_schema = writer.register_schema(
        "hc.robot.Action", "jsonschema", _schema_bytes(ACTION_SCHEMA)
    )
    source_schema = writer.register_schema(
        "hc.source.HuggingFace", "jsonschema", _schema_bytes(SOURCE_SCHEMA)
    )
    channels = {
        camera.topic: writer.register_channel(camera.topic, "json", camera_schema)
        for camera in CAMERAS
    }
    channels[STATE_TOPIC] = writer.register_channel(STATE_TOPIC, "json", joint_schema)
    channels[JOINT_TOPIC] = writer.register_channel(JOINT_TOPIC, "json", joint_schema)
    channels[ACTION_TOPIC] = writer.register_channel(ACTION_TOPIC, "json", action_schema)
    channels[SOURCE_TOPIC] = writer.register_channel(SOURCE_TOPIC, "json", source_schema)
    return channels


def _camera_message(frame_path: Path) -> bytes:
    encoded = frame_path.read_bytes()
    with Image.open(frame_path) as image:
        image.load()
        if image.format != "JPEG":
            raise RuntimeError(f"decoded camera frame is not JPEG: {frame_path}")
        width, height = image.size
    return _json_bytes(
        {
            "data_base64": base64.b64encode(encoded).decode("ascii"),
            "encoding": "jpeg",
            "height": height,
            "jpeg_sha256": hashlib.sha256(encoded).hexdigest(),
            "width": width,
        }
    )


def write_mcap(
    destination: Path,
    *,
    request: ConversionRequest,
    episode: EpisodeData,
    camera_frames: dict[str, tuple[Path, ...]],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    for camera in CAMERAS:
        frames = camera_frames.get(camera.feature_key)
        if frames is None or len(frames) != episode.frame_count:
            raise RuntimeError(f"camera {camera.feature_key} does not match the episode length")
    start_ns = round(request.capture_start.timestamp() * 1_000_000_000)
    state_names = [f"joint_{index}" for index in range(len(episode.states[0]))]
    action_names = [f"action_{index}" for index in range(len(episode.actions[0]))]
    with destination.open("wb") as output:
        writer = Writer(
            output,
            compression=CompressionType.ZSTD,
            chunk_size=8 * 1024 * 1024,
            enable_crcs=True,
            enable_data_crcs=True,
        )
        writer.start(profile="hc-robotics-json/v1", library=RECORDER_VERSION)
        channels = _register_channels(writer)
        source = {
            "episode_index": request.episode_index,
            "license": DEFAULT_SOURCE_LICENSE,
            "repository": request.repository,
            "requested_revision": request.requested_revision,
            "resolved_revision": request.resolved_revision,
            "source_format": "LeRobot episode files",
            "task_index": episode.task_index,
            "nominal_frequency_hz": episode.fps,
        }
        for index, relative_ns in enumerate(episode.relative_timestamps_ns):
            timestamp_ns = start_ns + relative_ns
            sequence = index + 1
            for camera in CAMERAS:
                writer.add_message(
                    channels[camera.topic],
                    log_time=timestamp_ns,
                    publish_time=timestamp_ns,
                    sequence=sequence,
                    data=_camera_message(camera_frames[camera.feature_key][index]),
                )
            state = _json_bytes({"names": state_names, "positions": episode.states[index]})
            for topic in (STATE_TOPIC, JOINT_TOPIC):
                writer.add_message(
                    channels[topic],
                    log_time=timestamp_ns,
                    publish_time=timestamp_ns,
                    sequence=sequence,
                    data=state,
                )
            writer.add_message(
                channels[ACTION_TOPIC],
                log_time=timestamp_ns,
                publish_time=timestamp_ns,
                sequence=sequence,
                data=_json_bytes({"names": action_names, "values": episode.actions[index]}),
            )
            writer.add_message(
                channels[SOURCE_TOPIC],
                log_time=timestamp_ns,
                publish_time=timestamp_ns,
                sequence=sequence,
                data=_json_bytes(source),
            )
        writer.finish()


def _file_checksums(path: Path) -> tuple[int, str, int]:
    sha256 = hashlib.sha256()
    crc64 = 0
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            sha256.update(chunk)
            crc64 = crc64_ecma(chunk, crc64)
    return size, sha256.hexdigest(), crc64


def _identity(request: ConversionRequest) -> dict[str, str]:
    seed = (
        f"{request.project_id}|{request.collection_task_id}|{request.repository}|"
        f"{request.resolved_revision}|{request.episode_index}|{RECORDER_VERSION}"
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return {
        "collection_job_id": f"hf-droid-job-{digest}",
        "rollout_id": f"hf-droid-ep-{request.episode_index:06d}-{digest}",
        "collection_session_id": f"hf-droid-session-{digest}",
        "recording_request_id": f"hf-droid-request-{digest}",
        "data_package_id": f"hf-droid-package-{request.episode_index:06d}-{digest}",
    }


def build_recording_config(
    *,
    request: ConversionRequest,
    episode: EpisodeData,
    source_info: dict[str, Any] | None,
) -> dict[str, object]:
    """Preserve source recording facts without claiming robot-model provenance."""

    intervals = [
        right - left
        for left, right in zip(
            episode.relative_timestamps_ns,
            episode.relative_timestamps_ns[1:],
            strict=False,
        )
    ]
    sorted_intervals = sorted(intervals)
    median_interval_ns = (
        sorted_intervals[len(sorted_intervals) // 2]
        if sorted_intervals
        else round(1_000_000_000 / episode.fps)
    )
    source_features = source_info.get("features", {}) if isinstance(source_info, dict) else {}
    if not isinstance(source_features, dict):
        source_features = {}
    camera_streams: list[dict[str, object]] = []
    for camera in CAMERAS:
        feature = source_features.get(camera.feature_key, {})
        feature = feature if isinstance(feature, dict) else {}
        video_info = feature.get("info", {})
        video_info = video_info if isinstance(video_info, dict) else {}
        camera_streams.append(
            {
                "camera_id": camera.camera_id,
                "source_feature": camera.feature_key,
                "output_topic": camera.topic,
                "source_video": {
                    key: video_info[key]
                    for key in (
                        "video.fps",
                        "video.height",
                        "video.width",
                        "video.channels",
                        "video.codec",
                        "video.pix_fmt",
                        "video.is_depth_map",
                        "has_audio",
                    )
                    if key in video_info
                },
            }
        )
    robot_type = source_info.get("robot_type") if isinstance(source_info, dict) else None
    return {
        "schema_version": "hc-recording-config/v1",
        "source": {
            "repository": request.repository,
            "requested_revision": request.requested_revision,
            "resolved_revision": request.resolved_revision,
            "episode_index": request.episode_index,
            "metadata_path": "meta/info.json",
        },
        "clock": {
            "basis": "source_episode_timestamps",
            "nominal_frequency_hz": episode.fps,
            "frame_count": episode.frame_count,
            "start_offset_ns": episode.relative_timestamps_ns[0],
            "last_sample_offset_ns": episode.relative_timestamps_ns[-1],
            "observed_interval_ns": {
                "minimum": min(intervals) if intervals else median_interval_ns,
                "median": median_interval_ns,
                "maximum": max(intervals) if intervals else median_interval_ns,
            },
        },
        "streams": {
            "cameras": camera_streams,
            "joint_state": {
                "source_feature": "observation.state.joint_position",
                "output_topic": JOINT_TOPIC,
                "dimensions": len(episode.states[0]),
            },
            "action": {
                "source_feature": "action",
                "output_topic": ACTION_TOPIC,
                "dimensions": len(episode.actions[0]),
            },
        },
        "robot_model": {
            "source_robot_type": robot_type,
            "dataset_declares_urdf": False,
            "urdf": None,
            "status": "NOT_PRESENT_IN_SOURCE_RECORDING_METADATA",
        },
        "source_metadata": source_info or {},
    }


def write_recording_config(
    destination: Path,
    *,
    request: ConversionRequest,
    episode: EpisodeData,
    source_info: dict[str, Any] | None,
) -> None:
    destination.write_text(
        json.dumps(
            build_recording_config(
                request=request,
                episode=episode,
                source_info=source_info,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def build_manifest(
    *,
    request: ConversionRequest,
    episode: EpisodeData,
    raw_file: Path,
    recording_config_file: Path,
) -> dict[str, object]:
    size, sha256, crc64 = _file_checksums(raw_file)
    config_size, config_sha256, config_crc64 = _file_checksums(recording_config_file)
    identity = _identity(request)
    frame_period_ns = round(1_000_000_000 / episode.fps)
    duration_ns = episode.relative_timestamps_ns[-1] + frame_period_ns
    end_time = request.capture_start + timedelta(microseconds=duration_ns / 1000)
    camera_topics = [camera.topic for camera in CAMERAS]
    actual_topics = [*camera_topics, ACTION_TOPIC, JOINT_TOPIC, SOURCE_TOPIC, STATE_TOPIC]
    topic_schemas = {
        **{camera.topic: "hc.camera.JpegEnvelope" for camera in CAMERAS},
        ACTION_TOPIC: "hc.robot.Action",
        JOINT_TOPIC: "hc.robot.JointState",
        SOURCE_TOPIC: "hc.source.HuggingFace",
        STATE_TOPIC: "hc.robot.JointState",
    }
    return {
        "schema_version": 1,
        "project_id": request.project_id,
        "task_id": request.collection_task_id,
        **identity,
        "pico_instance_id": "hf-lerobot-importer",
        "sequence_no": request.episode_index + 1,
        "robot_id": request.robot_id,
        "start_time": request.capture_start.isoformat().replace("+00:00", "Z"),
        "end_time": end_time.isoformat().replace("+00:00", "Z"),
        "cameras": [
            {
                "camera_id": camera.camera_id,
                "topic": camera.topic,
                "frame_id": camera.frame_id,
                "encoding": "jpeg",
            }
            for camera in CAMERAS
        ],
        "topics": [
            {
                "name": topic,
                "required": topic in REQUIRED_TOPICS,
                "message_encoding": "json",
                "schema_name": topic_schemas[topic],
            }
            for topic in actual_topics
        ],
        "expected_topics": list(REQUIRED_TOPICS),
        "actual_topics": actual_topics,
        "files": [
            {
                "path": RAW_FILE_NAME,
                "size": size,
                "sha256": sha256,
                "crc64": str(crc64),
                "media_type": "application/x-mcap",
                "role": "RAW_MCAP",
            },
            {
                "path": RECORDING_CONFIG_FILE_NAME,
                "size": config_size,
                "sha256": config_sha256,
                "crc64": str(config_crc64),
                "media_type": "application/json",
                "role": "AUXILIARY",
            },
        ],
        "file_size": size,
        "sha256": sha256,
        "crc64": str(crc64),
        "compression": "zstd",
        "recorder_version": RECORDER_VERSION,
        "source_recording": {
            "kind": "HUGGING_FACE_EPISODE",
            "repository": request.repository,
            "resolved_revision": request.resolved_revision,
            "episode_index": request.episode_index,
        },
    }


def validate_package(
    manifest_path: Path,
    raw_file: Path,
    recording_config_file: Path,
) -> None:
    preflight = parse_manifest_bytes(manifest_path.read_bytes())
    manifest = preflight.manifest
    size, sha256, crc64 = _file_checksums(raw_file)
    if (size, sha256, crc64) != (manifest.file_size, manifest.sha256, manifest.crc64):
        raise RuntimeError("generated MCAP does not match its Manifest checksums")
    auxiliary = next(
        (
            item
            for item in manifest.files
            if item.path == RECORDING_CONFIG_FILE_NAME and item.role == "AUXILIARY"
        ),
        None,
    )
    if auxiliary is None or not recording_config_file.is_file():
        raise RuntimeError("generated package is missing its recording configuration")
    if _file_checksums(recording_config_file) != (
        auxiliary.size,
        auxiliary.sha256,
        auxiliary.crc64,
    ):
        raise RuntimeError("recording configuration does not match its Manifest checksums")
    try:
        recording_config = json.loads(recording_config_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("recording configuration is not valid JSON") from exc
    declared_frequency = recording_config.get("clock", {}).get("nominal_frequency_hz")
    if not isinstance(declared_frequency, (int, float)) or declared_frequency <= 0:
        raise RuntimeError("recording configuration does not declare a valid frequency")

    def decode_json(_schema: bytes, message: bytes) -> object:
        return json.loads(message)

    verifier = McapVerifier(
        _LocalFileStorage(),
        decoder=RegisteredDecoderProbe({("json", "jsonschema"): decode_json}),
    )
    report = verifier.verify(
        rollout_id=manifest.rollout_id,
        object_key=str(raw_file),
        source_sha256=manifest.sha256,
        required_topics=set(manifest.expected_topics),
        known_optional_topics=set(manifest.actual_topics) - set(manifest.expected_topics),
    )
    if report.status is not VerificationStatus.VERIFIED:
        codes = ", ".join(item.code.value for item in report.findings)
        raise RuntimeError(f"generated MCAP failed platform verification: {codes}")
    with raw_file.open("rb") as stream:
        observed_topics = {
            channel.topic for _schema, channel, _message in make_reader(stream).iter_messages()
        }
    if observed_topics != set(manifest.actual_topics):
        raise RuntimeError("generated MCAP topic inventory differs from its Manifest")


def write_package(
    *,
    request: ConversionRequest,
    episode: EpisodeData,
    camera_frames: dict[str, tuple[Path, ...]],
    source_info: dict[str, Any] | None = None,
) -> Path:
    identity = _identity(request)
    package_dir = request.output_root / identity["data_package_id"]
    if package_dir.exists():
        manifest_path = package_dir / MANIFEST_FILE_NAME
        raw_file = package_dir / RAW_FILE_NAME
        recording_config_file = package_dir / RECORDING_CONFIG_FILE_NAME
        if (
            not manifest_path.is_file()
            or not raw_file.is_file()
            or not recording_config_file.is_file()
        ):
            raise RuntimeError(f"incomplete output package already exists: {package_dir}")
        existing = parse_manifest_bytes(manifest_path.read_bytes()).manifest
        if (
            existing.project_id != request.project_id
            or existing.task_id != request.collection_task_id
            or existing.data_package_id != identity["data_package_id"]
        ):
            raise RuntimeError(f"output package identity does not match: {package_dir}")
        validate_package(manifest_path, raw_file, recording_config_file)
        print(f"reuse validated package {package_dir.name}", file=sys.stderr)
        return package_dir
    request.output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{package_dir.name}.", dir=request.output_root))
    try:
        raw_file = staging / RAW_FILE_NAME
        write_mcap(raw_file, request=request, episode=episode, camera_frames=camera_frames)
        recording_config_file = staging / RECORDING_CONFIG_FILE_NAME
        write_recording_config(
            recording_config_file,
            request=request,
            episode=episode,
            source_info=source_info,
        )
        manifest = build_manifest(
            request=request,
            episode=episode,
            raw_file=raw_file,
            recording_config_file=recording_config_file,
        )
        manifest_path = staging / MANIFEST_FILE_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        validate_package(manifest_path, raw_file, recording_config_file)
        os.replace(staging, package_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return package_dir


def _convert_episode(
    args: argparse.Namespace,
    *,
    repository: str,
    requested_revision: str,
    resolved_revision: str,
    episode_index: int,
    cache_root: Path,
    output_root: Path,
) -> Path:
    print(
        f"source {repository}@{resolved_revision} episode {episode_index}",
        file=sys.stderr,
    )
    layout = acquire_source(
        repository=repository,
        revision=resolved_revision,
        episode_index=episode_index,
        cache_root=cache_root,
    )
    fps_value = layout.info.get("fps")
    if not isinstance(fps_value, (int, float)) or not 0 < float(fps_value) <= 240:
        raise RuntimeError("LeRobot info.json contains an invalid fps value")
    episode = load_episode_data(layout.data_file, episode_index, float(fps_value))
    capture_start = cast(datetime, args.capture_start) + timedelta(days=episode_index)
    request = ConversionRequest(
        project_id=cast(str, args.project_id),
        collection_task_id=cast(str, args.collection_task_id),
        episode_index=episode_index,
        repository=repository,
        requested_revision=requested_revision,
        resolved_revision=resolved_revision,
        robot_id=cast(str, args.robot_id),
        output_root=output_root,
        capture_start=capture_start,
    )
    identity = _identity(request)
    existing = output_root / identity["data_package_id"]
    if existing.exists():
        return write_package(
            request=request,
            episode=episode,
            camera_frames={},
            source_info=layout.info,
        )
    with tempfile.TemporaryDirectory(prefix="hc-hf-droid-frames-") as temporary:
        frames = extract_camera_frames(layout, episode, Path(temporary))
        return write_package(
            request=request,
            episode=episode,
            camera_frames=frames,
            source_info=layout.info,
        )


def convert(args: argparse.Namespace) -> tuple[Path, ...]:
    repository = cast(str, args.repository)
    requested_revision = cast(str, args.revision)
    resolved = _resolved_revision(repository, requested_revision)
    cache_root = cast(Path, args.cache_dir).expanduser().resolve()
    output_root = cast(Path, args.output_dir).expanduser().resolve()
    if cast(bool, args.include_compatible_robot_model):
        robot_assets = acquire_robot_model_assets(
            cache_root=cache_root,
            output_root=output_root,
        )
        print(f"robot model {robot_assets}", file=sys.stderr)
    indexes = (
        range(cast(int, args.episode_count))
        if cast(bool, args.all_episodes)
        else (cast(int, args.episode),)
    )
    return tuple(
        _convert_episode(
            args,
            repository=repository,
            requested_revision=requested_revision,
            resolved_revision=resolved,
            episode_index=episode_index,
            cache_root=cache_root,
            output_root=output_root,
        )
        for episode_index in indexes
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download one or all three-camera DROID episodes and create "
            "independent "
            "HC Data Platform MCAP + rollout_manifest.json upload folder."
        )
    )
    parser.add_argument("--project-id", required=True, help="exact current platform project ID")
    parser.add_argument(
        "--collection-task-id",
        required=True,
        help="collection task UUID written to Manifest task_id",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--episode", type=int, default=0, help="DROID episode index")
    selection.add_argument(
        "--all-episodes",
        action="store_true",
        help="convert the first --episode-count episodes (100 by default)",
    )
    parser.add_argument(
        "--episode-count",
        type=int,
        default=100,
        help="number of episodes for --all-episodes (maximum 100)",
    )
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--robot-id", default="droid-franka")
    parser.add_argument(
        "--include-compatible-robot-model",
        action="store_true",
        help=(
            "pull a pinned third-party Franka + Robotiq compatibility preview; "
            "the DROID recording does not supply an official URDF"
        ),
    )
    parser.add_argument(
        "--capture-start",
        type=_parse_datetime,
        default=DEFAULT_CAPTURE_START,
        help="UTC base time; episode index days are added for stable identities",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".cache/huggingface/hc-droid-import"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/hf-droid-mcap"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.episode < 0:
        parser.error("--episode must be non-negative")
    if not 1 <= args.episode_count <= 100:
        parser.error("--episode-count must be between 1 and 100")
    try:
        packages = convert(args)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for package in packages:
        print(package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
