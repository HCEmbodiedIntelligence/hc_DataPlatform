"""MCAP aligned source pixels -> independent package media with explicit units."""

import hashlib
from contextlib import contextmanager, suppress
from pathlib import Path
from tempfile import TemporaryDirectory

import av
import numpy as np

from hc_data_platform.data_formats.depth_evidence import DepthEvidenceVideo, file_hash
from hc_data_platform.data_formats.depth_source import profile_hash
from hc_data_platform.data_formats.feature_names import feature_names
from hc_data_platform.data_formats.holobrain_depth import DEFAULT_PROFILE, ENCODER_PROFILE
from hc_data_platform.data_formats.mcap_numeric import REVISION, normalize_vector
from hc_data_platform.data_formats.rgb_video import RgbVideoWriter
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot

from .bundle_models import FrozenObject
from .mcap_images import CameraFrame
from .mcap_plan import McapEpisodePlan
from .models import EpisodeIdentity, FileEntry, PackageInput, canonical, digest
from .prepared_assets import PreparedAssets, PreparedVideo

VERSION = "mcap-episode-package/2"
LEGACY_VERSION = "mcap-episode-package/1"


def encoding_configuration():
    return {
        "depth_profile": DEFAULT_PROFILE.document(),
        "depth_encoder": ENCODER_PROFILE,
        "rgb_encoder": "openarm-training-h264-crf18-veryfast/1",
        "pyav": av.__version__,
        "ffmpeg": {key: list(value) for key, value in av.library_versions.items()},
    }


def freeze_mcap_plan(
    session,
    request,
    manifest,
    staging,
    quality,
    *,
    quality_evidence=None,
    numeric_profile=None,
    schemas=None,
):
    source, original = session.source, session.preflight.manifest
    version = request.profile.converter_version
    if version not in {VERSION, LEGACY_VERSION}:
        raise ValueError("unsupported MCAP package processor version")
    numeric = {}
    if version == VERSION:
        if numeric_profile is None:
            raise ValueError("MCAP numeric package requires its frozen signal profile")
        numeric = {
            "revision": REVISION,
            "topics": sorted(
                set((numeric_profile.action.topic, numeric_profile.joint_topic))
                & set(original.actual_topics)
            ),
            "source_file": "hc/recording/mcap_numeric.jsonl",
        }
    topics = {camera.topic for camera in original.cameras}
    if set(session.camera_contracts) != topics:
        raise ValueError("MCAP package lacks a decoded contract for every camera")
    mapping = feature_names(sorted(original.actual_topics), camera_topics=sorted(topics))
    identity = EpisodeIdentity(
        organization_id=source.organization_id,
        project_id=source.project_id,
        region_code=source.region_code,
        dataset_id=request.dataset_id,
        episode_id="episode_" + hashlib.sha256(source.rollout_id.encode()).hexdigest()[:32],
        rollout_id=source.rollout_id,
    )
    schema = DatasetSchemaSnapshot.create(
        project_id=request.project_id,
        dataset_id=request.dataset_id,
        schema_snapshot_id=request.schema_snapshot_id,
        frequency_hz=request.profile.frequency_hz,
        fields={topic: "json" for topic in original.actual_topics},
    )
    capture = getattr(source, "capture_snapshot", None)
    if capture is not None:
        from hc_data_platform.capture_schemes.schema import semantic_schema

        schema = semantic_schema(
            request.project_id,
            request.dataset_id,
            capture["output"],
            {topic: "json" for topic in original.actual_topics},
        )
        if schemas is not None:
            schema = schemas.resolve_capture_schema(schema)
    plan = McapEpisodePlan.freeze(
        input=PackageInput(
            identity=identity,
            source_sha256=source.source_sha256,
            processor_version=version,
            configuration_sha256="0" * 64,
            schema_snapshot_id=request.schema_snapshot_id,
            required_features=tuple(sorted(mapping.values())),
            required_depth_features=tuple(
                sorted(
                    mapping[topic]
                    for topic, contract in session.camera_contracts.items()
                    if contract["kind"] == "depth"
                )
            ),
            robot_id=original.robot_id,
            task_id=original.task_id,
            recording_date=original.start_time.date().isoformat(),
        ),
        schema_snapshot=schema,
        metadata={
            **(
                {
                    "capture": capture,
                    "capture_validation": {
                        "presence": {
                            name: name in mapping.values() for name in capture["output"]["features"]
                        }
                    },
                }
                if capture
                else {}
            ),
            "task": original.task_id,
            "camera_topics": sorted(topics),
            **({"numeric_projection": numeric} if numeric else {}),
            "quality": quality.model_dump(mode="json"),
            **(
                {"quality_rules": quality_evidence.model_dump(mode="json")}
                if quality_evidence
                else {}
            ),
            "calibration": {"status": "UNKNOWN", "reason": "no validated calibration binding"},
            "source": {
                "format": "mcap",
                **({"numeric_projection": numeric} if numeric else {}),
                "session_id": source.session_id,
                "manifest_fingerprint": source.manifest_fingerprint,
                "original_manifest": original.model_dump(mode="json"),
                "alignment_clock": "mcap_log_time",
                "sensor_header_clock": "UNKNOWN",
                "depth_source_seq": "per-topic message ordinal in MCAP log-time order",
                "original_sequence": "preserved separately in each hc.source_ref",
                "start_ns": session.alignment_data.start_ns,
                "end_ns": session.alignment_data.end_ns,
            },
        },
        source=FrozenObject(source.object_key, original.file_size, source.source_sha256),
        source_locator_sha256=digest(source.model_dump(mode="json")),
        alignment_sha256=staging.content_sha256,
        alignment_version=staging.alignment_version,
        alignment_profile=request.profile,
        frame_count=manifest.row_count,
        feature_mapping={topic: mapping[topic] for topic in sorted(topics)},
        cameras={mapping[topic]: contract for topic, contract in session.camera_contracts.items()},
        encoding=encoding_configuration(),
    )
    plan.validate_bundle(request, manifest, staging, ())
    return plan


