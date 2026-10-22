"""Authorized recording windows -> independent RGB-D package preparation.

Only committed recording assets enter this worker-local path. Publication uses
the Episode store; public READY authorization is never used to read input assets.
"""

import hashlib
import json
import math
from contextlib import contextmanager
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

import av

from hc_data_platform import recording_fields
from hc_data_platform.data_formats.depth_evidence import file_hash
from hc_data_platform.data_formats.feature_names import feature_names
from hc_data_platform.data_formats.holobrain_depth import ENCODER_PROFILE
from hc_data_platform.data_formats.rgb_video import RgbVideoWriter
from hc_data_platform.episode_store.bundle_models import FrozenObject
from hc_data_platform.episode_store.depth_window import VERSION as WINDOW_VERSION
from hc_data_platform.episode_store.depth_window import prepare_depth_window
from hc_data_platform.episode_store.frame_window import DepthWindow
from hc_data_platform.episode_store.models import EpisodeIdentity, FileEntry, PackageInput, digest
from hc_data_platform.episode_store.native_plan import NativeFile, NativeVideo
from hc_data_platform.episode_store.prepared_assets import PreparedAssets, PreparedVideo
from hc_data_platform.episode_store.recording_plan import (
    RecordingEpisodePlan,
    recording_request_hash,
)
from hc_data_platform.episode_store.validation import parquet_rows
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot

from .asset_models import RecordingAsset, RecordingAssetRole, RecordingUpload
from .openarm_grid import frame_at, frame_time

VERSION = "continuous-episode-package/3"


def timestamp_ns(value: datetime):
    return int(value.timestamp()) * 1_000_000_000 + value.microsecond * 1000


def encoding_configuration():
    return {
        "depth_window": WINDOW_VERSION,
        "depth_encoder_profile": ENCODER_PROFILE,
        "rgb_window": "openarm-training-h264-crf18-veryfast/1",
        "pyav": av.__version__,
        "ffmpeg": {key: list(value) for key, value in av.library_versions.items()},
        "depth_requantized": False,
    }


def inventory(request):
    return tuple(
        sorted(
            (
                NativeFile(
                    relative_path=item.relative_path,
                    object=FrozenObject(item.object_key, item.size_bytes, item.content_sha256),
                )
                for item in request.assets
            ),
            key=lambda item: item.relative_path,
        )
    )


