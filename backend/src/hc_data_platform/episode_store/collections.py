"""Serialized dataset snapshots over immutable Episode references, with recovery."""

from contextlib import contextmanager

from hc_data_platform.core.context import current_request_context

from .catalog_models import CollectionVersion
from .commit import EpisodePackageCommitter
from .models import CollectionSnapshot, DataVersionRef, canonical, digest
from .postgres import PackageConflict, assert_scope


def collection_scope(dataset_id, project_id=None):
    context = current_request_context()
    if (
        not context.organization_id
        or not context.project_id
        or not context.region_code
        or (project_id is not None and project_id != context.project_id)
    ):
        raise PermissionError("collection access requires an exact organization/project/region")
    return context.organization_id, context.project_id, context.region_code, dataset_id


class EpisodeCollections:
    def __init__(
        self, packages, storage, *, legacy=None, legacy_entries=None, checkpoint=lambda stage: None
    ):
        self.packages, self.storage = packages, storage
        self.legacy, self.legacy_entries = legacy, legacy_entries
        self.checkpoint = checkpoint

    @contextmanager
    def writer_lock(self, scope):
        connection = self.packages.connections()
        try:
            connection.execute(
                "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
                ("episode-collection/1:" + digest(scope),),
            )
            connection.commit()
            yield
        finally:
            connection.close()

    @staticmethod
    def _view(row):
        snapshot = CollectionSnapshot.model_validate(row[0])
        if snapshot.content_hash != row[1]:
            raise PackageConflict("collection document hash mismatch")
        return CollectionVersion(
            **snapshot.model_dump(mode="python"), manifest_sha256=row[1], created_at=row[2]
        )

    def current(self, dataset_id, *, project_id=None):
        scope = collection_scope(dataset_id, project_id)
        with self.packages.transaction() as cursor:
            cursor.execute(
                """SELECT c.document,c.content_hash,c.created_at FROM episode_store.collections c
                JOIN episode_store.collection_heads h USING
                    (organization_id,project_id,region_code,dataset_id,collection_version_id)
                WHERE h.organization_id=%s AND h.project_id=%s AND h.region_code=%s
                  AND h.dataset_id=%s""",
                scope,
            )
            row = cursor.fetchone()
            return self._view(row) if row else None

    def get(self, dataset_id, *, version=None, collection_version_id=None, project_id=None):
        scope = collection_scope(dataset_id, project_id)
        if version is None and collection_version_id is None:
            result = self.current(dataset_id, project_id=project_id)
            if result is None:
                raise KeyError("dataset has no published Episode collection")
            return result
        with self.packages.transaction() as cursor:
            cursor.execute(
                """SELECT document,content_hash,created_at FROM episode_store.collections
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s
                  AND (%s::bigint IS NULL OR version=%s)
                  AND (%s::text IS NULL OR collection_version_id=%s)""",
                (*scope, version, version, collection_version_id, collection_version_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError("collection version not found in request scope")
            return self._view(row)

    def list_versions(self, dataset_id, *, project_id=None):
        scope = collection_scope(dataset_id, project_id)
        with self.packages.transaction() as cursor:
            cursor.execute(
                """SELECT document,content_hash,created_at FROM episode_store.collections
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s
                ORDER BY version""",
                scope,
            )
            return tuple(self._view(row) for row in cursor.fetchall())

    def publication(self, data: DataVersionRef):
        assert_scope(data.identity)
        with self.packages.transaction() as cursor:
            cursor.execute(
                """SELECT c.document,c.content_hash,c.created_at FROM episode_store.collections c
                JOIN episode_store.collection_publications p USING
                  (organization_id,project_id,region_code,dataset_id,collection_version_id)
                WHERE p.organization_id=%s AND p.project_id=%s AND p.region_code=%s
                  AND p.dataset_id=%s AND p.data_version_id=%s""",
                (*data.identity.scope, data.identity.dataset_id, data.data_version_id),
            )
            row = cursor.fetchone()
            return self._view(row) if row else None

    def publish(self, data: DataVersionRef, schema):
        """Append or replace one Episode; identical retries return the original snapshot."""
        from .schemas import EpisodeSchemas

        identity = data.identity
        assert_scope(identity)
        scope = collection_scope(identity.dataset_id, identity.project_id)
        # Only persisted complete packages may enter a visible collection.
        receipt = self.packages.get(identity, data.data_version_id)
        if receipt.data != data:
            raise PackageConflict("requested Episode reference differs from committed receipt")
        if (
            schema.project_id != identity.project_id
            or schema.dataset_id != identity.dataset_id
            or schema.schema_snapshot_id != data.schema_snapshot_id
        ):
            raise PackageConflict("Episode does not match the frozen dataset schema")
        EpisodeSchemas(self.packages).register_schema(schema)
        with self.writer_lock(scope):
            previous_publication = self.publication(data)
            if previous_publication is not None:
                return previous_publication
            current = self.current(identity.dataset_id)
            legacy = (
                self.legacy.current_version(identity.dataset_id, project_id=identity.project_id)
                if self.legacy
                else None
            )
            if current is not None:
                if (
                    current.schema_fingerprint != schema.fingerprint
                    or current.frequency_hz != schema.frequency_hz
                ):
                    raise PackageConflict("collection schema/frequency changed")
                entries, version = list(current.episodes), current.version + 1
                # An undrained historical writer cannot silently overwrite collection numbering.
                if legacy is not None and legacy.version >= current.version:
                    raise PackageConflict("legacy writer advanced after Episode collection switch")
            else:
                if legacy is not None and (
                    legacy.schema_fingerprint != schema.fingerprint
                    or legacy.frequency_hz != schema.frequency_hz
                ):
                    raise PackageConflict("legacy and new collection schema/frequency differ")
                if legacy is not None and self.legacy_entries is None:
                    raise PackageConflict(
                        "historical Episodes require explicit bindings before switch"
                    )
                entries = [] if legacy is None else list(self.legacy_entries(legacy))
                version = 1 if legacy is None else legacy.version + 1
                if legacy is not None and {e.identity.rollout_id for e in entries} != set(
                    legacy.committed_rollouts
                ):
                    raise PackageConflict("historical collection bindings are incomplete")
            index = next(
                (
                    i
                    for i, old in enumerate(entries)
                    if old.identity.episode_id == identity.episode_id
                ),
                None,
            )
            if index is None:
                entries.append(data)
            else:
                if entries[index].identity != identity:
                    raise PackageConflict("Episode/rollout binding changed between data versions")
                entries[index] = data
            fields = dict(
                organization_id=scope[0],
                project_id=scope[1],
                region_code=scope[2],
                dataset_id=scope[3],
                version=version,
                schema_snapshot_id=schema.schema_snapshot_id,
                schema_fingerprint=schema.fingerprint,
                frequency_hz=schema.frequency_hz,
                previous_collection_version_id=None
                if current is None
                else current.collection_version_id,
                episodes=[e.model_dump(mode="json") for e in entries],
            )
            snapshot = CollectionSnapshot(collection_version_id="cv_" + digest(fields), **fields)
            EpisodePackageCommitter(self.packages, self.storage)._put_json(
                snapshot.manifest_key, snapshot.model_dump(mode="json")
            )
            self.checkpoint("collection_object_verified")
            with self.packages.transaction() as cursor:
                cursor.execute(
                    """INSERT INTO episode_store.collections
                    (organization_id,project_id,region_code,dataset_id,collection_version_id,
                     version,content_hash,document) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    RETURNING created_at""",
                    (
                        *scope,
                        snapshot.collection_version_id,
                        version,
                        snapshot.content_hash,
                        canonical(snapshot.model_dump(mode="json")).decode(),
                    ),
                )
                created = cursor.fetchone()[0]
                cursor.execute(
                    """INSERT INTO episode_store.collection_heads
                    (organization_id,project_id,region_code,dataset_id,collection_version_id)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (organization_id,project_id,region_code,dataset_id)
                    DO UPDATE SET collection_version_id=EXCLUDED.collection_version_id""",
                    (*scope, snapshot.collection_version_id),
                )
                cursor.execute(
                    """INSERT INTO episode_store.collection_publications
                    (organization_id,project_id,region_code,dataset_id,collection_version_id,data_version_id)
                    VALUES (%s,%s,%s,%s,%s,%s)""",
                    (*scope, snapshot.collection_version_id, data.data_version_id),
                )
                cursor.execute(
                    """INSERT INTO episode_store.collection_events
                    (organization_id,project_id,region_code,dataset_id,collection_version_id,payload)
                    VALUES (%s,%s,%s,%s,%s,%s::jsonb)""",
                    (
                        *scope,
                        snapshot.collection_version_id,
                        canonical(receipt.model_dump(mode="json")).decode(),
                    ),
                )
            self.checkpoint("collection_database_committed")
            return CollectionVersion(
                **snapshot.model_dump(mode="python"),
                manifest_sha256=snapshot.content_hash,
                created_at=created,
            )
