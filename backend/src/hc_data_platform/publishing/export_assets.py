"""Resolve immutable export attachments inside the frozen dataset's tenant scope."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from hc_data_platform.aligned_media.models import AlignedMediaFrameReferenceV1
from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.ingest.ports import ObjectStoragePort
from hc_data_platform.ingest.raw_sources import PostgresRawSourceRepository
from hc_data_platform.lerobot_imports.committed import read_committed_manifest

from .adapters import parse_lance_version
from .models import PublishedDatasetManifestV1, PublishedRolloutV1


@dataclass(frozen=True)
class ExportVideoSource:
    object_key: str
    sha256: str
    size: int
    width: int
    height: int
    start_seconds: float
    end_seconds: float
    video_info: dict[str, Any] = field(default_factory=dict)


class ExportAssetsPort(Protocol):
    def episode_metadata(
        self,
        manifest: PublishedDatasetManifestV1,
        rollout: PublishedRolloutV1,
        source_metadata: dict[str, Any],
    ) -> dict[str, Any]: ...

    def video_source(
        self,
        manifest: PublishedDatasetManifestV1,
        rollout: PublishedRolloutV1,
        reference: AlignedMediaFrameReferenceV1,
    ) -> ExportVideoSource: ...

    def read_video(self, source: ExportVideoSource) -> Iterable[bytes]: ...


def export_error(code: str, detail: str) -> Exception:
    return problem(status=409, code=code, title="Export source is incomplete", detail=detail)


class PostgresExportAssets:
    def __init__(self, connections: Callable[[], Any], storage: ObjectStoragePort) -> None:
        self.connections, self.storage = connections, storage

    def _scope(
        self, manifest: PublishedDatasetManifestV1, rollout: PublishedRolloutV1
    ) -> tuple[str, str]:
        context = current_request_context()
        if context.project_id != manifest.project_id or (
            not context.organization_id and not context.service_identity
        ):
            raise export_error(
                "EXPORT_ASSET_SCOPE_MISMATCH", "Export requires a selected tenant scope."
            )
        with self.connections() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT DISTINCT lineage.organization_id, rollout.region_code
                FROM lance_rollout_lineage lineage
                JOIN ingest.rollouts rollout
                  ON rollout.organization_id=lineage.organization_id
                 AND rollout.project_id=lineage.project_id AND rollout.rollout_id=lineage.rollout_id
                 AND rollout.source_sha256=lineage.source_sha256
                WHERE lineage.project_id=%s AND lineage.dataset_id=%s
                  AND lineage.rollout_id=%s AND lineage.source_sha256=%s
                  AND lineage.version_added<=%s
                  AND (%s::text IS NULL OR lineage.organization_id=%s)
                  AND (%s::text IS NULL OR rollout.region_code=%s) LIMIT 2""",
                (
                    manifest.project_id,
                    manifest.dataset_id,
                    rollout.rollout_id,
                    rollout.source_mcap_sha256,
                    parse_lance_version(rollout.base_lance_version),
                    context.organization_id,
                    context.organization_id,
                    context.region_code,
                    context.region_code,
                ),
            )
            rows = cursor.fetchall()
        if len(rows) != 1:
            raise export_error(
                "EXPORT_ASSET_SCOPE_MISMATCH", "Frozen rollout has no unique tenant and region."
            )
        return str(rows[0][0]), str(rows[0][1])

    def episode_metadata(
        self,
        manifest: PublishedDatasetManifestV1,
        rollout: PublishedRolloutV1,
        source_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        organization, region = self._scope(manifest, rollout)
        metadata: dict[str, Any] = {
            "tags": [],
            "task": "Robot demonstration",
            "robot_type": "unknown",
        }
        with self.connections() as connection, connection.cursor() as cursor:
            if rollout.annotation_revision is not None:
                # Read the publication's exact revision and submission, never the
                # task's current draft/approved pointers, which may since have moved.
                cursor.execute(
                    """SELECT revision.tags, revision.content_hash, revision.tag_schema_id,
                              revision.tag_schema_version, submission.tag_schema_hash,
                              schema.document
                    FROM annotation.annotation_revisions revision
                    JOIN annotation.annotation_tasks task USING (task_id)
                    JOIN annotation.annotation_submissions submission
                      ON submission.task_id=revision.task_id
                     AND submission.revision=revision.revision
                    JOIN annotation.tag_schema_versions schema
                      ON schema.schema_id=revision.tag_schema_id
                     AND schema.version=revision.tag_schema_version
                     AND schema.project_id=task.project_id
                     AND schema.content_hash=submission.tag_schema_hash
                    WHERE task.organization_id=%s AND task.project_id=%s AND task.region_code=%s
                      AND task.dataset_id=%s AND task.rollout_id=%s
                      AND revision.task_id=%s AND revision.revision=%s
                      AND submission.submission_id=%s
                      AND submission.revision_content_hash=revision.content_hash""",
                    (
                        organization,
                        manifest.project_id,
                        region,
                        manifest.dataset_id,
                        rollout.rollout_id,
                        rollout.annotation_task_id,
                        rollout.annotation_revision,
                        rollout.annotation_submission_id,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise export_error(
                        "EXPORT_ANNOTATION_SNAPSHOT_MISSING",
                        "The frozen annotation revision is unavailable.",
                    )
                metadata.update(
                    tags=row[0],
                    revision_content_hash=row[1],
                    tag_schema_id=row[2],
                    tag_schema_version=row[3],
                    tag_schema_hash=row[4],
                    tag_schema=row[5],
                )
            from hc_data_platform.continuous_recordings.export_metadata import frozen_metadata

            metadata.update(
                frozen_metadata(cursor, self.storage, organization, region, manifest, rollout)
            )
            raw_id = source_metadata.get("raw_upload_id")
            if raw_id:
                cursor.execute(
                    """SELECT manifest_key, storage_prefix FROM ingest.raw_sources
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s
                      AND dataset_id=%s AND raw_source_id=%s""",
                    (organization, manifest.project_id, region, manifest.dataset_id, raw_id),
                )
                raw = cursor.fetchone()
                if raw is None or raw[0] != source_metadata.get("raw_manifest_key"):
                    raise export_error(
                        "EXPORT_ASSET_SCOPE_MISMATCH",
                        "Raw metadata does not belong to this export.",
                    )
            else:
                raw = None
        if raw is not None:
            raw_source = PostgresRawSourceRepository(self.connections).get_source(
                organization_id=organization,
                project_id=manifest.project_id,
                region_code=region,
                raw_source_id=raw_id,
            )
            if raw_source is None or raw_source.manifest_key != raw[0]:
                raise export_error(
                    "EXPORT_ASSET_SCOPE_MISMATCH", "Raw metadata does not belong to this export."
                )
            try:
                normalized, body = read_committed_manifest(self.storage, raw_source)
            except (ValueError, KeyError, TypeError) as exc:
                raise export_error(
                    "EXPORT_SOURCE_HASH_MISMATCH", "Raw manifest cannot be verified."
                ) from exc
            if hashlib.sha256(body).hexdigest() != rollout.source_mcap_sha256:
                raise export_error(
                    "EXPORT_SOURCE_HASH_MISMATCH", "Raw manifest changed after publication."
                )
            descriptors = {item["path"]: item for item in normalized["files"]}

            def read(relative: str) -> bytes:
                descriptor = descriptors[relative]
                content = b"".join(
                    self.storage.read_chunks(descriptor.get("object_key") or f"{raw[1]}/{relative}")
                )
                if (
                    len(content) != descriptor["size"]
                    or hashlib.sha256(content).hexdigest() != descriptor["sha256"]
                ):
                    raise export_error(
                        "EXPORT_SOURCE_HASH_MISMATCH", "Raw metadata differs from its receipt."
                    )
                return content

            info = json.loads(read("meta/info.json"))
            metadata["robot_type"] = info.get("robot_type") or "unknown"
            metadata["source_features"] = {
                key: value
                for key, value in info["features"].items()
                if key in {"action", "observation.state"} or value.get("dtype") == "video"
            }
            if "capture-context.json" in descriptors:
                capture = json.loads(read("capture-context.json"))
                index = source_metadata.get("episode_index")
                sources = [e for e in capture["episodes"] if e["episode_index"] == index]
                if len(sources) != 1:
                    raise export_error(
                        "EXPORT_SOURCE_EPISODE_MISSING", "Source episode is ambiguous."
                    )
                metadata.update(capture_context=capture, source_episode=sources[0])
                metadata["source_assets"] = {
                    "raw_source_id": raw_id,
                    "manifest_key": raw[0],
                    "files": [
                        {"path": name, "size": d["size"], "sha256": d["sha256"]}
                        for name, d in sorted(descriptors.items())
                    ],
                }
                for feature in metadata["source_features"].values():
                    if feature.get("dtype") == "video":
                        continue
                    from hc_data_platform import recording_fields as public

                    axes = (
                        public.axes(capture["profile"])
                        if info.get("recording_field_schema") == public.SCHEMA
                        else capture["profile"]["axes"]
                    )
                    feature["units"] = [axis["unit"] for axis in axes]
            if "meta/tasks.parquet" in descriptors:
                import pyarrow as pa
                import pyarrow.parquet as pq

                tasks = pq.read_table(pa.BufferReader(read("meta/tasks.parquet"))).to_pylist()
                for task in tasks:
                    if task.get("task_index") == source_metadata.get("task_index"):
                        metadata["task"] = (
                            task.get("task") or task.get("__index_level_0__") or metadata["task"]
                        )
                        break
        return metadata

    def video_source(
        self,
        manifest: PublishedDatasetManifestV1,
        rollout: PublishedRolloutV1,
        reference: AlignedMediaFrameReferenceV1,
    ) -> ExportVideoSource:
        organization, region = self._scope(manifest, rollout)
        with self.connections() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT media_object_key, object_manifest, media_width, media_height,
                          duration_seconds, timeline_json
                FROM aligned_media.artifacts
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND dataset_id=%s AND rollout_id=%s AND artifact_id=%s AND camera_id=%s
                  AND source_sha256=%s AND alignment_version=%s AND dataset_version<=%s
                  AND status='READY' AND dataset_committed_at IS NOT NULL AND deleted_at IS NULL""",
                (
                    organization,
                    manifest.project_id,
                    region,
                    manifest.dataset_id,
                    rollout.rollout_id,
                    reference.artifact_id,
                    reference.camera_id,
                    rollout.source_mcap_sha256,
                    reference.alignment_version,
                    parse_lance_version(rollout.base_lance_version),
                ),
            )
            row = cursor.fetchone()
        if row is None or row[0] != reference.object_key:
            raise export_error(
                "EXPORT_MEDIA_SOURCE_MISSING",
                "Camera reference has no matching committed media asset.",
            )
        original = (row[5] or {}).get("original_source")
        if original:
            return ExportVideoSource(
                original["object_key"],
                original["content_sha256"],
                int(original["size_bytes"]),
                int(original["width"]),
                int(original["height"]),
                float(original["start_seconds"]),
                float(original["end_seconds"]),
            )
        receipt = next((item for item in row[1] if item["key"] == reference.object_key), None)
        if receipt is None:
            raise export_error(
                "EXPORT_MEDIA_SOURCE_MISSING", "Camera video has no immutable object receipt."
            )
        return ExportVideoSource(
            reference.object_key,
            receipt["sha256"],
            int(receipt["size"]),
            int(row[2]),
            int(row[3]),
            0,
            float(row[4]),
            (row[5] or {}).get("video_info", {}),
        )

    def read_video(self, source: ExportVideoSource) -> Iterable[bytes]:
        return self.storage.read_chunks(source.object_key)
