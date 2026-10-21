"""Native LeRobot episodes adapted to the common durable ingest/media pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, cast

from hc_data_platform.alignment.models import AlignmentInputV1, AlignmentProfileV1, TimedSampleV1
from hc_data_platform.annotation.sampling import FrameSelectionSampler, SamplerFactory
from hc_data_platform.core.context import current_request_context
from hc_data_platform.episode_store.ingest_ports import NativeSampleCatalog, NativeSchemaRegistry
from hc_data_platform.ingest.manifest import ObjectStorageManifestParser, preflight_manifest
from hc_data_platform.ingest.models import (
    CollectionJob,
    CollectionJobStatus,
    Rollout,
    RolloutManifestV1,
    RolloutStatus,
)
from hc_data_platform.ingest.projection_metadata import (
    AlignmentMetadataBuilder,
    build_alignment_metadata,
)
from hc_data_platform.ingest.raw_sources import RawSource, RawSourceRepositoryPort
from hc_data_platform.ingest.schema_planning import DatasetSchemaBuilder, build_dataset_schema
from hc_data_platform.lance_catalog.ports import LanceCatalogPort
from hc_data_platform.object_storage.artifacts import ArtifactWorkspace
from hc_data_platform.object_storage.checksums import crc64_ecma
from hc_data_platform.object_storage.ports import JsonObjectStore
from hc_data_platform.quality.models import QualityProfileV1, QualityStreamObservationV1
from hc_data_platform.quality.ports import QualityProfileWriter
from hc_data_platform.verification.models import (
    RawVerificationReportV1,
    TopicInventoryV1,
    VerificationStatus,
)
from hc_data_platform.workflow.models import (
    AlignmentActivityInput,
    FrameSelectionManifestRefV1,
    IngestProjectionSourceV1,
    IngestRolloutWorkflowInput,
    ManifestActivityInput,
    QualityActivityInput,
    VerificationActivityInput,
)

from .adapter import EpisodeStream, LeRobotAdapter
from .cache import SourceCache
from .orchestration import LeRobotEpisodeTaskV1
from .pipeline_ports import (
    NativePlanningQuery,
    NativeProcessingState,
    NativeStateChange,
    SourceRolloutRegistration,
)
from .profiles import NativeProfile, quality_profile
from .reader import read_episode, required_episode_files
from .schema_diagnostics import schema_conflict_detail


class NativeImportBlocked(RuntimeError):
    code = "LEROBOT_PROCESSING_PLAN_REQUIRED"


class LeRobotPipeline:
    def __init__(
        self,
        storage: JsonObjectStore,
        catalog: LanceCatalogPort,
        staging: ArtifactWorkspace,
        cache_root: Path,
        *,
        raw_sources: RawSourceRepositoryPort,
        state: NativeProcessingState,
        registrations: SourceRolloutRegistration,
        planning: NativePlanningQuery,
        quality_profiles: QualityProfileWriter,
        cache_max_bytes: int = 5 * 1024**3,
        cache_ttl_hours: int = 24,
        quality_profile_factory: Callable[[NativeProfile, int], QualityProfileV1] = quality_profile,
        native_schemas: NativeSchemaRegistry,
        native_catalog: NativeSampleCatalog,
        schema_builder: DatasetSchemaBuilder = build_dataset_schema,
        sampler_factory: SamplerFactory = FrameSelectionSampler,
        metadata_builder: AlignmentMetadataBuilder = build_alignment_metadata,
    ) -> None:
        self.quality_profile_factory = quality_profile_factory
        self.sampler_factory = sampler_factory
        self.metadata_builder = metadata_builder
        self.state = state
        self.registrations = registrations
        self.planning = planning
        self.quality_profiles = quality_profiles
        self.storage = storage
        self.catalog = catalog
        self.native_schemas = native_schemas
        self.native_catalog = native_catalog
        self.schema_builder = schema_builder
        self.staging = staging
        self.cache_root = cache_root
        self.cache = SourceCache(
            cache_root, max_bytes=cache_max_bytes, ttl_seconds=cache_ttl_hours * 3600
        )
        self.raw = raw_sources

    def episode_ready(self, task: LeRobotEpisodeTaskV1) -> bool:
        self.source(task)
        return self.state.episode_ready(task)

    def update_state(self, update: Any) -> None:
        self.source(update.task)
        job = update.episode_job
        result = {} if job is None or job.result is None else job.result
        self.state.update_state(
            NativeStateChange(
                task=update.task,
                status=update.status,
                error_code=update.error_code,
                ready=job is not None and job.status.value == "SUCCEEDED",
                derived=result.get("derived") or {},
            )
        )

    def source(self, task: LeRobotEpisodeTaskV1) -> RawSource:
        ctx = current_request_context()
        if (ctx.organization_id, ctx.project_id, ctx.region_code) != (
            task.organization_id,
            task.project_id,
            task.region_code,
        ):
            raise ValueError("native processing requires the exact Worker scope")
        raw = self.raw.get_source(
            organization_id=task.organization_id,
            project_id=task.project_id,
            region_code=task.region_code,
            raw_source_id=task.source.raw_upload_id,
        )
        if (
            raw is None
            or raw.raw_status.value != "COMMITTED"
            or raw.source_format.value != "LEROBOT_V3"
            or (raw.dataset_id, raw.collection_task_id, raw.robot_id, raw.manifest_key)
            != (
                task.dataset_id,
                task.collection_task_id,
                task.robot_id,
                task.source.raw_manifest_key,
            )
        ):
            raise ValueError("native source lineage does not match committed Raw")
        return raw

    def _read_manifest(self, raw: RawSource) -> tuple[dict[str, Any], bytes]:
        from .committed import read_committed_manifest

        return read_committed_manifest(self.storage, raw)

    @staticmethod
    def _episode_cache_key(raw: RawSource, episode_index: int) -> str:
        return hashlib.sha256(f"{raw.content_hash}:episode:{episode_index}".encode()).hexdigest()

    def _localize(self, raw: RawSource, manifest: dict[str, Any], episode_index: int) -> Path:
        # Each episode owns a reconstructible cache entry. Concurrent episodes
        # must not evict each other's chunks or wait on one source-wide file lock.
        root = self.cache_root / self._episode_cache_key(raw, episode_index)
        root.mkdir(parents=True, exist_ok=True)
        files = {item["path"]: item for item in manifest["files"]}

        def copy(relative: str) -> Path:
            path = PurePosixPath(relative)
            if (
                path.is_absolute()
                or any(part in {"", ".", ".."} for part in path.parts)
                or "\\" in relative
            ):
                raise ValueError("unsafe native source path")
            descriptor = files.get(relative)
            if descriptor is None:
                raise ValueError("native source path is not in the committed manifest")
            target = root / relative
            marker = target.with_name(target.name + ".verified-sha256")
            if (
                target.is_file()
                and target.stat().st_size == descriptor["size"]
                and marker.is_file()
                and marker.read_text() == descriptor["sha256"]
            ):
                cached_digest = hashlib.sha256()
                with target.open("rb") as cached:
                    for chunk in iter(lambda: cached.read(1024 * 1024), b""):
                        cached_digest.update(chunk)
                if cached_digest.hexdigest() == descriptor["sha256"]:
                    return target
            with self.cache.reserve(int(descriptor["size"]) + 64):
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                fd, name = tempfile.mkstemp(dir=target.parent, suffix=".part")
                try:
                    with os.fdopen(fd, "wb") as output:
                        for chunk in self.storage.read_chunks(
                            descriptor.get("object_key") or f"{raw.storage_prefix}/{relative}"
                        ):
                            digest.update(chunk)
                            size += len(chunk)
                            output.write(chunk)
                    if size != descriptor["size"] or digest.hexdigest() != descriptor["sha256"]:
                        raise ValueError("native Raw source size or SHA-256 mismatch")
                    os.replace(name, target)
                    marker.write_text(descriptor["sha256"])
                finally:
                    Path(name).unlink(missing_ok=True)
            return target

        info = json.loads(copy("meta/info.json").read_text())
        matches = []
        for relative in sorted(files):
            if relative.startswith("meta/episodes/") and relative.endswith(".parquet"):
                import pyarrow.parquet as pq

                table = pq.read_table(
                    copy(relative), filters=[("episode_index", "=", episode_index)]
                )
                matches.extend(table.to_pylist())
        if len(matches) != 1:
            raise ValueError("native episode is not uniquely declared in metadata")
        metadata = matches[0]
        required = required_episode_files(info, metadata)
        for relative in (
            "meta/tasks.parquet",
            "capture-context.json",
            "export-complete.json",
            "source_mapping.jsonl",
        ):
            if relative in files:
                copy(relative)
        if "capture-context.json" in files:
            from .context import verify_source_context

            verify_source_context(root, files)
        # Both callers hold this episode's exclusive pin. Whole-entry eviction
        # cannot remove its chunks while this episode is being read.
        # Retain shared chunks and metadata; immutable object-store Raw stays intact.
        retained = required | {f"{relative}.verified-sha256" for relative in required}
        for folder in (root / "data", root / "videos"):
            for cached in folder.rglob("*"):
                if cached.is_file() and cached.relative_to(root).as_posix() not in retained:
                    cached.unlink()
        for relative in sorted(required):
            copy(relative)
        # These are optional for plain RGB sources, mandatory for declared depth
        # in the new package path. Retain the exact bytes and validate on commit.
        for relative in sorted(files):
            if relative in {"hc/calibration.json", "hc/modalities.json"} or (
                relative.startswith("hc/depth/")
            ):
                copy(relative)
        return root

    def prepare(self, task: LeRobotEpisodeTaskV1) -> IngestRolloutWorkflowInput:
        raw = self.source(task)
        manifest, body = self._read_manifest(raw)
        with self.cache.pin(self._episode_cache_key(raw, task.source.episode_index)):
            try:
                root = self._localize(raw, manifest, task.source.episode_index)
                return self._prepare(task, raw, body, root)
            except (ValueError, KeyError, IndexError) as exc:
                import re

                from hc_data_platform.core.errors import problem

                code = str(exc).split(":", 1)[0]
                if not re.fullmatch(r"(?:LEROBOT|OPENARM|CAPTURE)_[A-Z0-9_]+", code):
                    code = "CAPTURE_SOURCE_INVALID"
                raise problem(
                    status=422,
                    code=code,
                    title="LeRobot 源数据校验失败",
                    detail=str(exc),
                    retryable=False,
                ) from exc

    def _prepare(
        self, task: LeRobotEpisodeTaskV1, raw: RawSource, body: bytes, root: Path
    ) -> IngestRolloutWorkflowInput:
        from hc_data_platform.capture_schemes.models import OutputContract
        from hc_data_platform.capture_schemes.processing import require_snapshot, validate_lerobot
        from hc_data_platform.capture_schemes.schema import semantic_schema

        frozen = require_snapshot(self.planning.snapshot(task))
        from .context import read_context

        read_context(root, expected_snapshot=frozen)
        if task.storage_format != "lerobot_v3":
            from hc_data_platform.capture_schemes.common import reject

            reject(
                "CAPTURE_STORAGE_INCOMPATIBLE",
                "共享方案需要原生 Episode 包以保存 presence 与逐轴 validity",
            )
        from hc_data_platform.capture_schemes.schema import project_info

        from .capabilities import validate_processing_selection

        validate_processing_selection(
            project_info(json.loads((root / "meta/info.json").read_text()), frozen),
            json.loads(body).get("processing_capability_revision"),
        )
        evidence = validate_lerobot(
            root,
            task.source.episode_index,
            OutputContract.model_validate(frozen["output"]),
            frozen["effective_adapter"],
        )
        self.planning.freeze_contract(task, frozen)
        native_source = task.source.model_copy(
            update={
                "preserve_source_features": task.storage_format == "lerobot_v3",
                "capture_snapshot": frozen,
                "capture_validation": evidence,
            }
        )
        native_profile, layout, episode = read_episode(
            root,
            task.source.episode_index,
            preserve_features=native_source.preserve_source_features,
            capture_snapshot=frozen,
        )
        digest = hashlib.sha256(body).hexdigest()
        rollout_id = task.source.platform_episode_id or (
            f"lerobot-{raw.raw_source_id[:16]}-ep-{task.source.episode_index:06d}"
        )
        start = datetime(1970, 1, 1, tzinfo=timezone.utc)
        duration_ns = episode.relative_timestamps_ns[-1] + round(1e9 / episode.fps)
        nominal_ns = int(episode.frame_count * 1e9 / episode.fps)
        if abs(duration_ns - nominal_ns) <= 1_000:
            duration_ns = nominal_ns
        package = RolloutManifestV1(
            project_id=task.project_id,
            task_id=task.collection_task_id,
            collection_job_id=raw.raw_source_id,
            collection_session_id=raw.raw_source_id,
            recording_request_id=raw.raw_source_id,
            data_package_id=rollout_id,
            rollout_id=rollout_id,
            sequence_no=task.source.episode_index + 1,
            robot_id=task.robot_id,
            start_time=start,
            end_time=start + timedelta(microseconds=(duration_ns + 999) // 1000),
            cameras=[{"camera_id": c.camera_id, "topic": c.topic} for c in native_profile.cameras],
            topics=[{"name": topic, "required": True} for topic in native_profile.topics],
            expected_topics=list(native_profile.topics),
            actual_topics=list(native_profile.topics),
            files=[
                {
                    "path": "manifest.json",
                    "size": len(body),
                    "sha256": digest,
                    "crc64": crc64_ecma(body),
                    "role": "RAW_LEROBOT",
                }
            ],
            file_size=len(body),
            sha256=digest,
            crc64=crc64_ecma(body),
            compression="none",
            recorder_version="native-lerobot-v3/1",
            processing_mode="NATIVE_LEROBOT",
        )
        preflight = preflight_manifest(package)
        self.registrations.register_source_rollout(
            CollectionJob(
                project_id=task.project_id,
                region_code=task.region_code,
                task_id=task.collection_task_id,
                collection_job_id=raw.raw_source_id,
                robot_id=task.robot_id,
                status=CollectionJobStatus.COMPLETED,
            ),
            Rollout(
                project_id=task.project_id,
                region_code=task.region_code,
                collection_job_id=raw.raw_source_id,
                rollout_id=rollout_id,
                collection_session_id=raw.raw_source_id,
                recording_request_id=raw.raw_source_id,
                data_package_id=rollout_id,
                sequence_no=task.source.episode_index + 1,
                robot_id=task.robot_id,
                source_sha256=digest,
                status=RolloutStatus.RAW_COMMITTED,
            ),
        )
        profile = self.quality_profile_factory(native_profile, int(episode.fps))
        profile = profile.model_copy(
            update={
                "default_timing": profile.default_timing.model_copy(
                    update={
                        "target_frequency_hz": int(episode.fps),
                        "minimum_frequency_hz_risk": episode.fps * 0.95,
                        "minimum_frequency_hz_reject": episode.fps * 0.75,
                    }
                )
            }
        )
        # Different camera/auxiliary layouts must not overwrite one generic QC
        # profile. This policy identity is deliberately separate from data schema.
        profile = profile.model_copy(
            update={"profile_id": "capture-qc-" + profile.content_sha256()[:32]}
        )
        dataset = frozen["dataset_id"]
        if dataset != task.dataset_id:
            raise ValueError("CAPTURE_VERSION_MISMATCH: frozen Dataset differs from Raw")
        snapshot = semantic_schema(
            task.project_id,
            dataset,
            frozen["output"],
            {topic: "json" for topic in native_profile.topics},
        )
        schema_id = snapshot.schema_snapshot_id
        from hc_data_platform.capture_schemes.common import reject
        from hc_data_platform.episode_store.errors import PackageConflict

        try:
            if task.storage_format == "lerobot_v3":
                self.native_schemas.register_schema(snapshot)
            else:
                self.catalog.register_schema(snapshot)
        except (PackageConflict, ValueError) as exc:
            if not isinstance(exc, PackageConflict) and "already frozen differently" not in str(
                exc
            ):
                raise
            existing = (
                self.native_schemas.schema_for(task.project_id, dataset)
                if task.storage_format == "lerobot_v3"
                else self.catalog.schema_for(task.project_id, dataset)
            )
            reject("CAPTURE_DATASET_INCOMPATIBLE", schema_conflict_detail(snapshot, existing))
        self.quality_profiles.put_profile(task.project_id, profile)
        manifest_key = (
            f"derived/lerobot-imports/{task.organization_id}/{dataset}/"
            f"{raw.raw_source_id}/episodes/{task.source.episode_index:06d}-manifest.json"
        )
        self.storage.put_json(manifest_key, package.model_dump(mode="json"), if_none_match=False)
        source = IngestProjectionSourceV1(
            organization_id=task.organization_id,
            project_id=task.project_id,
            region_code=task.region_code,
            session_id=raw.raw_source_id,
            rollout_id=rollout_id,
            data_package_id=rollout_id,
            object_key=raw.manifest_key,
            manifest_key=manifest_key,
            manifest_fingerprint=preflight.manifest_fingerprint,
            source_sha256=digest,
            lerobot=native_source,
        )
        return IngestRolloutWorkflowInput(
            parallel_preparation=True,
            organization_id=task.organization_id,
            project_id=task.project_id,
            region_code=task.region_code,
            dataset_id=dataset,
            rollout_id=rollout_id,
            media_task_queue=os.getenv("HC_MEDIA_TEMPORAL_TASK_QUEUE", "hc-media-pipeline"),
            manifest=ManifestActivityInput(
                project_id=task.project_id,
                region_code=task.region_code,
                rollout_id=rollout_id,
                data_package_id=rollout_id,
                manifest_key=manifest_key,
                manifest_fingerprint=preflight.manifest_fingerprint,
                source_sha256=digest,
            ),
            verification=VerificationActivityInput(
                organization_id=task.organization_id,
                project_id=task.project_id,
                region_code=task.region_code,
                rollout_id=rollout_id,
                object_key=raw.manifest_key,
                source_sha256=digest,
                required_topics=frozenset(native_profile.topics),
            ),
            quality=QualityActivityInput(
                organization_id=task.organization_id,
                project_id=task.project_id,
                region_code=task.region_code,
                source=source,
                profile=profile,
            ),
            alignment=AlignmentActivityInput(
                storage_format=task.storage_format,
                organization_id=task.organization_id,
                project_id=task.project_id,
                region_code=task.region_code,
                dataset_id=dataset,
                schema_snapshot_id=schema_id,
                source=source,
                profile=AlignmentProfileV1(
                    profile_id=schema_id,
                    converter_version=(
                        "native-episode-package/2"
                        if task.storage_format == "lerobot_v3"
                        else "native-lerobot-v3/1"
                    ),
                    frequency_hz=int(episode.fps),
                    required_modalities=frozenset(native_profile.topics),
                    # Native rows already share a frame clock. Nearest preserves
                    # named joint vectors and avoids interpolating string labels.
                    stream_strategies={topic: "nearest" for topic in native_profile.topics},
                    default_tolerance_ns=34_000_000 if native_profile.legacy_g1 else 1_000,
                ),
            ),
        )

    @contextmanager
    def open_session(self, source: IngestProjectionSourceV1) -> Iterator[NativeEpisodeSession]:
        assert source.lerobot is not None
        raw = self.raw.get_source(
            organization_id=source.organization_id,
            project_id=source.project_id,
            region_code=source.region_code,
            raw_source_id=source.lerobot.raw_upload_id,
        )
        if raw is None or raw.manifest_key != source.object_key:
            raise ValueError("native source is absent or cross scope")
        manifest, body = self._read_manifest(raw)
        if hashlib.sha256(body).hexdigest() != source.source_sha256:
            raise ValueError("native manifest hash differs from workflow input")
        with self.cache.pin(self._episode_cache_key(raw, source.lerobot.episode_index)):
            root = self._localize(raw, manifest, source.lerobot.episode_index)
            with LeRobotAdapter().open_episode(
                root,
                source.lerobot,
                raw_manifest_sha256=source.source_sha256,
                original_files={
                    item["path"]: (
                        item.get("object_key") or f"{raw.storage_prefix}/{item['path']}",
                        item["size"],
                        item["sha256"],
                    )
                    for item in manifest["files"]
                },
            ) as stream:
                yield NativeEpisodeSession(
                    source,
                    stream,
                    self.staging,
                    len(body),
                    root=root,
                    raw=raw,
                    manifest=manifest,
                    sampler_factory=self.sampler_factory,
                )

    @contextmanager
    def package_assets(self, source, plan, attempt_id):
        from .package import prepared_native_assets

        with self.open_session(source) as session:
            yield prepared_native_assets(session, plan, attempt_id)

    def alignment_metadata(
        self,
        source: IngestProjectionSourceV1,
        *,
        dataset_id: str,
        dataset_version: int,
        storage_format: str = "lance",
        source_time_range: tuple[int, int] | None = None,
    ) -> AlignmentInputV1:
        if storage_format == "lerobot_v3" and source_time_range is None:
            raise ValueError("native viewer metadata requires the frozen source timeline")
        metadata = self.metadata_builder(
            source.rollout_id,
            source.source_sha256,
            ObjectStorageManifestParser(self.storage).parse(source.manifest_key),
        )
        if source_time_range is not None:
            start_ns, end_ns = source_time_range
            # The native plan pins nanoseconds. The transport manifest rounds its
            # end up to microseconds; using that rounded end can add a whole frame.
            # Only that known representation loss is allowed, never a genuine gap.
            if (
                storage_format != "lerobot_v3"
                or start_ns != metadata.start_ns
                or not 0 <= metadata.end_ns - end_ns < 1_000
                or end_ns <= start_ns
            ):
                raise ValueError("native source timeline differs from the committed manifest")
            metadata = metadata.model_copy(update={"start_ns": start_ns, "end_ns": end_ns})
        # Streaming alignment metadata deliberately carries no sample bodies. Read
        # one committed row to retain the real numeric/event types in the viewer.
        catalog = self.native_catalog if storage_format == "lerobot_v3" else self.catalog
        window = catalog.read_steps(
            dataset_id,
            source.rollout_id,
            0,
            1,
            project_id=source.project_id,
            version=dataset_version,
        )
        if not window.steps:
            raise ValueError("native viewer metadata requires a committed Step")
        step = window.steps[0]
        if source_time_range is not None and step.timestamp_ns != source_time_range[0]:
            raise ValueError("native source timeline differs from the committed first Step")
        return metadata.model_copy(
            update={
                "streams": {
                    topic: stream.model_copy(
                        update={
                            "samples": (
                                TimedSampleV1(
                                    timestamp_ns=step.timestamp_ns, value=step.modalities[topic]
                                ),
                            )
                        }
                    )
                    if topic in step.modalities
                    else stream
                    for topic, stream in metadata.streams.items()
                }
            }
        )


class NativeEpisodeSession:
    def __init__(
        self,
        source: IngestProjectionSourceV1,
        stream: EpisodeStream,
        staging: ArtifactWorkspace,
        object_size: int,
        *,
        root=None,
        raw=None,
        manifest=None,
        sampler_factory: SamplerFactory = FrameSelectionSampler,
    ) -> None:
        self.source, self.stream, self.staging = source, stream, staging
        self.sampler_factory = sampler_factory
        self.root, self.raw, self.raw_manifest = root, raw, manifest
        self.quality_data = stream.quality_input
        self.alignment_data = stream.alignment_input
        self.original_videos = stream.original_videos
        self._selection: FrameSelectionManifestRefV1 | None = None
        self.verification_report = RawVerificationReportV1.build(
            rollout_id=source.rollout_id,
            object_key=source.object_key,
            source_sha256=source.source_sha256,
            object_size=object_size,
            profile="native-lerobot-v3",
            library="pyarrow+ffmpeg",
            record_count=stream.episode.frame_count,
            message_count=stream.episode.frame_count * len(stream.profile.topics),
            schemas=0,
            channels=len(stream.profile.topics),
            chunks=0,
            schema_inventory=(),
            channel_inventory=(),
            topics=tuple(
                TopicInventoryV1(
                    topic=t,
                    message_encoding="lerobot-v3",
                    message_count=stream.episode.frame_count,
                    decode_checked=True,
                    decodable=True,
                )
                for t in stream.profile.topics
            ),
            findings=(),
            status=VerificationStatus.VERIFIED,
        )

    def quality_observations(self) -> Iterator[QualityStreamObservationV1]:
        return self.stream.quality_observations()

    def alignment_samples(self) -> Iterator[tuple[str, TimedSampleV1]]:
        sampler = self.sampler_factory()
        cameras = {
            camera.topic
            for camera in self.stream.profile.cameras
            if not (
                self.source.lerobot.preserve_source_features
                and self.stream.layout.info["features"][camera.feature_key]
                .get("info", {})
                .get("is_depth_map")
            )
        }
        for topic, sample in self.stream.alignment_samples():
            if topic in cameras:
                luma = self.stream.camera_luma[(topic, sample.timestamp_ns)]
                sampler.observe_camera(
                    topic, sample.timestamp_ns, luma_mean=luma, perceptual_hash=None, corrupt=False
                )
            else:
                sampler.observe_signal(topic, sample.timestamp_ns, sample.value)
            yield topic, sample
        selection = sampler.manifest(
            source_sha256=self.source.source_sha256, camera_topics=tuple(sorted(cameras))
        )
        project = hashlib.sha256(self.source.project_id.encode()).hexdigest()[:24]
        key = (
            f"derived/frame-selections/{project}/{self.source.source_sha256}/"
            f"{self.source.rollout_id}/adaptive-2fps-v2.json"
        )
        digest, size = self.staging.publish_json(key, selection)
        self._selection = FrameSelectionManifestRefV1(
            sampling_version="adaptive-2fps-v2",
            object_key=key,
            content_sha256=digest,
            size_bytes=size,
            source_frame_count=sampler.source_frame_count,
            selected_group_count=len(cast(list[object], selection["groups"])),
            camera_set=tuple(sorted(cameras)),
        )

    def frame_selection(self) -> FrameSelectionManifestRefV1:
        if self._selection is None:
            raise RuntimeError("native alignment stream was not completely consumed")
        return self._selection
