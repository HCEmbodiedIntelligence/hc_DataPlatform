"""Resolve an ingest workflow from immutable upload and processing-plan facts."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timezone
from typing import IO, Any, cast
from urllib.parse import quote

from hc_data_platform.alignment.models import (
    AlignmentInputV1,
    AlignmentProfileV1,
    ModalityKind,
    ModalityStreamV1,
    TimedSampleV1,
)
from hc_data_platform.ingest.models import ManifestPreflightResultV1
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot
from hc_data_platform.lance_catalog.ports import LanceCatalogPort
from hc_data_platform.quality.models import QualityInputV1, QualityProfileV1
from hc_data_platform.verification.ports import ReadableObjectStorage

from .ingest_dispatch import IngestWorkflowPlanBlocked
from .models import (
    AlignmentActivityInput,
    IngestRolloutWorkflowInput,
    ManifestActivityInput,
    QualityActivityInput,
    VerificationActivityInput,
)


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
        maximum_messages: int = 250_000,
    ) -> None:
        if maximum_messages < 1:
            raise ValueError("maximum_messages must be positive")
        self._connection_factory = connection_factory
        self._storage = storage
        self._catalog = catalog
        self._maximum_messages = maximum_messages

    def resolve(
        self,
        *,
        project_id: str,
        region_code: str,
        session_id: str,
        rollout_id: str,
        data_package_id: str,
    ) -> IngestRolloutWorkflowInput:
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
                dataset_id, schema_snapshot_id = self._dataset_target(
                    cursor, project_id, region_code
                )
        finally:
            connection.close()

        snapshot = self._ensure_dataset_schema(
            project_id=project_id,
            dataset_id=dataset_id,
            schema_snapshot_id=schema_snapshot_id,
            preflight=preflight,
            profile=profile,
        )
        quality_data, alignment_data = self._project_mcap(
            object_key=object_key,
            rollout_id=rollout_id,
            source_sha256=source_sha256,
            preflight=preflight,
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
            project_id=project_id,
            region_code=region_code,
            dataset_id=dataset_id,
            rollout_id=rollout_id,
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
                project_id=project_id,
                region_code=region_code,
                data=quality_data,
                profile=profile,
            ),
            alignment=AlignmentActivityInput(
                project_id=project_id,
                region_code=region_code,
                dataset_id=dataset_id,
                schema_snapshot_id=schema_snapshot_id,
                data=alignment_data,
                profile=alignment_profile,
            ),
        )

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
        candidates = [
            profile
            for row in cursor.fetchall()
            if (profile := QualityProfileV1.model_validate(row[0])).required_topics == expected
        ]
        if len(candidates) != 1:
            raise _blocked(
                "the project must persist exactly one quality profile for the Manifest topic set"
            )
        return candidates[0]

    @staticmethod
    def _dataset_target(cursor: Any, project_id: str, region_code: str) -> tuple[str, str]:
        cursor.execute(
            """
            SELECT dataset_id, dataset_schema_snapshot_id
            FROM annotation.tag_schema_bindings
            WHERE project_id = %s AND region_code = %s AND task_kind = 'TAGGING'
            ORDER BY dataset_id, dataset_schema_snapshot_id
            """,
            (project_id, region_code),
        )
        candidates = [(str(row[0]), str(row[1])) for row in cursor.fetchall()]
        if len(candidates) != 1:
            raise _blocked("the scope must persist exactly one published Tag Schema dataset target")
        return candidates[0]

    def _ensure_dataset_schema(
        self,
        *,
        project_id: str,
        dataset_id: str,
        schema_snapshot_id: str,
        preflight: ManifestPreflightResultV1,
        profile: QualityProfileV1,
    ) -> DatasetSchemaSnapshot:
        frequency_hz = profile.target_frequency_hz
        if not 1 <= frequency_hz <= 1000:
            raise _blocked("the quality profile frequency cannot be used for alignment")
        fields = {topic: "string" for topic in preflight.manifest.actual_topics}
        candidate = DatasetSchemaSnapshot.create(
            project_id=project_id,
            dataset_id=dataset_id,
            schema_snapshot_id=schema_snapshot_id,
            frequency_hz=float(frequency_hz),
            fields=fields,
        )
        try:
            self._catalog.register_schema(candidate)
        except Exception as exc:
            raise _blocked("the compatible dataset schema could not be registered") from exc
        return candidate

    def _project_mcap(
        self,
        *,
        object_key: str,
        rollout_id: str,
        source_sha256: str,
        preflight: ManifestPreflightResultV1,
    ) -> tuple[QualityInputV1, AlignmentInputV1]:
        try:
            from mcap.reader import make_reader
        except ImportError as exc:
            raise _blocked("the production MCAP data dependency is not installed") from exc

        timestamps: dict[str, list[int]] = defaultdict(list)
        samples: dict[str, list[TimedSampleV1]] = defaultdict(list)
        message_count = 0
        with closing(self._storage.open_reader(object_key)) as stream:
            try:
                messages = make_reader(cast(IO[bytes], stream)).iter_messages()
                for _schema, channel, message in messages:
                    message_count += 1
                    if message_count > self._maximum_messages:
                        raise _blocked("the MCAP message count exceeds the workflow input limit")
                    topic = str(channel.topic)
                    timestamp_ns = int(message.log_time)
                    timestamps[topic].append(timestamp_ns)
                    value = "mcap://{object}#{topic}?log_time={time}&sha256={digest}".format(
                        object=quote(object_key, safe="/="),
                        topic=quote(topic, safe="/"),
                        time=timestamp_ns,
                        digest=hashlib.sha256(message.data).hexdigest(),
                    )
                    if not samples[topic] or samples[topic][-1].timestamp_ns < timestamp_ns:
                        samples[topic].append(TimedSampleV1(timestamp_ns=timestamp_ns, value=value))
            except IngestWorkflowPlanBlocked:
                raise
            except Exception as exc:
                raise _blocked(
                    "the committed MCAP could not be projected into workflow input"
                ) from exc

        expected_topics = set(preflight.manifest.actual_topics)
        if set(timestamps) != expected_topics or any(
            not timestamps[name] for name in expected_topics
        ):
            raise _blocked("the MCAP topic inventory differs from the committed Manifest")
        start_ns = _datetime_ns(preflight.manifest.start_time)
        end_ns = _datetime_ns(preflight.manifest.end_time)
        camera_topics = {camera.topic for camera in preflight.manifest.cameras}
        streams = {
            topic: ModalityStreamV1(
                kind=_modality_kind(topic, camera_topics),
                samples=tuple(samples[topic]),
            )
            for topic in sorted(expected_topics)
        }
        return (
            QualityInputV1(
                rollout_id=rollout_id,
                source_sha256=source_sha256,
                start_ns=start_ns,
                end_ns=end_ns,
                topic_timestamps_ns={
                    topic: tuple(values) for topic, values in sorted(timestamps.items())
                },
            ),
            AlignmentInputV1(
                rollout_id=rollout_id,
                source_sha256=source_sha256,
                attempt_id=f"automatic-{source_sha256[:24]}",
                start_ns=start_ns,
                end_ns=end_ns,
                streams=streams,
            ),
        )


def _datetime_ns(value: datetime) -> int:
    utc = value.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = utc - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000


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
