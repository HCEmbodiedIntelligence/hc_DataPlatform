"""Dataset-owned publication projections, bound to an existing transaction."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from hc_data_platform.core.errors import problem

from .publication import DatasetReleaseProjection, release_version_id

if TYPE_CHECKING:
    from psycopg import Cursor


class PostgresDatasetPublicationProjection:
    def __init__(self, cursor: Cursor[Any]) -> None:
        self._cursor = cursor

    def project_release(self, request: DatasetReleaseProjection) -> None:
        self._project_release(self._cursor, request)

    def owner_id(
        self, *, organization_id: str, project_id: str, region_code: str, dataset_id: str
    ) -> str | None:
        self._cursor.execute(
            """SELECT owner_id FROM dataset_registry.datasets
            WHERE organization_id = %s AND project_id = %s
              AND region_code = %s AND dataset_id = %s""",
            (organization_id, project_id, region_code, dataset_id),
        )
        row = self._cursor.fetchone()
        return None if row is None else str(row[0])

    @classmethod
    def _project_release(
        cls,
        cursor: Cursor[Any],
        request: DatasetReleaseProjection,
    ) -> None:
        """Create the P06 business projection without copying Lance data.

        The child rows are small metadata projections. Their content references
        the same immutable source snapshot selected by ``source_version_id``;
        publishing therefore adds no second copy of the aligned data objects.
        """

        cursor.execute("SELECT to_regclass('dataset_registry.dataset_versions') IS NOT NULL")
        registry_available = cursor.fetchone()
        if registry_available is None or not bool(registry_available[0]):
            # The lower-level publication contract is also used by isolated
            # workers that intentionally do not install the P05/P06 projection.
            return

        source_version_id = request.source_version_id
        release_id = release_version_id(request.publication_version)
        cursor.execute(
            """
            SELECT organization_id, region_code, version_document
            FROM dataset_registry.dataset_versions
            WHERE project_id = %s AND dataset_id = %s AND version_id = %s
              AND version_scope = 'INTERNAL'
            ORDER BY organization_id, region_code
            """,
            (request.project_id, request.dataset_id, source_version_id),
        )
        sources = cursor.fetchall()
        if not sources:
            raise problem(
                status=409,
                code="PUBLICATION_BASE_PROJECTION_MISSING",
                title="Publication base snapshot is unavailable",
                detail="The selected Lance snapshot has no internal Dataset projection.",
                details={"source_version_id": request.source_version_id},
            )

        publication_manifest_id = f"publication-{request.publication_hash[:32]}"
        for organization_id, region_code, raw_document in sources:
            document = (
                json.loads(raw_document) if isinstance(raw_document, str) else dict(raw_document)
            )
            document.update(
                {
                    "version_id": release_id,
                    "display_version": request.publication_version,
                    "created_at": request.published_at.isoformat(),
                    "published_at": request.published_at.isoformat(),
                    "version_token": f"publication:{request.publication_hash}",
                }
            )
            summary = dict(document["manifest"])
            summary.update(
                {
                    "manifest_id": publication_manifest_id,
                    "sha256": request.publication_hash,
                    "entry_count": str(request.rollout_count),
                }
            )
            document["manifest"] = summary
            cursor.execute(
                """
                INSERT INTO dataset_registry.dataset_versions (
                    organization_id, project_id, region_code, dataset_id, version_id,
                    display_version, version_kind, version_status, created_at, published_at,
                    version_document, version_scope
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, 'READY', %s, %s, %s::jsonb,
                    'DATASET_RELEASE'
                )
                ON CONFLICT (
                    organization_id, project_id, region_code, dataset_id, version_id
                ) DO NOTHING
                """,
                (
                    organization_id,
                    request.project_id,
                    region_code,
                    request.dataset_id,
                    release_id,
                    request.publication_version,
                    str(document["kind"]),
                    request.published_at,
                    request.published_at,
                    json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ),
            )
            cls._copy_release_children(
                cursor,
                organization_id=str(organization_id),
                project_id=request.project_id,
                region_code=str(region_code),
                dataset_id=request.dataset_id,
                source_version_id=source_version_id,
                release_version_id=release_id,
                publication_manifest_id=publication_manifest_id,
                publication_hash=request.publication_hash,
            )
            cls._promote_dataset_current_release(
                cursor,
                organization_id=str(organization_id),
                project_id=request.project_id,
                region_code=str(region_code),
                dataset_id=request.dataset_id,
                release_version_id=release_id,
                display_version=request.publication_version,
                version_kind=str(document["kind"]),
                published_at=request.published_at,
                manifest_sha256=request.publication_hash,
            )

    @staticmethod
    def _promote_dataset_current_release(
        cursor: Cursor[Any],
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        release_version_id: str,
        display_version: str,
        version_kind: str,
        published_at: datetime,
        manifest_sha256: str,
    ) -> None:
        """Point the dataset summary at the newest explicit business release."""

        cursor.execute(
            """
            SELECT dataset_document, version, updated_at, activity_at
            FROM dataset_registry.datasets
            WHERE organization_id = %s AND project_id = %s AND region_code = %s
              AND dataset_id = %s
            FOR UPDATE
            """,
            (organization_id, project_id, region_code, dataset_id),
        )
        row = cursor.fetchone()
        if row is None:
            return
        document = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0])
        current = document.get("current_ready_version")
        if isinstance(current, dict) and current.get("version_id") == release_version_id:
            return
        if isinstance(current, dict) and current.get("version_id"):
            cursor.execute(
                """
                SELECT published_at
                FROM dataset_registry.dataset_versions
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND dataset_id = %s AND version_id = %s
                  AND version_scope = 'DATASET_RELEASE'
                """,
                (
                    organization_id,
                    project_id,
                    region_code,
                    dataset_id,
                    str(current["version_id"]),
                ),
            )
            current_row = cursor.fetchone()
            if current_row is not None and current_row[0] > published_at:
                return

        next_version = int(row[1]) + 1
        effective_updated_at = max(row[2], published_at)
        effective_activity_at = max(row[3], published_at)
        document.update(
            {
                "current_ready_version": {
                    "version_id": release_version_id,
                    "display_version": display_version,
                    "kind": version_kind,
                    "status": "READY",
                    "published_at": published_at.isoformat(),
                    "manifest_sha256": manifest_sha256,
                },
                "updated_at": effective_updated_at.isoformat(),
                "activity_at": effective_activity_at.isoformat(),
                "etag": f'"v{next_version}"',
            }
        )
        cursor.execute(
            """
            UPDATE dataset_registry.datasets
            SET version = %s, dataset_document = %s::jsonb,
                updated_at = %s, activity_at = %s
            WHERE organization_id = %s AND project_id = %s AND region_code = %s
              AND dataset_id = %s
            """,
            (
                next_version,
                json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                effective_updated_at,
                effective_activity_at,
                organization_id,
                project_id,
                region_code,
                dataset_id,
            ),
        )

    @staticmethod
    def _copy_release_children(
        cursor: Cursor[Any],
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        source_version_id: str,
        release_version_id: str,
        publication_manifest_id: str,
        publication_hash: str,
    ) -> None:
        identity = (
            release_version_id,
            organization_id,
            project_id,
            region_code,
            dataset_id,
            source_version_id,
        )
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_version_content_projections (
                organization_id, project_id, region_code, dataset_id, version_id,
                content_snapshot_id, content_snapshot_hash, manifest_id, manifest_sha256,
                operational_revision, content_document
            )
            SELECT organization_id, project_id, region_code, dataset_id, %s,
                   content_snapshot_id, content_snapshot_hash, %s, %s,
                   'publication:' || %s,
                   jsonb_set(
                     jsonb_set(
                       jsonb_set(content_document, '{version_id}', to_jsonb(%s::text)),
                       '{manifest,manifest_id}', to_jsonb(%s::text)
                     ),
                     '{manifest,sha256}', to_jsonb(%s::text)
                   )
            FROM dataset_registry.dataset_version_content_projections
            WHERE organization_id = %s AND project_id = %s AND region_code = %s
              AND dataset_id = %s AND version_id = %s
            ON CONFLICT DO NOTHING
            """,
            (
                release_version_id,
                publication_manifest_id,
                publication_hash,
                publication_hash,
                release_version_id,
                publication_manifest_id,
                publication_hash,
                organization_id,
                project_id,
                region_code,
                dataset_id,
                source_version_id,
            ),
        )
        copies = (
            (
                "dataset_version_episodes",
                "episode_id, storage_region_code, revision_id, ordinal, started_at, "
                "started_at_ns, included, success_state, task, robot_id, review_status, "
                "review_finding_count, has_finding, change_type",
                "episode_document",
            ),
            (
                "dataset_version_episode_revisions",
                "episode_id, revision_id, ordinal",
                "revision_document",
            ),
            (
                "dataset_version_schema_summaries",
                "schema_snapshot_id, channel_count",
                "schema_document",
            ),
            (
                "dataset_version_schema_details",
                "schema_snapshot_id, channel_count",
                "schema_document",
            ),
            (
                "dataset_version_source_provenance",
                "provenance_id, storage_region_code, upload_id, source_id, source_display_name, "
                "source_manifest_id, registered_at",
                "provenance_document",
            ),
            (
                "dataset_version_capacity_facts",
                "capacity_state, calculated_at",
                "capacity_document",
            ),
            (
                "dataset_version_manifest_entries",
                "entry_id, episode_id, revision_id, entry_role, size_bytes",
                "entry_document",
            ),
            (
                "dataset_version_required_storage",
                "object_id, object_role, size_bytes, safe_locator",
                "storage_document",
            ),
        )
        for table, columns, document_column in copies:
            cursor.execute(
                f"""
                INSERT INTO dataset_registry.{table} (
                    organization_id, project_id, region_code, dataset_id, version_id,
                    {columns}, {document_column}
                )
                SELECT organization_id, project_id, region_code, dataset_id, %s,
                       {columns},
                       jsonb_set({document_column}, '{{version_id}}', to_jsonb(%s::text))
                FROM dataset_registry.{table}
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND dataset_id = %s AND version_id = %s
                ON CONFLICT DO NOTHING
                """,
                (release_version_id, *identity),
            )
