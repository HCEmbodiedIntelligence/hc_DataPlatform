"""Resolve an ingest workflow from immutable upload and processing-plan facts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import tempfile
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import closing, suppress
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import IO, Any, cast

from PIL import Image, ImageStat, UnidentifiedImageError
from pydantic import TypeAdapter, ValidationError

from hc_data_platform.alignment.models import (
    AlignmentInputV1,
    AlignmentProfileV1,
    ModalityKind,
    ModalityStreamV1,
    TimedSampleV1,
)
from hc_data_platform.core.context import current_request_context
from hc_data_platform.dataset_registry.models import DatasetId
from hc_data_platform.ingest.models import ManifestPreflightResultV1
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot
from hc_data_platform.lance_catalog.ports import LanceCatalogPort
from hc_data_platform.quality.models import (
    ImageObservation,
    QualityInputV1,
    QualityProfileV1,
    QualityStreamObservationV1,
)
from hc_data_platform.verification.ports import (
    DecoderProbe,
    ReadableObjectStorage,
)

from .ingest_dispatch import IngestWorkflowPlanBlocked
from .ingest_metrics import (
    AUTO_ANNOTATION_SAMPLE_RATIO,
    PROJECTION_SCAN,
    PROJECTION_SOURCE_PASSES,
)
from .models import (
    AlignmentActivityInput,
    FrameSelectionManifestRefV1,
    IngestProjectionSourceV1,
    IngestRolloutWorkflowInput,
    ManifestActivityInput,
    ProjectionMaterializationV1,
    QualityActivityInput,
    VerificationActivityInput,
)
from .projection_store import ProjectionArtifactStorePort

_DATASET_ID_ADAPTER = TypeAdapter(DatasetId)


class PostgresIngestWorkflowInputResolver:
    """Build a bounded workflow input from exact, tenant-scoped persisted facts.

    The selected quality profile must be the only immutable profile whose required
    topic set exactly matches the committed Manifest.  Likewise, the published Tag
    Schema must expose exactly one compatible dataset target in the event scope.
    Ambiguous or incomplete plans fail closed instead of selecting a newest row.
    """

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        storage: ReadableObjectStorage,
        catalog: LanceCatalogPort,
        *,
        decoder: DecoderProbe | None = None,
        maximum_messages: int = 250_000,
        projection_store: ProjectionArtifactStorePort | None = None,
        projection_staging_root: Path = Path("/tmp/hc-data/projection-staging"),
        projection_ttl: timedelta = timedelta(hours=24),
    ) -> None:
        if maximum_messages < 1:
            raise ValueError("maximum_messages must be positive")
        self._connection_factory = connection_factory
        self._storage = storage
        self._catalog = catalog
        self._decoder = decoder
        self._maximum_messages = maximum_messages
        self._projection_store = projection_store
        self._projection_staging_root = projection_staging_root.resolve()
        self._projection_ttl = projection_ttl

    def resolve(
        self,
        *,
        project_id: str,
        region_code: str,
        session_id: str,
        rollout_id: str,
        data_package_id: str,
    ) -> IngestRolloutWorkflowInput:
        organization_id = current_request_context().organization_id
        if organization_id is None:
            raise _blocked("the ingest workflow requires an exact organization scope")
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT object.object_key, object.manifest_key, object.source_sha256,
                           discovery.manifest_fingerprint, discovery.preflight_json
                    FROM ingest.upload_sessions session
                    JOIN ingest.rollout_objects object
                      ON object.project_id = session.project_id
                     AND object.region_code = session.region_code
                     AND object.rollout_id = session.rollout_id
                    JOIN ingest.manifest_discoveries discovery
                      ON discovery.session_id = session.session_id
                     AND discovery.project_id = session.project_id
                     AND discovery.region_code = session.region_code
                    WHERE session.session_id = %s AND session.project_id = %s
                      AND session.region_code = %s AND session.rollout_id = %s
                      AND session.data_package_id = %s
                      AND session.status = 'RAW_COMMITTED'
                    """,
                    (session_id, project_id, region_code, rollout_id, data_package_id),
                )
                raw = cursor.fetchone()
                if raw is None:
                    raise _blocked("the committed upload facts are missing or cross scope")
                object_key = str(raw[0])
                manifest_key = str(raw[1])
                source_sha256 = str(raw[2])
                manifest_fingerprint = str(raw[3])
                preflight = ManifestPreflightResultV1.model_validate(raw[4])
                self._validate_manifest_lineage(
                    preflight,
                    project_id=project_id,
                    rollout_id=rollout_id,
                    data_package_id=data_package_id,
                    source_sha256=source_sha256,
                    manifest_fingerprint=manifest_fingerprint,
                )
                profile = self._quality_profile(cursor, project_id, preflight)
                dataset_id = self._task_dataset(
                    cursor,
                    organization_id=organization_id,
                    project_id=project_id,
                    collection_task_id=preflight.manifest.task_id,
                )
                schema_snapshot_ids = self._dataset_schema_targets(
                    cursor,
                    project_id=project_id,
                    storage_region_code=region_code,
                    dataset_id=dataset_id,
                )
        finally:
            connection.close()

        dataset_id, schema_snapshot_id, snapshot = self._select_dataset_schema(
            project_id=project_id,
            dataset_id=dataset_id,
            schema_snapshot_ids=schema_snapshot_ids,
            preflight=preflight,
            profile=profile,
        )
        source = IngestProjectionSourceV1(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            session_id=session_id,
            rollout_id=rollout_id,
            data_package_id=data_package_id,
            object_key=object_key,
            manifest_key=manifest_key,
            manifest_fingerprint=manifest_fingerprint,
            source_sha256=source_sha256,
        )
        frequency_hz = int(snapshot.frequency_hz)
        alignment_profile = AlignmentProfileV1(
            profile_id=f"dataset-schema:{snapshot.schema_snapshot_id}",
            converter_version="mcap-source-reference/1",
            frequency_hz=frequency_hz,
            required_modalities=frozenset(snapshot.fields),
            default_tolerance_ns=max(1, 1_000_000_000 // frequency_hz),
        )
        return IngestRolloutWorkflowInput(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            dataset_id=dataset_id,
            rollout_id=rollout_id,
            media_task_queue=os.getenv("HC_MEDIA_TEMPORAL_TASK_QUEUE", "hc-media-pipeline"),
            manifest=ManifestActivityInput(
                project_id=project_id,
                region_code=region_code,
                rollout_id=rollout_id,
                data_package_id=data_package_id,
                manifest_key=manifest_key,
                manifest_fingerprint=manifest_fingerprint,
                source_sha256=source_sha256,
            ),
            verification=VerificationActivityInput(
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                rollout_id=rollout_id,
                object_key=object_key,
                source_sha256=source_sha256,
                required_topics=frozenset(preflight.manifest.expected_topics),
                known_optional_topics=frozenset(
                    set(preflight.manifest.actual_topics) - set(preflight.manifest.expected_topics)
                ),
            ),
            quality=QualityActivityInput(
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                source=source,
                profile=profile,
            ),
            alignment=AlignmentActivityInput(
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                dataset_id=dataset_id,
                schema_snapshot_id=schema_snapshot_id,
                source=source,
                profile=alignment_profile,
            ),
        )

    def project_quality(self, source: IngestProjectionSourceV1) -> QualityInputV1:
        del source
        raise _blocked(
            "bulk quality projection is disabled; materialize once and use online aggregation"
        )

    def open_local_session(self, source: IngestProjectionSourceV1) -> _LocalProjectionSession:
        """Localize Raw once; no projection Arrow object is created or uploaded."""

        preflight = self._reload_projection_source(source)
        return _LocalProjectionSession(
            source=source,
            preflight=preflight,
            storage=self._storage,
            decoder=self._decoder,
            maximum_messages=self._maximum_messages,
            staging_root=self._projection_staging_root,
            artifact_store=self._projection_store,
        )

    def project_alignment_metadata(self, source: IngestProjectionSourceV1) -> AlignmentInputV1:
        return _alignment_metadata(source, self._reload_projection_source(source))

    def project_quality_stream(
        self, source: IngestProjectionSourceV1
    ) -> tuple[QualityInputV1, Iterable[QualityStreamObservationV1]]:
        preflight = self._reload_projection_source(source)
        if source.materialization is None:
            raise _blocked("streaming quality requires a materialized projection")
        metadata = QualityInputV1(
            rollout_id=source.rollout_id,
            source_sha256=source.source_sha256,
            start_ns=_datetime_ns(preflight.manifest.start_time),
            end_ns=_datetime_ns(preflight.manifest.end_time),
            topic_timestamps_ns={},
        )
        return metadata, self._iter_materialized_quality(source)

    def _iter_materialized_quality(
        self, source: IngestProjectionSourceV1
    ) -> Iterator[QualityStreamObservationV1]:
        materialization = source.materialization
        if materialization is None or self._projection_store is None:
            raise _blocked("the quality projection materialization is unavailable")
        with self._projection_store.local_file(
            materialization.object_key,
            expected_sha256=materialization.content_sha256,
            expected_size=materialization.size_bytes,
        ) as path:
            for row in _projection_rows(
                path,
                columns=(
                    "topic",
                    "timestamp_ns",
                    "is_camera",
                    "luma_mean",
                    "fingerprint",
                    "corrupt",
                ),
            ):
                yield QualityStreamObservationV1(
                    topic=str(row["topic"]),
                    timestamp_ns=int(row["timestamp_ns"]),
                    is_camera=bool(row["is_camera"]),
                    luma_mean=(None if row["luma_mean"] is None else float(row["luma_mean"])),
                    fingerprint=(None if row["fingerprint"] is None else str(row["fingerprint"])),
                    corrupt=bool(row["corrupt"]),
                )

    def project_alignment(self, source: IngestProjectionSourceV1) -> AlignmentInputV1:
        del source
        raise _blocked(
            "bulk alignment projection is disabled; materialize once and stream to Arrow"
        )

    def project_alignment_stream(
        self, source: IngestProjectionSourceV1
    ) -> tuple[AlignmentInputV1, Iterable[tuple[str, TimedSampleV1]]]:
        """Return bounded stream metadata plus a lazy Arrow sample iterator."""

        preflight = self._reload_projection_source(source)
        if source.materialization is None:
            raise _blocked("streaming alignment requires a materialized projection")
        expected_topics = tuple(sorted(preflight.manifest.actual_topics))
        metadata = _alignment_metadata(source, preflight)
        return metadata, self._iter_materialized_alignment(source, expected_topics)

    def _iter_materialized_alignment(
        self,
        source: IngestProjectionSourceV1,
        expected_topics: Sequence[str],
    ) -> Iterator[tuple[str, TimedSampleV1]]:
        materialization = source.materialization
        if materialization is None or self._projection_store is None:
            raise _blocked("the alignment projection materialization is unavailable")
        seen: set[str] = set()
        with self._projection_store.local_file(
            materialization.object_key,
            expected_sha256=materialization.content_sha256,
            expected_size=materialization.size_bytes,
        ) as path:
            for row in _projection_sample_rows(path):
                if not row[3]:
                    continue
                topic, timestamp_ns, is_camera, _accepted, value_json, image = row
                seen.add(topic)
                if is_camera:
                    value: object = b"" if image is None else image
                else:
                    if value_json is None:
                        raise _blocked("a materialized non-camera value is missing")
                    value = json.loads(value_json)
                yield topic, TimedSampleV1(timestamp_ns=timestamp_ns, value=value)
        if seen != set(expected_topics):
            raise _blocked("the projection artifact topic inventory is incomplete")

    def materialize(self, source: IngestProjectionSourceV1) -> IngestProjectionSourceV1:
        """Scan the committed MCAP exactly once into a short-lived Arrow artifact."""

        if source.materialization is not None:
            return source
        if self._projection_store is None:
            raise _blocked("the ingest projection artifact store is not configured")
        preflight = self._reload_projection_source(source)
        materialization = self._materialize_mcap(source, preflight)
        return source.model_copy(update={"materialization": materialization})

    def cleanup(self, source: IngestProjectionSourceV1) -> None:
        """Delete only the short-lived Arrow object; preserve frame selection."""

        if source.materialization is None:
            return
        if self._projection_store is None:
            raise _blocked("the ingest projection artifact store is not configured")
        self._projection_store.delete(source.materialization.object_key)

    def _reload_projection_source(
        self,
        source: IngestProjectionSourceV1,
    ) -> ManifestPreflightResultV1:
        context = current_request_context()
        if (
            context.organization_id != source.organization_id
            or context.project_id != source.project_id
            or context.region_code != source.region_code
        ):
            raise _blocked("the projection source does not match the Worker scope")
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT object.object_key, object.manifest_key, object.source_sha256,
                           discovery.manifest_fingerprint, discovery.preflight_json
                    FROM ingest.upload_sessions session
                    JOIN ingest.rollout_objects object
                      ON object.project_id = session.project_id
                     AND object.region_code = session.region_code
                     AND object.rollout_id = session.rollout_id
                    JOIN ingest.manifest_discoveries discovery
                      ON discovery.session_id = session.session_id
                     AND discovery.project_id = session.project_id
                     AND discovery.region_code = session.region_code
                    WHERE session.session_id = %s AND session.project_id = %s
                      AND session.region_code = %s AND session.rollout_id = %s
                      AND session.data_package_id = %s
                      AND session.status = 'RAW_COMMITTED'
                    """,
                    (
                        source.session_id,
                        source.project_id,
                        source.region_code,
                        source.rollout_id,
                        source.data_package_id,
                    ),
                )
                raw = cursor.fetchone()
                if raw is None:
                    raise _blocked("the committed projection source is missing or cross scope")
                preflight = ManifestPreflightResultV1.model_validate(raw[4])
                persisted = (
                    str(raw[0]),
                    str(raw[1]),
                    str(raw[2]),
                    str(raw[3]),
                )
                expected = (
                    source.object_key,
                    source.manifest_key,
                    source.source_sha256,
                    source.manifest_fingerprint,
                )
                if persisted != expected:
                    raise _blocked("the committed projection source changed after dispatch")
                self._validate_manifest_lineage(
                    preflight,
                    project_id=source.project_id,
                    rollout_id=source.rollout_id,
                    data_package_id=source.data_package_id,
                    source_sha256=source.source_sha256,
                    manifest_fingerprint=source.manifest_fingerprint,
                )
                return preflight
        finally:
            connection.close()

    @staticmethod
    def _validate_manifest_lineage(
        preflight: ManifestPreflightResultV1,
        *,
        project_id: str,
        rollout_id: str,
        data_package_id: str,
        source_sha256: str,
        manifest_fingerprint: str,
    ) -> None:
        manifest = preflight.manifest
        if (
            manifest.project_id != project_id
            or manifest.rollout_id != rollout_id
            or manifest.data_package_id != data_package_id
            or manifest.sha256 != source_sha256
            or preflight.manifest_fingerprint != manifest_fingerprint
        ):
            raise _blocked("the persisted Manifest lineage does not match the outbox event")

    @staticmethod
    def _quality_profile(
        cursor: Any,
        project_id: str,
        preflight: ManifestPreflightResultV1,
    ) -> QualityProfileV1:
        cursor.execute(
            """
            SELECT profile_json FROM quality_profiles
            WHERE project_id = %s
            ORDER BY profile_id, profile_version
            """,
            (project_id,),
        )
        expected = frozenset(preflight.manifest.expected_topics)
        latest: dict[str, QualityProfileV1] = {}
        for row in cursor.fetchall():
            profile = QualityProfileV1.model_validate(row[0])
            previous = latest.get(profile.profile_id)
            if previous is None or profile.profile_version > previous.profile_version:
                latest[profile.profile_id] = profile
        candidates = [profile for profile in latest.values() if profile.required_topics == expected]
        if len(candidates) != 1:
            raise _blocked(
                "the project must persist exactly one quality profile for the Manifest topic set"
            )
        return candidates[0]

    @staticmethod
    def _task_dataset(
        cursor: Any,
        *,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
    ) -> str:
        cursor.execute(
            """
            SELECT dataset_id
            FROM collection_tasks.collection_tasks
            WHERE organization_id = %s AND project_id = %s
              AND collection_task_id = %s
            """,
            (organization_id, project_id, collection_task_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise _blocked("the Manifest task must reference one collection task in the project")
        dataset_id = str(raw[0])
        try:
            _DATASET_ID_ADAPTER.validate_python(dataset_id)
        except ValidationError as exc:
            raise _blocked("the collection task has an invalid Dataset identity") from exc
        return dataset_id

    @staticmethod
    def _dataset_schema_targets(
        cursor: Any,
        *,
        project_id: str,
        storage_region_code: str,
        dataset_id: str,
    ) -> tuple[str, ...]:
        cursor.execute(
            """
            SELECT dataset_schema_snapshot_id
            FROM annotation.tag_schema_bindings
            WHERE project_id = %s AND region_code = %s AND task_kind = 'TAGGING'
              AND dataset_id = %s
            ORDER BY dataset_schema_snapshot_id
            """,
            (project_id, storage_region_code, dataset_id),
        )
        return tuple(str(row[0]) for row in cursor.fetchall())

    def _select_dataset_schema(
        self,
        *,
        project_id: str,
        dataset_id: str,
        schema_snapshot_ids: Sequence[str],
        preflight: ManifestPreflightResultV1,
        profile: QualityProfileV1,
    ) -> tuple[str, str, DatasetSchemaSnapshot]:
        compatible: list[tuple[str, DatasetSchemaSnapshot]] = []
        for schema_snapshot_id in schema_snapshot_ids:
            candidate = self._dataset_schema_candidate(
                project_id=project_id,
                dataset_id=dataset_id,
                schema_snapshot_id=schema_snapshot_id,
                preflight=preflight,
                profile=profile,
            )
            existing = self._catalog.schema_for(project_id, dataset_id)
            if existing is None or existing == candidate:
                compatible.append((schema_snapshot_id, candidate))
        if len(compatible) != 1:
            raise _blocked(
                "the task Dataset must have exactly one compatible Tag Schema binding in "
                "the selected storage region"
            )
        schema_snapshot_id, candidate = compatible[0]
        try:
            self._catalog.register_schema(candidate)
        except Exception as exc:
            raise _blocked("the compatible dataset schema could not be registered") from exc
        return dataset_id, schema_snapshot_id, candidate

    @staticmethod
    def _dataset_schema_candidate(
        *,
        project_id: str,
        dataset_id: str,
        schema_snapshot_id: str,
        preflight: ManifestPreflightResultV1,
        profile: QualityProfileV1,
    ) -> DatasetSchemaSnapshot:
        frequency_hz = profile.default_timing.target_frequency_hz
        if not 1 <= frequency_hz <= 1000:
            raise _blocked("the quality profile frequency cannot be used for alignment")
        # Camera rows carry compact aligned-media frame references. Raw JPEG bytes
        # remain only in short-lived alignment staging and are never committed to Lance.
        fields = {topic: "json" for topic in preflight.manifest.actual_topics}
        return DatasetSchemaSnapshot.create(
            project_id=project_id,
            dataset_id=dataset_id,
            schema_snapshot_id=schema_snapshot_id,
            frequency_hz=float(frequency_hz),
            fields=fields,
        )

    def _materialize_mcap(
        self,
        source: IngestProjectionSourceV1,
        preflight: ManifestPreflightResultV1,
    ) -> ProjectionMaterializationV1:
        try:
            from mcap.reader import make_reader
        except ImportError as exc:
            raise _blocked("the production MCAP data dependency is not installed") from exc

        assert self._projection_store is not None
        self._projection_staging_root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f"projection-{source.source_sha256[:16]}-",
            suffix=".arrow.part",
            dir=self._projection_staging_root,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        writer = _ArrowProjectionWriter(temporary, source_sha256=source.source_sha256)
        sampler = _FrameSelectionSampler()
        camera_topics = {camera.topic for camera in preflight.manifest.cameras}
        seen_topics: set[str] = set()
        topic_counts: dict[str, int] = defaultdict(int)
        last_alignment_timestamp: dict[str, int] = {}
        message_count = 0
        PROJECTION_SCAN.inc()
        PROJECTION_SOURCE_PASSES.inc()
        try:
            with closing(self._storage.open_reader(source.object_key)) as stream:
                messages = make_reader(cast(IO[bytes], stream)).iter_messages()
                for schema, channel, message in messages:
                    message_count += 1
                    if message_count > self._maximum_messages:
                        raise _blocked(
                            "the MCAP message count exceeds the projection materialization limit"
                        )
                    topic = str(channel.topic)
                    timestamp_ns = int(message.log_time)
                    seen_topics.add(topic)
                    topic_counts[topic] += 1
                    accepted = timestamp_ns > last_alignment_timestamp.get(topic, -1)
                    if accepted:
                        last_alignment_timestamp[topic] = timestamp_ns
                    if topic in camera_topics:
                        if channel.message_encoding != "json":
                            raise _blocked(
                                f"camera topic {topic!r} must use the supported JSON/JPEG "
                                "message encoding"
                            )
                        observation, image, perceptual_hash = _decode_image_projection(
                            message.data,
                            timestamp_ns=timestamp_ns,
                        )
                        sampler.observe_camera(
                            topic,
                            timestamp_ns,
                            luma_mean=observation.luma_mean,
                            perceptual_hash=perceptual_hash,
                            corrupt=observation.corrupt,
                        )
                        writer.append(
                            topic=topic,
                            timestamp_ns=timestamp_ns,
                            is_camera=True,
                            alignment_accepted=accepted,
                            value_json=None,
                            image=image,
                            luma_mean=observation.luma_mean,
                            fingerprint=observation.fingerprint,
                            perceptual_hash=perceptual_hash,
                            corrupt=observation.corrupt,
                        )
                    else:
                        if schema is None or self._decoder is None:
                            raise _blocked(f"topic {topic!r} has no configured value decoder")
                        if not self._decoder.supports(channel.message_encoding, schema.encoding):
                            raise _blocked(f"topic {topic!r} has no supported value decoder")
                        value = _bounded_decoded_value(
                            self._decoder.probe(
                                message_encoding=channel.message_encoding,
                                schema_encoding=schema.encoding,
                                schema_name=schema.name,
                                schema_data=schema.data,
                                message_data=message.data,
                            )
                        )
                        sampler.observe_signal(topic, timestamp_ns, value)
                        writer.append(
                            topic=topic,
                            timestamp_ns=timestamp_ns,
                            is_camera=False,
                            alignment_accepted=accepted,
                            value_json=json.dumps(
                                value,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode(),
                            image=None,
                            luma_mean=None,
                            fingerprint=None,
                            perceptual_hash=None,
                            corrupt=False,
                        )
            expected_topics = set(preflight.manifest.actual_topics)
            if seen_topics != expected_topics or any(
                topic_counts.get(name, 0) < 1 for name in expected_topics
            ):
                raise _blocked("the MCAP topic inventory differs from the committed Manifest")
            writer.close()
            content_sha256 = _file_sha256(temporary)
            project_component = hashlib.sha256(source.project_id.encode()).hexdigest()[:24]
            prefix = (
                f"staging/projections/{project_component}/{source.source_sha256}/"
                "arrow-projection-v1"
            )
            object_key = f"{prefix}.arrow"
            size_bytes = self._projection_store.publish_file(
                object_key, temporary, sha256=content_sha256
            )
            selection = sampler.manifest(
                source_sha256=source.source_sha256,
                camera_topics=tuple(sorted(camera_topics)),
            )
            selection_key = (
                f"derived/frame-selections/{project_component}/{source.source_sha256}/"
                "adaptive-2fps-v1.json"
            )
            selection_sha256, selection_size = self._projection_store.publish_json(
                selection_key, selection
            )
            source_frames = sampler.source_frame_count
            selected_groups = len(cast(list[object], selection["groups"]))
            AUTO_ANNOTATION_SAMPLE_RATIO.set(
                0 if source_frames == 0 else selected_groups / source_frames
            )
            now = datetime.now(timezone.utc)
            return ProjectionMaterializationV1(
                object_key=object_key,
                content_sha256=content_sha256,
                size_bytes=size_bytes,
                created_at=now,
                expires_at=now + self._projection_ttl,
                frame_selection=FrameSelectionManifestRefV1(
                    object_key=selection_key,
                    content_sha256=selection_sha256,
                    size_bytes=selection_size,
                    source_frame_count=source_frames,
                    selected_group_count=selected_groups,
                    camera_set=tuple(sorted(camera_topics)),
                ),
            )
        except IngestWorkflowPlanBlocked:
            writer.abort()
            raise
        except Exception as exc:
            writer.abort()
            raise _blocked(
                "the committed MCAP could not be materialized into bounded Arrow batches"
            ) from exc
        finally:
            with suppress(OSError):
                temporary.unlink()


class _LocalProjectionSession:
    """Worker-ephemeral Raw session shared by verification, QC, and alignment."""

    _COPY_CHUNK_BYTES = 8 * 1024 * 1024

    def __init__(
        self,
        *,
        source: IngestProjectionSourceV1,
        preflight: ManifestPreflightResultV1,
        storage: ReadableObjectStorage,
        decoder: DecoderProbe | None,
        maximum_messages: int,
        staging_root: Path,
        artifact_store: ProjectionArtifactStorePort | None,
    ) -> None:
        self.source = source
        self.preflight = preflight
        self.quality_data = QualityInputV1(
            rollout_id=source.rollout_id,
            source_sha256=source.source_sha256,
            start_ns=_datetime_ns(preflight.manifest.start_time),
            end_ns=_datetime_ns(preflight.manifest.end_time),
            topic_timestamps_ns={},
        )
        self.alignment_data = _alignment_metadata(source, preflight)
        self._storage = storage
        self._decoder = decoder
        self._maximum_messages = maximum_messages
        self._staging_root = staging_root
        self._artifact_store = artifact_store
        self._path: Path | None = None
        self._frame_selection: FrameSelectionManifestRefV1 | None = None

    def __enter__(self) -> _LocalProjectionSession:
        self._staging_root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f"raw-{self.source.source_sha256[:16]}-",
            suffix=".mcap.part",
            dir=self._staging_root,
        )
        os.close(descriptor)
        path = Path(temporary_name)
        digest = hashlib.sha256()
        PROJECTION_SOURCE_PASSES.inc()
        try:
            with (
                closing(self._storage.open_reader(self.source.object_key)) as source_stream,
                path.open("wb") as destination,
            ):
                while chunk := source_stream.read(self._COPY_CHUNK_BYTES):
                    if not isinstance(chunk, bytes):
                        raise TypeError("object storage reader must return bytes")
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            if digest.hexdigest() != self.source.source_sha256:
                raise _blocked("localized Raw SHA-256 differs from the committed Manifest")
        except Exception:
            with suppress(OSError):
                path.unlink()
            raise
        self._path = path
        return self

    def __exit__(self, *_args: object) -> None:
        path = self._path
        self._path = None
        if path is not None:
            with suppress(OSError):
                path.unlink()

    def open_reader(self) -> IO[bytes]:
        path = self._require_path()
        return path.open("rb")

    def quality_observations(self) -> Iterator[QualityStreamObservationV1]:
        camera_topics = {camera.topic for camera in self.preflight.manifest.cameras}
        seen_topics: set[str] = set()
        topic_counts: dict[str, int] = defaultdict(int)
        for schema, channel, message in self._messages():
            del schema
            topic = str(channel.topic)
            timestamp_ns = int(message.log_time)
            seen_topics.add(topic)
            topic_counts[topic] += 1
            if topic in camera_topics:
                if channel.message_encoding != "json":
                    raise _blocked(
                        f"camera topic {topic!r} must use the supported JSON/JPEG message encoding"
                    )
                observation, _image, _perceptual_hash = _decode_image_projection(
                    message.data,
                    timestamp_ns=timestamp_ns,
                )
                yield QualityStreamObservationV1(
                    topic=topic,
                    timestamp_ns=timestamp_ns,
                    is_camera=True,
                    luma_mean=observation.luma_mean,
                    fingerprint=observation.fingerprint,
                    corrupt=observation.corrupt,
                )
            else:
                yield QualityStreamObservationV1(
                    topic=topic,
                    timestamp_ns=timestamp_ns,
                    is_camera=False,
                )
        self._validate_topic_inventory(seen_topics, topic_counts)

    def alignment_samples(self) -> Iterator[tuple[str, TimedSampleV1]]:
        sampler = _FrameSelectionSampler()
        camera_topics = {camera.topic for camera in self.preflight.manifest.cameras}
        seen_topics: set[str] = set()
        topic_counts: dict[str, int] = defaultdict(int)
        last_alignment_timestamp: dict[str, int] = {}
        for schema, channel, message in self._messages():
            topic = str(channel.topic)
            timestamp_ns = int(message.log_time)
            seen_topics.add(topic)
            topic_counts[topic] += 1
            accepted = timestamp_ns > last_alignment_timestamp.get(topic, -1)
            if accepted:
                last_alignment_timestamp[topic] = timestamp_ns
            if topic in camera_topics:
                if channel.message_encoding != "json":
                    raise _blocked(
                        f"camera topic {topic!r} must use the supported JSON/JPEG message encoding"
                    )
                observation, image, perceptual_hash = _decode_image_projection(
                    message.data,
                    timestamp_ns=timestamp_ns,
                )
                sampler.observe_camera(
                    topic,
                    timestamp_ns,
                    luma_mean=observation.luma_mean,
                    perceptual_hash=perceptual_hash,
                    corrupt=observation.corrupt,
                )
                value: object = b"" if image is None else image
            else:
                if schema is None or self._decoder is None:
                    raise _blocked(f"topic {topic!r} has no configured value decoder")
                if not self._decoder.supports(channel.message_encoding, schema.encoding):
                    raise _blocked(f"topic {topic!r} has no supported value decoder")
                value = _bounded_decoded_value(
                    self._decoder.probe(
                        message_encoding=channel.message_encoding,
                        schema_encoding=schema.encoding,
                        schema_name=schema.name,
                        schema_data=schema.data,
                        message_data=message.data,
                    )
                )
                sampler.observe_signal(topic, timestamp_ns, value)
            if accepted:
                yield topic, TimedSampleV1(timestamp_ns=timestamp_ns, value=value)
        self._validate_topic_inventory(seen_topics, topic_counts)
        self._frame_selection = self._publish_frame_selection(sampler, camera_topics)

    def frame_selection(self) -> FrameSelectionManifestRefV1:
        if self._frame_selection is None:
            raise RuntimeError("alignment stream was not completely consumed")
        return self._frame_selection

    def _messages(self) -> Iterator[tuple[Any, Any, Any]]:
        try:
            from mcap.reader import make_reader
        except ImportError as exc:
            raise _blocked("the production MCAP data dependency is not installed") from exc
        PROJECTION_SCAN.inc()
        with self.open_reader() as stream:
            messages = make_reader(stream).iter_messages()
            for count, item in enumerate(messages, start=1):
                if count > self._maximum_messages:
                    raise _blocked("the MCAP message count exceeds the ingest processing limit")
                yield item

    def _publish_frame_selection(
        self,
        sampler: _FrameSelectionSampler,
        camera_topics: set[str],
    ) -> FrameSelectionManifestRefV1:
        if self._artifact_store is None:
            raise _blocked("the frame-selection artifact store is not configured")
        camera_set = tuple(sorted(camera_topics))
        selection = sampler.manifest(
            source_sha256=self.source.source_sha256,
            camera_topics=camera_set,
        )
        project_component = hashlib.sha256(self.source.project_id.encode()).hexdigest()[:24]
        selection_key = (
            f"derived/frame-selections/{project_component}/{self.source.source_sha256}/"
            "adaptive-2fps-v1.json"
        )
        selection_sha256, selection_size = self._artifact_store.publish_json(
            selection_key, selection
        )
        source_frames = sampler.source_frame_count
        selected_groups = len(cast(list[object], selection["groups"]))
        AUTO_ANNOTATION_SAMPLE_RATIO.set(
            0 if source_frames == 0 else selected_groups / source_frames
        )
        return FrameSelectionManifestRefV1(
            object_key=selection_key,
            content_sha256=selection_sha256,
            size_bytes=selection_size,
            source_frame_count=source_frames,
            selected_group_count=selected_groups,
            camera_set=camera_set,
        )

    def _validate_topic_inventory(
        self,
        seen_topics: set[str],
        topic_counts: Mapping[str, int],
    ) -> None:
        expected_topics = set(self.preflight.manifest.actual_topics)
        if seen_topics != expected_topics or any(
            topic_counts.get(name, 0) < 1 for name in expected_topics
        ):
            raise _blocked("the MCAP topic inventory differs from the committed Manifest")

    def _require_path(self) -> Path:
        if self._path is None:
            raise RuntimeError("local ingest projection session is not active")
        return self._path


def _alignment_metadata(
    source: IngestProjectionSourceV1,
    preflight: ManifestPreflightResultV1,
) -> AlignmentInputV1:
    camera_topics = {camera.topic for camera in preflight.manifest.cameras}
    expected_topics = tuple(sorted(preflight.manifest.actual_topics))
    return AlignmentInputV1(
        rollout_id=source.rollout_id,
        source_sha256=source.source_sha256,
        attempt_id=f"automatic-{source.source_sha256[:24]}",
        start_ns=_datetime_ns(preflight.manifest.start_time),
        end_ns=_datetime_ns(preflight.manifest.end_time),
        streams={
            topic: ModalityStreamV1(
                kind=_modality_kind(topic, camera_topics),
                samples=(),
            )
            for topic in expected_topics
        },
    )


def _datetime_ns(value: datetime) -> int:
    utc = value.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = utc - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000


class _ArrowProjectionWriter:
    """Arrow batches bounded by rows and bytes, independent of rollout duration."""

    _BATCH_ROWS = 4_096
    _BATCH_BYTES = 48 * 1024 * 1024

    def __init__(self, path: Path, *, source_sha256: str) -> None:
        import pyarrow as pa
        import pyarrow.ipc as ipc

        self._pa = pa
        self._sink = pa.OSFile(str(path), "wb")
        self._schema = pa.schema(
            [
                pa.field("topic", pa.string(), nullable=False),
                pa.field("timestamp_ns", pa.int64(), nullable=False),
                pa.field("is_camera", pa.bool_(), nullable=False),
                pa.field("alignment_accepted", pa.bool_(), nullable=False),
                pa.field("value_json", pa.binary()),
                pa.field("image", pa.binary()),
                pa.field("luma_mean", pa.float32()),
                pa.field("fingerprint", pa.string()),
                pa.field("perceptual_hash", pa.string()),
                pa.field("corrupt", pa.bool_(), nullable=False),
            ],
            metadata={
                b"hc.schema": b"ingest-projection/arrow-v1",
                b"hc.source_sha256": source_sha256.encode(),
            },
        )
        self._writer = ipc.new_file(self._sink, self._schema)
        self._columns: dict[str, list[object]] = {field.name: [] for field in self._schema}
        self._buffered_bytes = 0
        self.peak_buffered_bytes = 0
        self._closed = False

    def append(self, **row: object) -> None:
        if self._closed:
            raise RuntimeError("projection writer is closed")
        if set(row) != set(self._columns):
            raise ValueError("projection row does not match its Arrow schema")
        row_bytes = sum(_projection_value_size(value) for value in row.values())
        if self._columns["topic"] and self._buffered_bytes + row_bytes > self._BATCH_BYTES:
            self._flush()
        for name in self._columns:
            self._columns[name].append(row[name])
        self._buffered_bytes += row_bytes
        self.peak_buffered_bytes = max(self.peak_buffered_bytes, self._buffered_bytes)
        if (
            len(self._columns["topic"]) >= self._BATCH_ROWS
            or self._buffered_bytes >= self._BATCH_BYTES
        ):
            self._flush()

    def close(self) -> None:
        if self._closed:
            return
        self._flush()
        self._writer.close()
        self._sink.close()
        self._pa.default_memory_pool().release_unused()
        self._closed = True

    def abort(self) -> None:
        if self._closed:
            return
        with suppress(Exception):
            self._writer.close()
        with suppress(Exception):
            self._sink.close()
        self._closed = True

    def _flush(self) -> None:
        if not self._columns["topic"]:
            return
        batch = self._pa.record_batch(
            [self._columns[field.name] for field in self._schema], schema=self._schema
        )
        self._writer.write_batch(batch)
        del batch
        self._columns = {field.name: [] for field in self._schema}
        self._buffered_bytes = 0
        self._pa.default_memory_pool().release_unused()


def _projection_value_size(value: object) -> int:
    if value is None:
        return 1
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode())
    return 8


def _projection_rows(path: Path, *, columns: Sequence[str]):  # type: ignore[no-untyped-def]
    """Yield Python rows one bounded record batch at a time; never read_all()."""

    import pyarrow as pa
    import pyarrow.ipc as ipc

    with pa.memory_map(str(path), "r") as source:
        reader = ipc.open_file(source)
        if (reader.schema.metadata or {}).get(b"hc.schema") != b"ingest-projection/arrow-v1":
            raise ValueError("projection artifact schema marker is invalid")
        for index in range(reader.num_record_batches):
            batch = reader.get_batch(index).select(columns)
            yield from batch.to_pylist()


def _projection_sample_rows(
    path: Path,
) -> Iterator[tuple[str, int, bool, bool, bytes | None, bytes | None]]:
    """Read JPEG projection columns scalar-by-scalar without batch.to_pylist()."""

    import pyarrow as pa
    import pyarrow.ipc as ipc

    columns = (
        "topic",
        "timestamp_ns",
        "is_camera",
        "alignment_accepted",
        "value_json",
        "image",
    )
    with pa.memory_map(str(path), "r") as source:
        reader = ipc.open_file(source)
        if (reader.schema.metadata or {}).get(b"hc.schema") != b"ingest-projection/arrow-v1":
            raise ValueError("projection artifact schema marker is invalid")
        for batch_index in range(reader.num_record_batches):
            batch = reader.get_batch(batch_index).select(columns)
            arrays = {name: batch.column(name) for name in columns}
            for row_index in range(batch.num_rows):
                raw_value = arrays["value_json"][row_index].as_py()
                raw_image = arrays["image"][row_index].as_py()
                yield (
                    str(arrays["topic"][row_index].as_py()),
                    int(arrays["timestamp_ns"][row_index].as_py()),
                    bool(arrays["is_camera"][row_index].as_py()),
                    bool(arrays["alignment_accepted"][row_index].as_py()),
                    None if raw_value is None else bytes(raw_value),
                    None if raw_image is None else bytes(raw_image),
                )


class _FrameSelectionSampler:
    """Decode-free candidate selection after the one ingest image decode."""

    _UNIFORM_INTERVAL_NS = 500_000_000
    _REFINEMENT_NS = 500_000_000
    _MAX_GROUPS = 10_000

    def __init__(self) -> None:
        self.source_frame_count = 0
        self._last_uniform: dict[str, int] = {}
        self._last_luma: dict[str, float] = {}
        self._last_hash: dict[str, str] = {}
        self._last_signal: dict[str, str] = {}
        self._recent_camera: dict[str, deque[int]] = defaultdict(deque)
        self._refine_until = -1
        self._groups: dict[int, set[str]] = {}

    def observe_camera(
        self,
        topic: str,
        timestamp_ns: int,
        *,
        luma_mean: float | None,
        perceptual_hash: str | None,
        corrupt: bool,
    ) -> None:
        self.source_frame_count += 1
        recent = self._recent_camera[topic]
        recent.append(timestamp_ns)
        while recent and recent[0] < timestamp_ns - self._REFINEMENT_NS:
            recent.popleft()
        last_uniform = self._last_uniform.get(topic)
        if last_uniform is None or timestamp_ns - last_uniform >= self._UNIFORM_INTERVAL_NS:
            self._select(timestamp_ns, "uniform-2fps")
            self._last_uniform[topic] = timestamp_ns
        previous_luma = self._last_luma.get(topic)
        if (
            luma_mean is not None
            and previous_luma is not None
            and abs(luma_mean - previous_luma) >= 18
        ):
            self._select(timestamp_ns, "luma-change")
        if luma_mean is not None:
            self._last_luma[topic] = luma_mean
        previous_hash = self._last_hash.get(topic)
        if (
            perceptual_hash is not None
            and previous_hash is not None
            and _hex_hamming(perceptual_hash, previous_hash) >= 12
        ):
            self._select(timestamp_ns, "perceptual-change")
        if perceptual_hash is not None:
            self._last_hash[topic] = perceptual_hash
        if timestamp_ns <= self._refine_until:
            self._select(timestamp_ns, "event-refinement")
        if corrupt:
            self._select(timestamp_ns, "corrupt-frame-boundary")

    def observe_signal(self, topic: str, timestamp_ns: int, value: object) -> None:
        normalized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        prior = self._last_signal.get(topic)
        self._last_signal[topic] = normalized
        lowered = topic.lower()
        if (
            prior is None
            or prior == normalized
            or not any(marker in lowered for marker in ("action", "joint", "command", "event"))
        ):
            return
        self._select(timestamp_ns, "robot-state-change")
        for recent in self._recent_camera.values():
            for camera_timestamp in recent:
                self._select(camera_timestamp, "event-refinement")
        self._refine_until = max(self._refine_until, timestamp_ns + self._REFINEMENT_NS)

    def manifest(self, *, source_sha256: str, camera_topics: tuple[str, ...]) -> dict[str, object]:
        groups = [
            {
                "timestamp_ns": timestamp_ns,
                "camera_set": list(camera_topics),
                "reasons": sorted(reasons),
            }
            for timestamp_ns, reasons in sorted(self._groups.items())
        ]
        return {
            "schema_version": 1,
            "sampling_version": "adaptive-2fps-v1",
            "source_sha256": source_sha256,
            "camera_set": list(camera_topics),
            "source_frame_count": self.source_frame_count,
            "groups": groups,
        }

    def _select(self, timestamp_ns: int, reason: str) -> None:
        if timestamp_ns not in self._groups and len(self._groups) >= self._MAX_GROUPS:
            return
        self._groups.setdefault(timestamp_ns, set()).add(reason)


def _hex_hamming(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return 0


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_decoded_value(value: object) -> object:
    """Normalize a decoder result into bounded JSON without persisting object locators."""

    remaining = [10_000]

    def normalize(candidate: object, depth: int = 0) -> object:
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 12:
            raise ValueError("decoded message exceeds the structural limit")
        if candidate is None or isinstance(candidate, (bool, int)):
            return candidate
        if isinstance(candidate, float):
            if not math.isfinite(candidate):
                raise ValueError("decoded message contains a non-finite number")
            return candidate
        if isinstance(candidate, str):
            if len(candidate) > 65_536:
                raise ValueError("decoded message string exceeds the size limit")
            return candidate
        if isinstance(candidate, Mapping):
            if len(candidate) > 4096:
                raise ValueError("decoded message object exceeds the field limit")
            normalized: dict[str, object] = {}
            for key, item in candidate.items():
                if not isinstance(key, str) or not key or len(key) > 512:
                    raise ValueError("decoded message contains an invalid field name")
                normalized[key] = normalize(item, depth + 1)
            return normalized
        if isinstance(candidate, Sequence) and not isinstance(
            candidate, (bytes, bytearray, memoryview)
        ):
            if len(candidate) > 150_000:
                raise ValueError("decoded message array exceeds the item limit")
            return [normalize(item, depth + 1) for item in candidate]
        model_dump = getattr(candidate, "model_dump", None)
        if callable(model_dump):
            return normalize(model_dump(mode="json"), depth + 1)
        slot_names: list[str] = []
        for candidate_type in type(candidate).__mro__:
            declared_slots = candidate_type.__dict__.get("__slots__", ())
            if isinstance(declared_slots, str):
                declared_slots = (declared_slots,)
            if not isinstance(declared_slots, Sequence):
                continue
            for name in declared_slots:
                if (
                    isinstance(name, str)
                    and name
                    and not name.startswith("_")
                    and name not in slot_names
                ):
                    slot_names.append(name)
        if slot_names:
            if len(slot_names) > 4096:
                raise ValueError("decoded message object exceeds the field limit")
            slot_values = {
                name: getattr(candidate, name) for name in slot_names if hasattr(candidate, name)
            }
            # The canonical data streams are field values, not decoder-library
            # wrapper objects. Preserve structure for multi-field messages while
            # projecting a single-field ROS message directly as its scalar/vector.
            if len(slot_values) == 1:
                return normalize(next(iter(slot_values.values())), depth + 1)
            return normalize(slot_values, depth + 1)
        attributes = getattr(candidate, "__dict__", None)
        if isinstance(attributes, Mapping):
            return normalize(
                {key: item for key, item in attributes.items() if not key.startswith("_")},
                depth + 1,
            )
        raise ValueError(
            f"decoded message type {type(candidate).__name__!r} is not JSON-compatible"
        )

    normalized = normalize(value)
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > 1024 * 1024:
        raise ValueError("decoded message exceeds the one MiB value limit")
    return normalized


def _image_observation(message_data: bytes, *, timestamp_ns: int) -> ImageObservation:
    """Decode the platform JSON/JPEG camera envelope into bounded QC facts."""

    observation, _encoded_image = _decode_image_observation(
        message_data,
        timestamp_ns=timestamp_ns,
    )
    return observation


def _decode_image_observation(
    message_data: bytes,
    *,
    timestamp_ns: int,
) -> tuple[ImageObservation, bytes | None]:
    """Return both QC facts and the verified JPEG bytes used by alignment/media."""

    observation, encoded_image, _perceptual_hash = _decode_image_projection(
        message_data,
        timestamp_ns=timestamp_ns,
    )
    return observation, encoded_image


def _decode_image_projection(
    message_data: bytes,
    *,
    timestamp_ns: int,
) -> tuple[ImageObservation, bytes | None, str | None]:
    """Decode JPEG once and derive QC plus low-cost perceptual sampling facts."""

    try:
        payload = json.loads(message_data)
        if not isinstance(payload, dict) or payload.get("encoding") != "jpeg":
            raise ValueError("camera message is not a JSON/JPEG envelope")
        encoded_value = payload.get("data_base64")
        if not isinstance(encoded_value, str):
            raise ValueError("camera message omits data_base64")
        encoded_image = base64.b64decode(encoded_value, validate=True)
        fingerprint = hashlib.sha256(encoded_image).hexdigest()
        if payload.get("jpeg_sha256") != fingerprint:
            raise ValueError("camera JPEG hash does not match its envelope")
        with Image.open(BytesIO(encoded_image)) as image:
            image.load()
            if (
                image.format != "JPEG"
                or image.width != payload.get("width")
                or image.height != payload.get("height")
            ):
                raise ValueError("camera JPEG properties do not match its envelope")
            luma = image.convert("L")
            luma_mean = float(ImageStat.Stat(luma).mean[0])
            reduced = luma.resize((9, 8), Image.Resampling.BILINEAR)
            pixels = list(reduced.getdata())
            bits = 0
            for row in range(8):
                offset = row * 9
                for column in range(8):
                    bits = (bits << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
            perceptual_hash = f"{bits:016x}"
    except (
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        OSError,
        TypeError,
        UnidentifiedImageError,
        ValueError,
    ):
        return ImageObservation(timestamp_ns=timestamp_ns, corrupt=True), None, None
    return (
        ImageObservation(
            timestamp_ns=timestamp_ns,
            luma_mean=luma_mean,
            fingerprint=fingerprint,
        ),
        encoded_image,
        perceptual_hash,
    )


def _modality_kind(topic: str, camera_topics: set[str]) -> ModalityKind:
    if topic in camera_topics:
        return ModalityKind.IMAGE
    lowered = topic.lower()
    if "action" in lowered or "command" in lowered:
        return ModalityKind.ACTION
    if "point" in lowered or "lidar" in lowered:
        return ModalityKind.POINT_CLOUD
    return ModalityKind.DISCRETE


def _blocked(detail: str) -> IngestWorkflowPlanBlocked:
    return IngestWorkflowPlanBlocked(detail)
