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
            or compute_schema_fingerprint(snapshot.fields) != snapshot.fingerprint
        ):
            raise PackageConflict("logical schema fingerprint/frequency is invalid")
        return snapshot

    def schema_for(self, project_id, dataset_id):
        scope = collection_scope(dataset_id, project_id)
        with self.repository.transaction() as cursor:
            cursor.execute(
                """SELECT document,content_hash FROM episode_store.dataset_schemas
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s""",
                scope,
            )
            row = cursor.fetchone()
            if row is None:
                return None
            if digest(row[0]) != row[1]:
                raise PackageConflict("logical schema document hash mismatch")
            snapshot = self.validate(DatasetSchemaSnapshot.model_validate(row[0]))
            if snapshot.project_id != project_id or snapshot.dataset_id != dataset_id:
                raise PackageConflict("logical schema document identity mismatch")
            return snapshot

    def register_schema(self, snapshot):
        """Idempotent freeze. Schema evolution requires a separately defined migration."""
        scope = collection_scope(snapshot.dataset_id, snapshot.project_id)
        self.validate(snapshot)
        document = snapshot.model_dump(mode="json")
        with self.repository.transaction() as cursor:
            cursor.execute(
                """INSERT INTO episode_store.dataset_schemas
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
                """SELECT document,content_hash FROM episode_store.dataset_schemas
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s""",
                scope,
            )
            row = cursor.fetchone()
            if row is None or row[0] != document or row[1] != digest(document):
                raise PackageConflict("dataset logical schema is already frozen differently")
