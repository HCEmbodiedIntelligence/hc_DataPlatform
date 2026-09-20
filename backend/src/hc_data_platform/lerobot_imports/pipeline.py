"""Native G1 episodes adapted to the common durable ingest/media pipeline."""

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
from hc_data_platform.core.context import current_request_context
from hc_data_platform.ingest.manifest import ObjectStorageManifestParser, preflight_manifest
from hc_data_platform.ingest.models import (
    CollectionJob,
    CollectionJobStatus,
    Rollout,
    RolloutManifestV1,
    RolloutStatus,
)
from hc_data_platform.ingest.ports import ObjectStoragePort, crc64_ecma
from hc_data_platform.ingest.postgres import PostgresIngestPersistence
from hc_data_platform.ingest.raw_sources import PostgresRawSourceRepository, RawSource
from hc_data_platform.lance_catalog.ports import LanceCatalogPort
from hc_data_platform.quality.models import QualityProfileV1, QualityStreamObservationV1
from hc_data_platform.quality.postgres import PostgresQualityRepository
from hc_data_platform.tools import hf_unitree_g1_to_mcap as reader
from hc_data_platform.verification.models import (
    RawVerificationReportV1,
    TopicInventoryV1,
    VerificationStatus,
)
from hc_data_platform.workflow.ingest_plan import (
    PostgresIngestWorkflowInputResolver,
    _FrameSelectionSampler,
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
from hc_data_platform.workflow.projection_store import ProjectionArtifactStorePort

from .adapter import EpisodeStream, LeRobotAdapter
from .cache import SourceCache
from .orchestration import LeRobotEpisodeTaskV1


class NativeImportBlocked(RuntimeError):
    code = "LEROBOT_PROCESSING_PLAN_REQUIRED"


class LeRobotPipeline:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        storage: ObjectStoragePort,
        catalog: LanceCatalogPort,
        staging: ProjectionArtifactStorePort,
        cache_root: Path,
        *,
        cache_max_bytes: int = 5 * 1024**3,
        cache_ttl_hours: int = 24,
    ) -> None:
        self.connections = connection_factory
        self.storage = storage
        self.catalog = catalog
        self.staging = staging
        self.cache_root = cache_root
        self.cache = SourceCache(
            cache_root, max_bytes=cache_max_bytes, ttl_seconds=cache_ttl_hours * 3600
        )
        self.raw = PostgresRawSourceRepository(connection_factory)

    def episode_ready(self, task: LeRobotEpisodeTaskV1) -> bool:
        self.source(task)
        with self.connections() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT status FROM ingest.raw_source_episodes
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND raw_source_id=%s AND source_episode_index=%s""",
                (
                    task.organization_id,
                    task.project_id,
                    task.region_code,
                    task.source.raw_upload_id,
                    task.source.episode_index,
                ),
            )
            row = cursor.fetchone()
            return row is not None and row[0] == "READY"

    def update_state(self, update: Any) -> None:
        task = update.task
        self.source(task)
        import re

        error = (
            None
            if update.error_code is None
            else re.sub(r"[^A-Z0-9_]", "_", update.error_code.upper())[:128]
        )
        scope = (task.organization_id, task.project_id, task.region_code, task.source.raw_upload_id)
        with self.connections() as connection, connection.cursor() as cursor:
            if update.status.startswith("EPISODE_"):
                job = update.episode_job
                ready = job is not None and job.status.value == "SUCCEEDED"
                result = {} if job is None or job.result is None else job.result
                derived = result.get("derived") or {}
                cursor.execute(
                    """UPDATE ingest.raw_source_episodes
                    SET status=%s, frame_count=%s, dataset_version=%s, lance_version=%s,
                    updated_at=clock_timestamp()
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s
                      AND raw_source_id=%s AND source_episode_index=%s""",
                    (
                        "READY" if ready else "FAILED",
                        derived.get("step_count"),
                        derived.get("dataset_version"),
                        derived.get("lance_version"),
                        *scope,
                        task.source.episode_index,
                    ),
                )
                return
            cursor.execute(
                """UPDATE ingest.raw_ingest_jobs
                SET status=%s, last_error_code=%s, updated_at=clock_timestamp()
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                AND raw_source_id=%s""",
                (update.status, error, *scope),
            )
            source_status = {
                "RUNNING": "PROCESSING",
                "SUCCEEDED": "READY",
                "PARTIALLY_FAILED": "PARTIALLY_FAILED",
                "FAILED": "FAILED",
                "CANCELLED": "FAILED",
            }[update.status]
            cursor.execute(
                """UPDATE ingest.raw_sources SET processing_status=%s, updated_at=clock_timestamp()
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                AND raw_source_id=%s""",
                (source_status, *scope),
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
        body = b"".join(self.storage.read_chunks(raw.manifest_key))
        manifest = json.loads(body)
        if (
            manifest.get("content_hash") != raw.content_hash
            or manifest.get("source_prefix") != raw.storage_prefix
        ):
            raise ValueError("native Raw manifest changed after commit")
        return manifest, body

    def _localize(self, raw: RawSource, manifest: dict[str, Any], episode_index: int) -> Path:
        # A content-addressed local cache is reconstructible from immutable Raw objects.
        root = self.cache_root / raw.content_hash
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
                return target
            with self.cache.reserve(int(descriptor["size"]) + 64):
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                fd, name = tempfile.mkstemp(dir=target.parent, suffix=".part")
                try:
                    with os.fdopen(fd, "wb") as output:
                        for chunk in self.storage.read_chunks(f"{raw.storage_prefix}/{relative}"):
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
        copy(
            info["data_path"].format(
                chunk_index=int(metadata["data/chunk_index"]),
                file_index=int(metadata["data/file_index"]),
            )
        )
        for camera in reader.CAMERAS:
            prefix = f"videos/{camera.feature_key}"
            copy(
                info["video_path"].format(
                    video_key=camera.feature_key,
                    chunk_index=int(metadata[f"{prefix}/chunk_index"]),
                    file_index=int(metadata[f"{prefix}/file_index"]),
                )
            )
        return root

    def prepare(self, task: LeRobotEpisodeTaskV1) -> IngestRolloutWorkflowInput:
        raw = self.source(task)
        manifest, body = self._read_manifest(raw)
        with self.cache.pin(raw.content_hash):
            root = self._localize(raw, manifest, task.source.episode_index)
            return self._prepare(task, raw, body, root)

    def _prepare(
        self, task: LeRobotEpisodeTaskV1, raw: RawSource, body: bytes, root: Path
    ) -> IngestRolloutWorkflowInput:
        layout = reader.acquire_source(
            repository="platform-raw/lerobot",
            revision=raw.raw_source_id,
            episode_index=task.source.episode_index,
            cache_root=root,
            source_root=root,
        )
        episode = reader.load_episode_data(
            layout.data_file, task.source.episode_index, float(layout.info["fps"])
        )
        digest = hashlib.sha256(body).hexdigest()
        rollout_id = f"lerobot-{raw.raw_source_id[:16]}-ep-{task.source.episode_index:06d}"
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
            cameras=[{"camera_id": c.camera_id, "topic": c.topic} for c in reader.CAMERAS],
            topics=[{"name": topic, "required": True} for topic in reader.ACTUAL_TOPICS],
            expected_topics=list(reader.ACTUAL_TOPICS),
            actual_topics=list(reader.ACTUAL_TOPICS),
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
        PostgresIngestPersistence(self.connections).register_source_rollout(
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
        profile = QualityProfileV1(
            profile_id="native-g1-v3-30hz-v1",
            profile_version=2,
            required_topics=frozenset(reader.ACTUAL_TOPICS),
            default_timing={"target_frequency_hz": 30},
            joint_topic=reader.JOINT_TOPIC,
            action={"topic": reader.ACTION_TOPIC},
        )
        with self.connections() as connection, connection.cursor() as cursor:
            dataset = PostgresIngestWorkflowInputResolver._task_dataset(
                cursor,
                organization_id=task.organization_id,
                project_id=task.project_id,
                collection_task_id=task.collection_task_id,
            )
            if dataset != task.dataset_id:
                raise ValueError("native collection task Dataset changed")
            targets = PostgresIngestWorkflowInputResolver._dataset_schema_targets(
                cursor,
                project_id=task.project_id,
                storage_region_code=task.region_code,
                dataset_id=dataset,
            )
        if len(set(targets)) != 1:
            raise NativeImportBlocked(
                "请先在标签体系中发布并绑定当前数据集的标注 Schema，再重试处理。"
            )
        schema_id = targets[0]
        snapshot = PostgresIngestWorkflowInputResolver._dataset_schema_candidate(
            project_id=task.project_id,
            dataset_id=dataset,
            schema_snapshot_id=schema_id,
            preflight=preflight,
            profile=profile,
        )
        self.catalog.register_schema(snapshot)
        PostgresQualityRepository(self.connections).put_profile(task.project_id, profile)
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
            lerobot=task.source,
        )
        return IngestRolloutWorkflowInput(
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
                required_topics=frozenset(reader.ACTUAL_TOPICS),
            ),
            quality=QualityActivityInput(
                organization_id=task.organization_id,
                project_id=task.project_id,
                region_code=task.region_code,
                source=source,
                profile=profile,
            ),
            alignment=AlignmentActivityInput(
                organization_id=task.organization_id,
                project_id=task.project_id,
                region_code=task.region_code,
                dataset_id=dataset,
                schema_snapshot_id=schema_id,
                source=source,
                profile=AlignmentProfileV1(
                    profile_id=schema_id,
                    converter_version="native-lerobot-v3/1",
                    frequency_hz=30,
                    required_modalities=frozenset(reader.ACTUAL_TOPICS),
                    # Native rows already share a frame clock. Nearest preserves
                    # named joint vectors and avoids interpolating string labels.
                    stream_strategies={topic: "nearest" for topic in reader.ACTUAL_TOPICS},
                    default_tolerance_ns=34_000_000,
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
        with self.cache.pin(raw.content_hash):
            root = self._localize(raw, manifest, source.lerobot.episode_index)
            with LeRobotAdapter().open_episode(
                root,
                source.lerobot,
                raw_manifest_sha256=source.source_sha256,
                original_files={
                    item["path"]: (
                        f"{raw.storage_prefix}/{item['path']}",
                        item["size"],
                        item["sha256"],
                    )
                    for item in manifest["files"]
                },
            ) as stream:
                yield NativeEpisodeSession(source, stream, self.staging, len(body))

    def alignment_metadata(
        self, source: IngestProjectionSourceV1, *, dataset_id: str, dataset_version: int
    ) -> AlignmentInputV1:
        from hc_data_platform.workflow.ingest_plan import _alignment_metadata

        metadata = _alignment_metadata(
            source, ObjectStorageManifestParser(self.storage).parse(source.manifest_key)
        )
        # Streaming alignment metadata deliberately carries no sample bodies. Read
        # one committed row to retain the real numeric/event types in the viewer.
        window = self.catalog.read_steps(
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
        staging: ProjectionArtifactStorePort,
        object_size: int,
    ) -> None:
        self.source, self.stream, self.staging = source, stream, staging
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
            message_count=stream.episode.frame_count * len(reader.ACTUAL_TOPICS),
            schemas=0,
            channels=len(reader.ACTUAL_TOPICS),
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
                for t in reader.ACTUAL_TOPICS
            ),
            findings=(),
            status=VerificationStatus.VERIFIED,
        )

    def quality_observations(self) -> Iterator[QualityStreamObservationV1]:
        return self.stream.quality_observations()

    def alignment_samples(self) -> Iterator[tuple[str, TimedSampleV1]]:
        sampler = _FrameSelectionSampler()
        cameras = {c.topic for c in reader.CAMERAS}
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
            f"{self.source.rollout_id}/adaptive-2fps-v1.json"
        )
        digest, size = self.staging.publish_json(key, selection)
        self._selection = FrameSelectionManifestRefV1(
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
