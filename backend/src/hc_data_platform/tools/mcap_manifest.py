"""Build upload manifests and an explicit processing plan for existing MCAP files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcap.reader import make_reader

from hc_data_platform.ingest.manifest import parse_manifest_bytes
from hc_data_platform.ingest.models import RolloutManifestV1
from hc_data_platform.ingest.ports import crc64_ecma
from hc_data_platform.quality.models import QualityProfileV1, TopicTimingProfileV1
from hc_data_platform.verification import McapRos2DecoderProbe


def checksums(path: Path) -> tuple[int, str, int]:
    digest, size, crc = hashlib.sha256(), 0, 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024**2), b""):
            digest.update(chunk)
            size += len(chunk)
            crc = crc64_ecma(chunk, crc)
    return size, digest.hexdigest(), crc


def build_manifest(
    path: Path,
    *,
    project_id: str,
    task_id: str,
    robot_id: str,
    sequence_no: int = 1,
    depth_unit: str | None = None,
) -> RolloutManifestV1:
    size, sha, crc = checksums(path)
    with path.open("rb") as stream:
        reader = make_reader(stream, validate_crcs=True)
        summary = reader.get_summary()
        if summary is None or summary.statistics is None:
            raise ValueError("MCAP requires a complete readable summary before manifest generation")
        cameras = {}
        topics = {}
        for channel in summary.channels.values():
            schema = summary.schemas.get(channel.schema_id)
            if schema is None:
                raise ValueError(f"topic {channel.topic!r} has no schema")
            topics[channel.topic] = {
                "name": channel.topic,
                "message_encoding": channel.message_encoding,
                "schema_name": schema.name,
                "required": False,
            }
            schema_name = schema.name.replace("/msg/", "/")
            if schema_name in {"sensor_msgs/CompressedImage", "foxglove_msgs/CompressedVideo"}:
                cameras[channel.topic] = {
                    "camera_id": "camera-"
                    + hashlib.sha256(channel.topic.encode()).hexdigest()[:16],
                    "topic": channel.topic,
                }
        probe = McapRos2DecoderProbe()
        missing = set(cameras)
        if missing:
            for schema, channel, message in reader.iter_messages(topics=sorted(missing)):
                if channel.topic not in missing:
                    continue
                value = probe.probe(
                    message_encoding=channel.message_encoding,
                    schema_encoding=schema.encoding,
                    schema_name=schema.name,
                    schema_data=schema.data,
                    message_data=message.data,
                )
                fmt = str(getattr(value, "format", getattr(value, "codec", "")))
                cameras[channel.topic]["encoding"] = fmt
                if "16UC1" in fmt and depth_unit is not None:
                    cameras[channel.topic]["depth_unit"] = depth_unit
                missing.remove(channel.topic)
                if not missing:
                    break
        required = set(cameras) | {
            name
            for name in topics
            if any(marker in name.lower() for marker in ("joint_states", "joint_cmd", "/action"))
        }
        if not required:
            required = set(topics)
        for name in required:
            topics[name]["required"] = True
        start_ns = summary.statistics.message_start_time
        end_ns = summary.statistics.message_end_time
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    identity = hashlib.sha256(f"{project_id}:{task_id}:{robot_id}:{sha}".encode()).hexdigest()[:32]
    manifest = RolloutManifestV1(
        project_id=project_id,
        task_id=task_id,
        robot_id=robot_id,
        collection_job_id="mcap-job-" + identity,
        rollout_id="mcap-" + identity,
        collection_session_id="mcap-session-" + identity,
        recording_request_id="mcap-request-" + identity,
        data_package_id="mcap-package-" + identity,
        sequence_no=sequence_no,
        start_time=epoch + timedelta(microseconds=start_ns // 1000),
        end_time=epoch + timedelta(microseconds=end_ns // 1000 + 1),
        cameras=list(cameras.values()),
        topics=[topics[k] for k in sorted(topics)],
        actual_topics=sorted(topics),
        expected_topics=sorted(required),
        files=[
            {
                "path": path.name,
                "size": size,
                "sha256": sha,
                "crc64": crc,
                "role": "RAW_MCAP",
                "media_type": "application/octet-stream",
            }
        ],
        file_size=size,
        sha256=sha,
        crc64=crc,
        compression="none",
        recorder_version="existing-mcap-import/v1",
    )
    parse_manifest_bytes(manifest.model_dump_json().encode())
    return manifest


def build_quality_profile(manifest: RolloutManifestV1) -> QualityProfileV1:
    """One profile per required-topic set, so automatic dispatch is unambiguous.

    This is a format/continuity check, not a declaration of a 30 Hz capture rate.
    Frequency and coverage requirements need a separate acquisition contract.
    Static metadata topics are not periodic sensor streams.
    """
    required = frozenset(manifest.expected_topics)
    identity = hashlib.sha256("\n".join(sorted(required)).encode()).hexdigest()[:16]
    timing = TopicTimingProfileV1(
        minimum_frequency_hz_risk=0,
        minimum_frequency_hz_reject=0,
        minimum_coverage_ratio_risk=0,
        minimum_coverage_ratio_reject=0,
        maximum_consecutive_missing_risk=14,
        maximum_consecutive_missing_reject=29,
    )
    static = timing.model_copy(
        update={
            "maximum_gap_ns_risk": 2**63 - 1,
            "maximum_gap_ns_reject": 2**63 - 1,
            "maximum_consecutive_missing_risk": 2**31 - 1,
            "maximum_consecutive_missing_reject": 2**31 - 1,
        }
    )
    return QualityProfileV1(
        profile_id="mcap-format-" + identity,
        profile_version=2,
        required_topics=required,
        default_timing=timing,
        topic_timing={name: static for name in manifest.actual_topics if name not in required},
        image_profiles={
            camera.topic: {
                "black_luma_threshold": 0,
                "maximum_repeated_frame_ratio_risk": 1,
                "maximum_repeated_frame_ratio_reject": 1,
            }
            for camera in manifest.cameras
            if camera.depth_unit is not None
        },
        action={"minimum_observation_count_risk": 0, "minimum_observation_count_reject": 0},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--depth-unit", choices=["mm"])
    parser.add_argument(
        "--link-source",
        action="store_true",
        help="Hard-link original bytes into each upload package; never rewrite them",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse only packages whose generated manifest is identical",
    )
    args = parser.parse_args()
    paths = sorted(args.source.glob("*.mcap")) if args.source.is_dir() else [args.source]
    if args.limit is not None:
        paths = paths[: args.limit]
    if not paths:
        parser.error("no MCAP files found")
    args.output.mkdir(parents=True, exist_ok=True)
    inventory = []
    for number, path in enumerate(paths, 1):
        manifest = build_manifest(
            path,
            project_id=args.project_id,
            task_id=args.task_id,
            robot_id=args.robot_id,
            sequence_no=number,
            depth_unit=args.depth_unit,
        )
        package = args.output / path.stem
        if package.exists() and args.resume:
            existing = RolloutManifestV1.model_validate_json(
                (package / "rollout_manifest.json").read_text()
            )
            if existing != manifest:
                raise ValueError(f"existing package manifest differs: {package}")
        package.mkdir(exist_ok=args.resume)
        (package / "rollout_manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n")
        if args.link_source:
            target = package / path.name
            if not target.exists():
                os.link(path, target)
            elif not args.resume or not os.path.samefile(path, target):
                raise ValueError(f"existing raw file is not the source hard link: {target}")
        profile = build_quality_profile(manifest)
        plan = {
            "project_id": args.project_id,
            "task_id": args.task_id,
            "robot_id": args.robot_id,
            "rollout_id": manifest.rollout_id,
            "quality_profile": profile.model_dump(mode="json"),
            "schema_fields": {topic: "json" for topic in manifest.actual_topics},
            "frequency_hz": 30,
            "note": (
                "Register task, QC profile and a published Tag Schema binding before dispatch. "
                "Alignment uses a 30 Hz output grid, not a guaranteed source rate. "
                "QC checks format and continuity, not model limits or semantic action quality."
            ),
        }
        (package / "processing-plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
        )
        inventory.append(
            {
                "source": str(path.resolve()),
                "package": str(package.resolve()),
                "sha256": manifest.sha256,
                "rollout_id": manifest.rollout_id,
            }
        )
        print(json.dumps({"completed": number, "total": len(paths), "file": path.name}), flush=True)
    (args.output / "inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
