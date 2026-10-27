"""Tenant- and region-scoped logical schemas, independent of physical storage."""

import math

from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot
from hc_data_platform.lance_catalog.schema_spec import compute_schema_fingerprint

from .errors import PackageConflict
from .models import canonical, digest
from .scope import collection_scope


class EpisodeSchemas:
    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def validate(snapshot):
        if (
            not math.isfinite(snapshot.frequency_hz)
            or snapshot.frequency_hz <= 0
            or compute_schema_fingerprint(
                snapshot.fields, semantic_contract=snapshot.semantic_contract
            )
            != snapshot.fingerprint
        ):
            raise PackageConflict("logical schema fingerprint/frequency is invalid")
        return snapshot

    def schema_for(self, project_id, dataset_id, schema_snapshot_id=None):
        scope = collection_scope(dataset_id, project_id)
        with self.repository.transaction() as cursor:
            cursor.execute(
                """SELECT document,content_hash FROM episode_store.dataset_schemas
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s
                AND (%s::text IS NULL OR schema_snapshot_id=%s)
                UNION ALL SELECT document,content_hash FROM episode_store.dataset_schema_versions
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s
                AND (%s::text IS NULL OR schema_snapshot_id=%s)""",
                (*scope, schema_snapshot_id, schema_snapshot_id, *scope, schema_snapshot_id, schema_snapshot_id),
            )
            rows = cursor.fetchall()
            if len(rows) > 1:
                raise PackageConflict("dataset has multiple formats; select an explicit schema version")
            row = rows[0] if rows else None
            if row is None:
                return None
            if digest(row[0]) != row[1]:
                raise PackageConflict("logical schema document hash mismatch")
            snapshot = self.validate(DatasetSchemaSnapshot.model_validate(row[0]))
            if snapshot.project_id != project_id or snapshot.dataset_id != dataset_id:
                raise PackageConflict("logical schema document identity mismatch")
            return snapshot

    def register_schema(self, snapshot):
        """Immutable per-format registration, retaining the historical v1 table."""
        scope = collection_scope(snapshot.dataset_id, snapshot.project_id)
        self.validate(snapshot)
        document = snapshot.model_dump(mode="json")
        versioned = (snapshot.semantic_contract or {}).get("algorithm") in {"capture-structure/v2", "capture-structure/v3"}
        table = "episode_store.dataset_schema_versions" if versioned else "episode_store.dataset_schemas"
        with self.repository.transaction() as cursor:
            cursor.execute(
                f"""INSERT INTO {table}
                (organization_id,project_id,region_code,dataset_id,schema_snapshot_id,
                 document,content_hash) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)
                ON CONFLICT DO NOTHING""",
                (
                    *scope,
                    snapshot.schema_snapshot_id,
                    canonical(document).decode(),
                    digest(document),
                ),
            )
            cursor.execute(
                f"""SELECT document,content_hash FROM {table}
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s"""
                + (" AND schema_snapshot_id=%s" if versioned else ""),
                (*scope, snapshot.schema_snapshot_id) if versioned else scope,
            )
            row = cursor.fetchone()
            if row is None or row[0] != document or row[1] != digest(document):
                raise PackageConflict("dataset logical schema is already frozen differently")

    def resolve_capture_schema(self, snapshot):
        """Retain reviewed legacy physical identities while enforcing the new contract."""
        from hc_data_platform.capture_schemes.adoption import physical_schema

        scope = collection_scope(snapshot.dataset_id, snapshot.project_id)
        self.validate(snapshot)
        if (snapshot.semantic_contract or {}).get("algorithm") in {"capture-structure/v2", "capture-structure/v3"}:
            return snapshot
        with self.repository.transaction() as cursor:
            return self.validate(physical_schema(cursor.connection, scope[:3], snapshot))
