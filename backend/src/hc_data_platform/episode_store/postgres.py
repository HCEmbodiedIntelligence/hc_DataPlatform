"""Scoped transactional index; physical package checks happen before commit."""

from contextlib import contextmanager

from hc_data_platform.core.context import current_request_context

from .models import CommitReceipt, EpisodeIdentity, PackageManifest, canonical, digest


class PackageConflict(ValueError):
    pass


def assert_scope(identity: EpisodeIdentity) -> None:
    context = current_request_context()
    if identity.scope != (context.organization_id, context.project_id, context.region_code):
        raise PermissionError("Episode data reference is outside the verified request scope")


class PostgresEpisodeStore:
    def __init__(self, connections):
        self.connections = connections

    @contextmanager
    def transaction(self):
        connection = self.connections()
        cursor = connection.cursor()
        try:
            yield cursor
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @contextmanager
    def writer_lock(self, identity):
        assert_scope(identity)
        # Session lock spans the object write and independent short DB transactions.
        connection = self.connections()
        key = "episode-package/1:" + digest(identity.key)
        try:
            connection.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (key,))
            connection.commit()
            yield
        finally:
            # Closing a lost/failed connection releases its session lock as well.
            connection.close()

    def reserve(self, manifest: PackageManifest) -> tuple[int, CommitReceipt | None]:
        source, identity = manifest.input, manifest.input.identity
        assert_scope(identity)
        with self.transaction() as cursor:
            cursor.execute(
                """INSERT INTO episode_store.bindings
                (organization_id, project_id, region_code, dataset_id, episode_id, rollout_id)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (*identity.key, identity.rollout_id),
            )
            cursor.execute(
                """SELECT rollout_id FROM episode_store.bindings WHERE organization_id=%s
                AND project_id=%s AND region_code=%s AND dataset_id=%s AND episode_id=%s""",
                identity.key,
            )
            binding = cursor.fetchone()
            if binding is None or binding[0] != identity.rollout_id:
                raise PackageConflict("Episode/rollout binding differs from frozen identity")
            cursor.execute(
                """SELECT version, content_hash, input_document, receipt_document
                FROM episode_store.versions WHERE organization_id=%s AND project_id=%s
                AND region_code=%s AND data_version_id=%s""",
                (*identity.scope, source.data_version_id),
            )
            old = cursor.fetchone()
            if old is not None:
                if old[1] != manifest.content_hash or old[2] != source.model_dump(mode="json"):
                    raise PackageConflict("same processing identity produced different content")
                return old[0], CommitReceipt.model_validate(old[3]) if old[3] else None
            cursor.execute(
                """SELECT COALESCE(max(version),0)+1 FROM episode_store.versions
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                AND dataset_id=%s AND episode_id=%s""",
                identity.key,
            )
            version = cursor.fetchone()[0]
            cursor.execute(
                """INSERT INTO episode_store.versions
                (organization_id,project_id,region_code,dataset_id,episode_id,data_version_id,
                 version,idempotency_key,input_document,content_hash,status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,'PREPARING')""",
                (
                    *identity.key,
                    source.data_version_id,
                    version,
                    source.idempotency_key,
                    canonical(source.model_dump(mode="json")).decode(),
                    manifest.content_hash,
                ),
            )
            return version, None

    def start_attempt(self, source, attempt_id):
        assert_scope(source.identity)
        with self.transaction() as cursor:
            cursor.execute(
                """INSERT INTO episode_store.attempts
                (organization_id,project_id,region_code,dataset_id,episode_id,
                 data_version_id,attempt_id,status) VALUES (%s,%s,%s,%s,%s,%s,%s,'PREPARING')""",
                (*source.identity.key, source.data_version_id, attempt_id),
            )

    def set_attempt(self, source, attempt_id, status, error_code=None):
        assert_scope(source.identity)
        with self.transaction() as cursor:
            cursor.execute(
                """UPDATE episode_store.attempts SET status=%s,error_code=%s,
                updated_at=clock_timestamp() WHERE organization_id=%s AND project_id=%s
                AND region_code=%s AND data_version_id=%s AND attempt_id=%s
                AND status<>'COMMITTED'""",
                (status, error_code, *source.identity.scope, source.data_version_id, attempt_id),
            )

    def commit(self, receipt: CommitReceipt, attempt_id):
        data, identity = receipt.data, receipt.data.identity
        assert_scope(identity)
        serialized = canonical(receipt.model_dump(mode="json")).decode()
        with self.transaction() as cursor:
            cursor.execute(
                """SELECT content_hash,status,receipt_document FROM episode_store.versions
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                AND data_version_id=%s FOR UPDATE""",
                (*identity.scope, data.data_version_id),
            )
            row = cursor.fetchone()
            if row is None or row[0] != data.content_hash:
                raise PackageConflict("reserved version differs from verified receipt")
            if row[1] == "COMMITTED":
                if row[2] != receipt.model_dump(mode="json"):
                    raise PackageConflict("committed receipt is immutable")
                cursor.execute(
                    """UPDATE episode_store.attempts SET status='COMMITTED',
                    updated_at=clock_timestamp()
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s
                    AND data_version_id=%s AND attempt_id=%s AND status='OBJECTS_VERIFIED'""",
                    (*identity.scope, data.data_version_id, attempt_id),
                )
                return
            cursor.execute(
                """SELECT status FROM episode_store.attempts WHERE organization_id=%s
                AND project_id=%s AND region_code=%s AND data_version_id=%s AND attempt_id=%s""",
                (*identity.scope, data.data_version_id, attempt_id),
            )
            attempt = cursor.fetchone()
            if attempt is None or attempt[0] != "OBJECTS_VERIFIED":
                raise PackageConflict("unverified package cannot become visible")
            cursor.execute(
                """UPDATE episode_store.versions SET status='COMMITTED',receipt_document=%s::jsonb,
                committed_at=clock_timestamp() WHERE organization_id=%s AND project_id=%s
                AND region_code=%s AND data_version_id=%s""",
                (serialized, *identity.scope, data.data_version_id),
            )
            cursor.execute(
                """UPDATE episode_store.bindings SET current_data_version_id=%s
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                AND dataset_id=%s AND episode_id=%s""",
                (data.data_version_id, *identity.key),
            )
            cursor.execute(
                """UPDATE episode_store.attempts SET status='COMMITTED',updated_at=clock_timestamp()
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                AND attempt_id=%s""",
                (*identity.scope, attempt_id),
            )
            cursor.execute(
                """INSERT INTO episode_store.events
                (organization_id,project_id,region_code,dataset_id,episode_id,data_version_id,payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (*identity.key, data.data_version_id, serialized),
            )

    def current(self, identity: EpisodeIdentity) -> CommitReceipt | None:
        assert_scope(identity)
        with self.transaction() as cursor:
            cursor.execute(
                """SELECT v.receipt_document FROM episode_store.bindings b
                JOIN episode_store.versions v ON v.organization_id=b.organization_id
                  AND v.project_id=b.project_id AND v.region_code=b.region_code
                  AND v.data_version_id=b.current_data_version_id
                WHERE b.organization_id=%s AND b.project_id=%s AND b.region_code=%s
                  AND b.dataset_id=%s AND b.episode_id=%s AND b.rollout_id=%s
                  AND v.status='COMMITTED'""",
                (*identity.key, identity.rollout_id),
            )
            row = cursor.fetchone()
            return CommitReceipt.model_validate(row[0]) if row else None