def frame_for(step, topic, contract):
    value = step.modalities[topic]
    if value is None:
        if step.valid[topic]:
            raise ValueError("missing MCAP frame cannot claim valid pixels")
        return None
    frame = CameraFrame.from_projection(value)
    source = frame.source
    if (
        frame.kind != contract.kind
        or tuple(frame.pixels.shape) != contract.shape
        or source.get("topic") != topic
        or type(source.get("source_frame_index")) is not int
        or source["source_frame_index"] < 0
        or tuple(step.source_timestamps_ns[topic]) != (source.get("log_time_ns"),)
        or not step.valid[topic]
        or step.time_error_ns[topic] != abs(source["log_time_ns"] - step.timestamp_ns)
        or frame.profile is not None
        and contract.source_profiles.get(profile_hash(frame.profile)) != frame.profile
    ):
        raise ValueError("aligned MCAP frame differs from its frozen source contract")
    return frame


@contextmanager
def prepared_mcap_assets(plan, steps, attempt_id):
    type(plan).model_validate(plan.model_dump(mode="json"))
    if plan.encoding != encoding_configuration():
        raise ValueError("MCAP package encoding dependencies differ from its frozen plan")
    fps = plan.schema_snapshot.frequency_hz
    if fps != int(fps):
        raise ValueError("MCAP package requires an integer canonical frequency")
    fps = int(fps)
    with TemporaryDirectory(prefix="mcap-episode-media-") as temporary:
        root = Path(temporary)
        writers, pending = {}, []
        numeric = plan.metadata.get("numeric_projection")
        provenance = None
        if numeric:
            provenance = root / numeric["source_file"]
            provenance.parent.mkdir(parents=True)
        try:
            for feature, contract in plan.cameras.items():
                directory = root / digest(feature)
                directory.mkdir()
                writer = (
                    DepthEvidenceVideo(
                        directory / "video.mp4",
                        directory / "evidence",
                        fps,
                        contract.shape,
                        require_source_profile=True,
                    )
                    if contract.kind == "depth"
                    else RgbVideoWriter(directory / "video.mp4", fps, contract.shape)
                )
                writers[feature] = writer
                pending.append(writer)
            count = 0

            def traced_steps():
                if provenance is None:
                    yield from steps
                    return
                with provenance.open("wb") as trace:
                    for step in steps:
                        originals = {topic: step.modalities[topic] for topic in numeric["topics"]}
                        # Validate before writing any final package. Preserve header,
                        # velocity/effort and all other original decoded fields.
                        for value in originals.values():
                            normalize_vector(value)
                        trace.write(
                            canonical(
                                {
                                    "frame_index": step.step_index,
                                    "timestamp_ns": step.timestamp_ns,
                                    "messages": originals,
                                }
                            )
                            + b"\n"
                        )
                        yield step

            for step in traced_steps():
                if (
                    step.step_index != count
                    or count >= plan.frame_count
                    or step.timestamp_ns
                    != plan.metadata["source"]["start_ns"] + count * 1_000_000_000 // fps
                ):
                    raise ValueError("MCAP media steps differ from the frozen alignment grid")
                for topic, feature in plan.feature_mapping.items():
                    contract = plan.cameras[feature]
                    frame = frame_for(step, topic, contract)
                    if contract.kind == "depth":
                        source = None if frame is None else frame.source
                        writers[feature].write(
                            None if frame is None else frame.pixels,
                            None if frame is None else frame.profile["scale_m_per_unit"],
                            source_id=topic,
                            source_seq=None if source is None else source["source_frame_index"],
                            source_timestamp_ns=None if source is None else source["log_time_ns"],
                            target_timestamp_ns=step.timestamp_ns,
                            missing=frame is None,
                            invalid_values=() if frame is None else frame.profile["invalid_values"],
                            source_profile=None if frame is None else frame.profile,
                            max_time_error_ns=plan.alignment_profile.stream_tolerance_ns.get(
                                topic, plan.alignment_profile.default_tolerance_ns
                            ),
                        )
                    else:
                        writers[feature].write(
                            np.zeros(contract.shape, np.uint8) if frame is None else frame.pixels
                        )
                count += 1
            if count != plan.frame_count:
                raise ValueError("MCAP media did not supply all canonical frames")
            while pending:
                pending.pop().close()
        except BaseException:
            for writer in pending:
                with suppress(Exception):
                    writer.close()
            raise
        videos = {}
        for feature, contract in plan.cameras.items():
            directory = root / digest(feature)
            path, evidence = directory / "video.mp4", directory / "evidence"
            entries = (
                tuple(
                    FileEntry(
                        path=file.relative_to(evidence).as_posix(),
                        size=file.stat().st_size,
                        sha256=file_hash(file),
                        media_type="application/octet-stream",
                    )
                    for file in sorted(evidence.rglob("*"))
                    if file.is_file()
                )
                if contract.kind == "depth"
                else ()
            )
            info = (
                {**DEFAULT_PROFILE.metadata(), "video.fps": fps}
                if entries
                else {
                    "video.codec": "h264",
                    "video.pix_fmt": "yuv420p",
                    "video.channels": 3,
                    "video.fps": fps,
                    "has_audio": False,
                }
            )
            videos[feature] = PreparedVideo(
                path,
                file_hash(path),
                path.stat().st_size,
                {
                    "dtype": "video",
                    "shape": list(contract.shape) + ([1] if entries else []),
                    "names": ["height", "width", "channels"],
                    "info": info,
                },
                evidence if entries else None,
                entries,
            )
        auxiliary = (
            ()
            if provenance is None
            else (
                FileEntry(
                    path=numeric["source_file"],
                    size=provenance.stat().st_size,
                    sha256=file_hash(provenance),
                    media_type="application/x-ndjson",
                ),
            )
        )
        yield PreparedAssets(plan.input.identity, attempt_id, videos, root, auxiliary)


def mcap_steps(plan, steps):
    for step in steps:
        values = dict(step.modalities)
        for topic in plan.metadata.get("numeric_projection", {}).get("topics", ()):
            values[topic] = normalize_vector(values[topic])
        for topic, feature in plan.feature_mapping.items():
            frame = frame_for(step, topic, plan.cameras[feature])
            values[topic] = {
                "kind": "mcap_camera",
                "frame_index": step.step_index,
                "missing": frame is None,
                "valid": step.valid[topic],
                "source": None if frame is None else frame.source,
                "selection_status": "SELECTED"
                if frame is not None
                else (
                    "NO_FRAME_WITHIN_TOLERANCE"
                    if step.source_timestamps_ns[topic]
                    else "NO_SOURCE_FRAME"
                ),
                "candidate_source_timestamps_ns": list(step.source_timestamps_ns[topic]),
                "source_profile_sha256": None
                if frame is None or frame.profile is None
                else profile_hash(frame.profile),
            }
        yield step.model_copy(update={"modalities": values})