@contextmanager
def recording_files(request, connections, storage):
    """Recheck full tenant + finalized slice + committed upload before localization."""
    source = request.projection
    from hc_data_platform.core.context import current_request_context

    context = current_request_context()
    scope = (source.organization_id, source.project_id, source.region_code)
    if scope != (context.organization_id, context.project_id, context.region_code) or not (
        context.service_identity
    ):
        raise PermissionError("recording preparation requires its exact worker scope")
    connection = connections()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT u.upload_document,p.start_offset_ns,p.end_offset_ns
                FROM ingest.recording_uploads u
                JOIN ingest.continuous_recordings r ON r.organization_id=u.organization_id
                  AND r.project_id=u.project_id AND r.region_code=u.region_code
                  AND r.recording_upload_id=u.upload_id
                JOIN ingest.recording_episode_processing p ON p.organization_id=r.organization_id
                  AND p.project_id=r.project_id AND p.region_code=r.region_code
                  AND p.recording_id=r.recording_id
                WHERE u.organization_id=%s AND u.project_id=%s AND u.region_code=%s
                  AND u.upload_id=%s AND r.recording_id=%s AND p.episode_id=%s
                  AND u.status='COMMITTED'""",
                (*scope, source.recording_upload_id, source.recording_id, source.episode_id),
            )
            row = cursor.fetchone()
            if row is None or (row[1], row[2]) != (request.start_offset_ns, request.end_offset_ns):
                raise PermissionError("recording window differs from committed source ledger")
            upload = RecordingUpload.model_validate(row[0])
            if (
                upload.command.recording_config != request.recording_config
                or upload.command.robot_id != source.robot_id
                or upload.command.collection_task_id != source.collection_task_id
            ):
                raise PermissionError("recording identity/configuration differs from workflow")
            cursor.execute(
                """SELECT asset_document FROM ingest.recording_upload_assets
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND upload_id=%s AND status='COMMITTED' ORDER BY asset_path""",
                (*scope, source.recording_upload_id),
            )
            assets = [RecordingAsset.model_validate(item[0]) for item in cursor.fetchall()]
    finally:
        connection.close()
    expected = {
        (
            str(item.asset_id),
            item.manifest.path,
            item.manifest.role,
            item.manifest.camera_id,
            item.manifest.media_type,
            item.object_key,
            item.manifest.size,
            item.manifest.sha256,
        )
        for item in assets
    }
    actual = {
        (
            item.asset_id,
            item.relative_path,
            item.role,
            item.camera_id,
            item.media_type,
            item.object_key,
            item.size_bytes,
            item.content_sha256,
        )
        for item in request.assets
    }
    if expected != actual or len(expected) != len(request.assets):
        raise PermissionError("recording assets differ from complete committed upload")
    frozen = inventory(request)  # Validates every path, including unread originals.
    roles = {item.relative_path: item.role for item in request.assets}
    with TemporaryDirectory(prefix="recording-package-") as temporary:
        root = Path(temporary)
        paths = {}
        for file in frozen:
            # Originals stay immutable in Raw. The selected provenance file is
            # copied below; robot archives and whole MCAP are not new primaries.
            if (
                request.recording_config.recorder_version
                in {"openarm-session/v2", "native-mcap-recording/1", "native-mcap-recording/2"}
                and roles[file.relative_path] is RecordingAssetRole.AUXILIARY
                and file.relative_path.startswith("source/original/")
            ):
                continue
            path = root / file.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            size, sha = 0, hashlib.sha256()
            with path.open("xb") as output:
                for block in storage.read_chunks(file.object.key):
                    size += len(block)
                    if size > file.object.size:
                        raise ValueError("recording object exceeds committed receipt")
                    sha.update(block)
                    output.write(block)
            if (size, sha.hexdigest()) != (file.object.size, file.object.sha256):
                raise ValueError("recording object differs from committed hash/size")
            paths[file.relative_path] = path
        yield upload, paths


def read_json(path):
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("recording JSON evidence exceeds the bounded document limit")
    return json.loads(path.read_bytes())


def source_context(request, upload, paths):
    config_asset = next(
        item for item in request.assets if item.role is RecordingAssetRole.RECORDING_CONFIG
    )
    if (
        type(request.recording_config).model_validate(read_json(paths[config_asset.relative_path]))
        != request.recording_config
    ):
        raise ValueError("recording config bytes differ from committed command")
    origin = timestamp_ns(upload.command.capture_started_at)
    facts = {
        "capture_started_at": upload.command.capture_started_at.isoformat(),
        "recording_origin_ns": origin,
    }
    media = {}
    if "recording-media.json" in paths:
        declaration = read_json(paths["recording-media.json"])
        if not isinstance(declaration, dict) or declaration.get("format") != "hc-recording-media/1":
            raise ValueError("unsupported recording media declaration")
        media = declaration.get("cameras")
        if not isinstance(media, dict) or set(media) != {
            camera.camera_id for camera in request.recording_config.cameras
        }:
            raise ValueError("recording media declaration requires every configured camera")
        facts["media_contract"] = "recording-media.json"
    elif request.recording_config.recorder_version == "openarm-session/v2":
        provenance = read_json(paths["capture-provenance.json"])
        if provenance["processor_version"] not in {
            "openarm-raw-platform/3",
            "openarm-raw-platform/4",
            "openarm-raw-platform/5",
            "openarm-raw-platform/6",
            "openarm-raw-platform/7",
            "openarm-raw-platform/8",
            "openarm-raw-platform/9",
            "openarm-raw-platform/10",
            "openarm-raw-platform/11",
        }:
            raise ValueError("recording derivation requires an explicit media contract")
        originals = {item.relative_path: item for item in request.assets}
        if (
            originals["source/original/manifest.json"].content_sha256
            != provenance["original_manifest_sha256"]
        ):
            raise ValueError("recording grid provenance differs from original manifest")
        origin = provenance["source_t0_ns"]
        if provenance.get("timeline_mode") == "primary_camera":
            # Version 9 associates against actual primary exposures, then writes
            # a CFR export starting at the first exposure. The record-button
            # timestamp is no longer the origin of that export grid. Read its
            # exact integer origin from the already hash-verified frame trace.
            if provenance["processor_version"] not in {
                "openarm-raw-platform/9",
                "openarm-raw-platform/10",
                "openarm-raw-platform/11",
            }:
                raise ValueError("unsupported primary-camera media contract")
            with paths["source/frames.jsonl"].open() as stream:
                first_line = stream.readline(16 * 1024 * 1024 + 1)
            if len(first_line) > 16 * 1024 * 1024:
                raise ValueError("recording frame evidence exceeds the bounded document limit")
            first = json.loads(first_line)
            origin = first["row"]["export_grid_origin_ns"]
            if (
                type(origin) is not int
                or not provenance["source_t0_ns"] <= origin < provenance["source_end_ns"]
                or first["source_frame"] != 0
                or first["offset_ns"] != 0
                or first["row"]["export_target_time_ns"] != origin
                or first["row"]["target_time_ns"] != origin
            ):
                raise ValueError("recording primary-camera grid differs from frame evidence")
            facts["timeline_mode"] = "primary_camera"
        if type(origin) is not int or not 0 <= origin < 2**63:
            raise ValueError("recording source grid requires exact integer nanoseconds")
        facts.update(
            source_grid_origin_ns=origin,
            capture_to_source_grid_offset_ns=origin - facts["recording_origin_ns"],
            grid_provenance="capture-provenance.json",
        )
        # This adapter follows the versioned producer's exact topic contract.
        # It does not infer depth from HEVC alone or from a camera name suffix.
        for camera in request.recording_config.cameras:
            for source_id in provenance["depth_sources"]:
                if camera.modality_key == recording_fields.image_key(source_id, True):
                    media[camera.camera_id] = {
                        "kind": "depth",
                        "video_path": f"videos/{source_id}_depth.mp4",
                        "evidence_path": f"hc/depth/{source_id}",
                    }
    videos = {
        item.camera_id: item.relative_path
        for item in request.assets
        if item.role is RecordingAssetRole.RAW_VIDEO
    }
    for camera, item in media.items():
        if (
            not isinstance(item, dict)
            or item.get("kind") not in {"rgb", "depth"}
            or item.get("video_path") != videos.get(camera)
        ):
            raise ValueError("recording media declaration differs from committed camera assets")
        if item["kind"] == "depth":
            prefix = item.get("evidence_path")
            if not isinstance(prefix, str):
                raise ValueError("depth declaration requires a committed HC evidence directory")
            FileEntry(
                path=prefix + "/profile.json",
                size=1,
                sha256="0" * 64,
                media_type="application/json",
            )
            if any(
                prefix + "/" + name not in paths
                for name in (
                    "profile.json",
                    "report.json",
                    "frames.parquet",
                    "validity/flags.parquet",
                )
            ):
                raise ValueError("depth declaration omits required committed evidence")
    return origin, facts, media


def timeline(request, origin):
    if request.recording_config.recorder_version in {
        "openarm-session/v2",
        "native-mcap-recording/1",
        "native-mcap-recording/2",
    }:
        first, stop = (
            frame_at(request.start_offset_ns, request.alignment_profile.frequency_hz),
            frame_at(request.end_offset_ns, request.alignment_profile.frequency_hz),
        )
        if first >= stop:
            raise ValueError("recording slice has no canonical frames")
        return origin + frame_time(first, request.alignment_profile.frequency_hz), frame_time(
            stop - first, request.alignment_profile.frequency_hz
        )
    return origin + request.start_offset_ns, request.end_offset_ns - request.start_offset_ns


def freeze_plan(request, alignment, manifest, staging, upload, paths, quality, *, schemas=None):
    source = request.projection
    files = {file.relative_path: file for file in inventory(request)}
    origin, clock, declared = source_context(request, upload, paths)
    if alignment.data.start_ns != timeline(request, origin)[0]:
        raise ValueError("recording alignment uses a different source clock")
    mapping = feature_names(sorted(request.alignment_profile.required_modalities))
    cameras = {camera.camera_id: camera for camera in request.recording_config.cameras}
    videos, windows = {}, {}
    for asset in request.assets:
        if asset.role is not RecordingAssetRole.RAW_VIDEO:
            continue
        camera = cameras[asset.camera_id]
        target = mapping[camera.modality_key]
        source_file = files[asset.relative_path]
        with av.open(str(paths[asset.relative_path])) as container:
            stream = container.streams.video[0]
            fps = Fraction(stream.average_rate)
            width, height = stream.width, stream.height
            pix_fmt, codec, count = (
                stream.codec_context.format.name,
                stream.codec_context.name,
                stream.frames,
            )
        if (
            fps != Fraction(str(camera.fps))
            or width != camera.width
            or height != camera.height
            or codec != camera.codec
            or count <= 0
        ):
            raise ValueError("recording video header differs from frozen camera configuration")
        declaration = declared.get(camera.camera_id)
        evidence = {}
        info = {
            "video.fps": float(fps),
            "video.codec": codec,
            "video.pix_fmt": pix_fmt,
            "video.channels": 3,
            "has_audio": False,
        }
        if declaration is not None and declaration["kind"] == "depth":
            if declaration["video_path"] != asset.relative_path:
                raise ValueError("depth contract names a different committed video")
            prefix = declaration["evidence_path"] + "/"
            evidence = {
                name[len(prefix) :]: file for name, file in files.items() if name.startswith(prefix)
            }
            report = read_json(paths[prefix + "report.json"])
            profile = read_json(paths[prefix + "profile.json"])
            if (
                report["video_encoding"]["video_sha256"] != source_file.object.sha256
                or not report.get("source_contract_required")
                or not report.get("source_profiles")
            ):
                raise ValueError("recording depth requires verified source units and video binding")
            info = profile["feature_info"]
        elif pix_fmt.startswith("gray"):
            raise ValueError("recording grayscale depth requires an explicit HC media contract")
        feature = {
            "dtype": "video",
            "shape": [height, width, info["video.channels"]],
            "names": ["height", "width", "channels"],
            "info": info,
        }
        videos[target] = NativeVideo(source=source_file, feature=feature, evidence=evidence)
        offset = request.start_offset_ns - camera.capture_start_offset_ns
        stop = request.end_offset_ns - camera.capture_start_offset_ns
        if offset < 0 or stop <= offset:
            raise ValueError("recording camera does not cover the selected window")
        first = round(Fraction(offset, 1_000_000_000) * fps)
        windows[target] = DepthWindow(
            first,
            min(count, round(Fraction(stop, 1_000_000_000) * fps)),
            manifest.row_count,
            request.alignment_profile.frequency_hz,
            alignment.data.start_ns,
            source_start_offset_ns=offset,
        )
    ident = EpisodeIdentity(
        organization_id=source.organization_id,
        project_id=source.project_id,
        region_code=source.region_code,
        dataset_id=request.dataset_id,
        episode_id="episode_" + hashlib.sha256(source.rollout_id.encode()).hexdigest()[:32],
        rollout_id=source.rollout_id,
    )
    schema = DatasetSchemaSnapshot.create(
        project_id=source.project_id,
        dataset_id=request.dataset_id,
        schema_snapshot_id=request.schema_snapshot_id,
        frequency_hz=request.alignment_profile.frequency_hz,
        fields={key: "json" for key in mapping},
    )
    if request.capture_snapshot is not None:
        from hc_data_platform.capture_schemes.schema import semantic_schema

        schema = semantic_schema(
            source.project_id,
            request.dataset_id,
            request.capture_snapshot["output"],
            {key: "json" for key in mapping},
        )
        if schemas is not None:
            # Resolve against the immutable server receipt again at packaging.
            # A caller-supplied legacy ID alone never authorizes schema reuse.
            schema = schemas.resolve_capture_schema(schema)
    metadata = {
        "capture": request.capture_snapshot,
        "capture_validation": request.capture_validation,
        "task": source.collection_task_id,
        "source": {
            "recording_id": source.recording_id,
            "recording_upload_id": source.recording_upload_id,
            "slice_id": source.episode_id,
            "start_offset_ns": request.start_offset_ns,
            "end_offset_ns": request.end_offset_ns,
            "clock": clock,
            "inventory": [file.model_dump(mode="json") for file in files.values()],
        },
        "quality": quality,
        **({"quality_rules": quality["rule_evidence"]} if quality.get("rule_evidence") else {}),
        "calibration": {"status": "UNKNOWN", "reason": "no validated calibration declared"},
    }
    if "mcap-provenance.json" in paths:
        metadata["source"]["mcap"] = read_json(paths["mcap-provenance.json"])
    recording_evidence = []
    if "source/frames.jsonl" in files:
        recording_evidence.append("hc/recording/source_frames.jsonl")
    calibration = [
        item.relative_path for item in request.assets if item.role is RecordingAssetRole.CALIBRATION
    ]
    if calibration:
        metadata["calibration"] = {
            "status": "PROVIDED_UNVERIFIED",
            "files": ["hc/recording/calibration/" + name for name in calibration],
            "validation": "original declaration preserved; calibration correctness not inferred",
        }
        recording_evidence.extend(metadata["calibration"]["files"])
    metadata["source"]["recording_evidence"] = recording_evidence
    plan = RecordingEpisodePlan.freeze(
        input=PackageInput(
            identity=ident,
            source_sha256=source.source_sha256,
            processor_version=VERSION,
            configuration_sha256="0" * 64,
            schema_snapshot_id=request.schema_snapshot_id,
            required_features=tuple(sorted(mapping.values())),
            required_depth_features=tuple(
                sorted(key for key, video in videos.items() if video.evidence)
            ),
            robot_id=source.robot_id,
            task_id=source.collection_task_id,
            recording_date=upload.command.capture_started_at.date().isoformat(),
        ),
        schema_snapshot=schema,
        metadata=metadata,
        alignment_sha256=staging.content_sha256,
        alignment_version=staging.alignment_version,
        alignment_profile=request.alignment_profile,
        frame_count=manifest.row_count,
        feature_mapping={
            camera.modality_key: mapping[camera.modality_key] for camera in cameras.values()
        },
        videos=videos,
        request_sha256=recording_request_hash(request),
        inventory=tuple(files.values()),
        windows=windows,
        encoding=encoding_configuration(),
    )
    plan.validate_recording_bundle(request, alignment, manifest, staging)
    return plan


def prepare_rgb_window(media, window, destination):
    """Copy an identical whole stream or use the existing RGB clip encoder."""
    fps = Fraction(str(media.feature["info"]["video.fps"]))
    if window.source_frame(0, fps) != window.first_source_frame:
        raise ValueError("RGB window origin differs from frozen frame selection")
    with av.open(str(media.path)) as container:
        stream = container.streams.video[0]
        count = stream.frames
        whole = (
            window.first_source_frame == 0
            and window.stop_source_frame == count
            and window.frame_count == count
            and Fraction(str(window.frequency_hz)) == fps
            and window.source_start_offset_ns in (None, 0)
        )
        if (
            window.stop_source_frame > count
            or window.source_frame(window.frame_count - 1, fps) >= window.stop_source_frame
        ):
            raise ValueError("RGB frame selection extends beyond its frozen source window")
        writer = (
            None
            if whole
            else RgbVideoWriter(destination, window.frequency_hz, media.feature["shape"])
        )
        index = 0
        try:
            observed = 0
            for frame in container.decode(stream):
                if frame.pts is None or abs(
                    Fraction(frame.pts) * frame.time_base - Fraction(observed) / fps
                ) > Fraction(1, 1_000_000):
                    raise ValueError("RGB video is outside its declared constant frame grid")
                while index < window.frame_count and window.source_frame(index, fps) == observed:
                    if writer is not None:
                        writer.write(frame.to_ndarray(format="rgb24"))
                    index += 1
                observed += 1
            if observed != count or index != window.frame_count:
                raise ValueError("RGB video did not supply every selected frame")
        finally:
            if writer is not None:
                writer.close()
    if whole:
        return media
    feature = {
        **media.feature,
        "info": {
            **media.feature["info"],
            "video.codec": "h264",
            "video.pix_fmt": "yuv420p",
            "video.fps": window.frequency_hz,
        },
    }
    return PreparedVideo(destination, file_hash(destination), destination.stat().st_size, feature)


@contextmanager
def prepared_recording_assets(request, plan, paths, attempt_id):
    type(plan).model_validate(plan.model_dump(mode="json"))
    if (
        plan.request_sha256 != recording_request_hash(request)
        or plan.inventory != inventory(request)
        or plan.encoding != encoding_configuration()
    ):
        raise ValueError("recording asset preparation differs from frozen source/encoding version")
    with TemporaryDirectory(prefix="recording-window-") as temporary:
        root = Path(temporary)
        videos = {}
        for feature, descriptor in plan.videos.items():
            entries = tuple(
                FileEntry(
                    path=relative,
                    size=file.object.size,
                    sha256=file.object.sha256,
                    media_type="application/octet-stream",
                )
                for relative, file in descriptor.evidence.items()
            )
            evidence_roots = {
                paths[file.relative_path].as_posix().removesuffix(relative)
                for relative, file in descriptor.evidence.items()
            }
            if len(evidence_roots) > 1:
                raise ValueError("recording depth evidence must share one source directory")
            media = PreparedVideo(
                paths[descriptor.source.relative_path],
                descriptor.source.object.sha256,
                descriptor.source.object.size,
                descriptor.feature,
                Path(next(iter(evidence_roots))) if evidence_roots else None,
                entries,
            )
            window = plan.windows[feature]
            destination = root / digest(feature)
            videos[feature] = (
                prepare_depth_window(
                    media,
                    window,
                    destination,
                    preserve_association_reference=bool(
                        plan.metadata.get("quality", {}).get("capture_quality")
                    ),
                )
                if entries
                else prepare_rgb_window(media, window, destination / "rgb.mp4")
            )
        extra = []
        for relative in plan.metadata["source"]["recording_evidence"]:
            output = root / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            if relative == "hc/recording/source_frames.jsonl":
                first, stop = (
                    frame_at(request.start_offset_ns, request.alignment_profile.frequency_hz),
                    frame_at(request.end_offset_ns, request.alignment_profile.frequency_hz),
                )
                count = 0
                with (
                    paths["source/frames.jsonl"].open("rb") as original,
                    output.open("xb") as target,
                ):
                    for index, line in enumerate(original):
                        if len(line) > 16 * 1024 * 1024:
                            raise ValueError("recording source frame evidence row is too large")
                        document = json.loads(line)
                        if document["source_frame"] != index:
                            raise ValueError("recording source frame trace is not contiguous")
                        if first <= index < stop:
                            target.write(line)
                            count += 1
                        if index >= stop:
                            break
                if count != plan.frame_count:
                    raise ValueError("recording trace does not cover every Episode frame")
            else:
                import shutil

                original = relative.removeprefix("hc/recording/calibration/")
                shutil.copyfile(paths[original], output)
            extra.append(
                FileEntry(
                    path=relative,
                    size=output.stat().st_size,
                    sha256=file_hash(output),
                    media_type="application/octet-stream",
                )
            )
        yield PreparedAssets(plan.input.identity, attempt_id, videos, root, tuple(extra))


def capture_frame_rows(assets):
    path = assets.auxiliary_root / "hc/recording/source_frames.jsonl"
    with path.open() as stream:
        for line in stream:
            yield json.loads(line)


def recording_steps(steps, plan, assets):
    """Bind numeric rows to selected physical depth evidence, preserving source refs."""
    traces = {
        feature: iter(parquet_rows(media.evidence_root / "frames.parquet"))
        for feature, media in assets.videos.items()
        if media.evidence_root is not None
    }
    capture = (
        iter(capture_frame_rows(assets))
        if assets.auxiliary_root is not None
        and (assets.auxiliary_root / "hc/recording/source_frames.jsonl").is_file()
        else None
    )
    count = 0
    for step in steps:
        if step.step_index != count or count >= plan.frame_count:
            raise ValueError("recording alignment rows differ from frozen Episode window")
        updates = {
            name: dict(getattr(step, name))
            for name in ("modalities", "source_timestamps_ns", "time_error_ns", "valid", "repeated")
        }
        capture_valid = True
        if capture is not None:
            document = next(capture, None)
            if document is None:
                raise ValueError("capture validity trace ended early")
            capture_valid = document["valid"]
            row = document["row"]
            for topic, value in step.modalities.items():
                valid = updates["valid"].get(topic, False)
                if topic in {"/observation/state", "/action"}:
                    valid = (
                        valid
                        and isinstance(value, dict)
                        and bool(value.get("values"))
                        and all(
                            type(v) in (int, float) and math.isfinite(v)
                            for v in value.get("values", [])
                        )
                    )
                elif topic == "source.timestamp_ns":
                    valid = valid and type(value) is int
                elif isinstance(value, dict) and "camera_id" in value:
                    camera_id = value["camera_id"]
                    stream = "depth" if camera_id.endswith("_depth") else "rgb"
                    camera_id = camera_id.removesuffix("_depth")
                    # Native MCAP IDs are declared by the instance. Only old
                    # OpenArm traces use camera_hand for the head video.
                    source_id = (
                        "camera_hand"
                        if camera_id == "head" and camera_id not in row["images"]
                        else camera_id
                    )
                    valid = valid and stream in row["images"].get(source_id, {})
                updates["valid"][topic] = bool(valid)
        for topic, feature in plan.feature_mapping.items():
            window, video = plan.windows[feature], plan.videos[feature]
            selected = window.source_frame(count, Fraction(str(video.feature["info"]["video.fps"])))
            value = step.modalities[topic]
            if (
                step.timestamp_ns != window.target_time(count)
                or not isinstance(value, dict)
                or value.get("source_frame_index") != selected
            ):
                raise ValueError("recording alignment camera selection differs from package window")
            if feature in traces:
                row = next(traces[feature], None)
                if (
                    row is None
                    or row["frame_index"] != count
                    or row["target_timestamp_ns"] != step.timestamp_ns
                ):
                    raise ValueError("depth provenance differs from the aligned Episode frame")
                updates["source_timestamps_ns"][topic] = (
                    () if row["missing"] else (row["source_timestamp_ns"],)
                )
                updates["time_error_ns"][topic] = row["time_error_ns"]
                updates["repeated"][topic] = row["repeated"]
                updates["valid"][topic] = (
                    updates["valid"].get(topic, False)
                    and not row["missing"]
                    and not row["time_threshold_exceeded"]
                )
                updates["modalities"][topic] = {
                    **value,
                    "depth_source_id": row["source_id"],
                    "depth_source_seq": row["source_seq"],
                }
        yield step.model_copy(
            update={
                **updates,
                "sample_valid": capture_valid
                and step.sample_valid
                and all(updates["valid"].values()),
            }
        )
        count += 1
    if count != plan.frame_count or any(next(rows, None) is not None for rows in traces.values()):
        raise ValueError("recording alignment/provenance frame count mismatch")
