"""Atomic, resumable migration publication and guarded pointer-only rollback.

Operator service, deliberately separate from ordinary processing. Frozen plans
and complete conversion spools are required; no caller can mark unchecked bytes
READY. Old collections, packages, Lance and product/review history are retained.
"""

from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from hc_data_platform.core.context import current_request_context
from hc_data_platform.dataset_registry.migration_projection import (
    projection_hash,
    read_projection,
    transform_projection,
    write_projection,
)
from hc_data_platform.lance_catalog.adapters import PostgresAdvisoryDatasetLock
from hc_data_platform.lance_catalog.service import compute_fragment_hash

from .catalog_models import CollectionVersion
from .commit import EpisodePackageCommitter
from .errors import PackageConflict
from .files import PackageFiles, VerifiedFileCache
from .migration_conversion import (
    MigrationEpisodePlan,
    legacy_steps,
    pinned_legacy,
    restored_steps,
    verify_conversion,
)
from .models import CollectionSnapshot, canonical, digest
from .previews import EpisodePreviews
from .schemas import EpisodeSchemas
from .scope import assert_scope, collection_scope
from .validation import document, validate_package


class MigrationBatches:
    def __init__(self, collections, *, checkpoint=lambda stage: None):
        if collections.legacy is None or collections.legacy_entries is None:
            raise ValueError("migration batches require the scoped legacy catalog")
        self.collections = collections
        self.packages, self.storage = collections.packages, collections.storage
        self.legacy = collections.legacy
        self.checkpoint = checkpoint

    @staticmethod
    def _scope(dataset_id):
        scope = collection_scope(dataset_id)
        if not current_request_context().service_identity:
            raise PermissionError("migration publication requires a scoped operator identity")
        return scope

    @contextmanager
    def _locks(self, scope, plans, *, lock_episodes=True):
        # Same advisory locks as legacy, collection and Episode writers. Keep
        # ordering fixed; normal commit releases its Episode lock before publish.
        with ExitStack() as stack:
            stack.enter_context(
                PostgresAdvisoryDatasetLock(self.packages.connections).acquire(scope[1], scope[3])
            )
            stack.enter_context(self.collections.writer_lock(scope))
            if lock_episodes:
                for plan in sorted(plans, key=lambda p: p.legacy.identity.key):
                    stack.enter_context(self.packages.writer_lock(plan.legacy.identity))
            yield

    def _previews(self, members, *, prepare):
        """Use normal official SDK processing; publish only rechecks existing bytes."""
        receipts = []
        with TemporaryDirectory(prefix="hc-migration-previews-") as directory:
            cache = VerifiedFileCache(Path(directory) / "cache")
            try:
                previews = EpisodePreviews(
                    PackageFiles(self.packages, self.storage, cache),
                    EpisodePackageCommitter(self.packages, self.storage),
                    Path(directory) / "previews",
                )
                for member in members:
                    data = member["data"]
                    if prepare:
                        values = previews.prepare_all(data)
                    else:
                        package = previews.files.open(data.identity, data.data_version_id)
                        values = []
                        for feature, definition in member["info"]["features"].items():
                            if definition["dtype"] != "video":
                                continue
                            row = previews._row(data, feature)
                            if row is None or row[0] != "READY":
                                raise PackageConflict("migration browser preview is not ready")
                            source_hash = previews._source(package, feature)[3]
                            values.append(previews._verified(package, feature, row, source_hash))
                    receipts.extend(v.model_dump(mode="json") for v in values)
            finally:
                cache.close()
        return receipts

    def get(self, dataset_id, batch_id):
        scope = self._scope(dataset_id)
        with self.packages.transaction() as cursor:
            cursor.execute(
                """SELECT plan,status,publication,rollback FROM episode_store.migration_batches
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                    AND dataset_id=%s
                  AND batch_id=%s""",
                (*scope, batch_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError("migration batch not found in operator scope")
            if row[0]["batch_id"] != batch_id or tuple(row[0]["scope"]) != scope:
                raise PackageConflict("stored migration batch scope differs")
            return {
                "batch_id": batch_id,
                "plan": row[0],
                "status": row[1],
                "publication": row[2],
                "rollback": row[3],
            }

    def _heads(self, scope):
        current = self.collections.current(scope[3])
        legacy = self.legacy.current_version(scope[3], project_id=scope[1])
        return (
            None if current is None else current.model_dump(mode="json"),
            None if legacy is None else legacy.model_dump(mode="json"),
        )

    def _pointers(self, cursor, plans, *, lock=False):
        result = {}
        for plan in plans:
            identity = plan.legacy.identity
            cursor.execute(
                """SELECT rollout_id,current_data_version_id FROM episode_store.bindings
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND dataset_id=%s AND episode_id=%s"""
                + (" FOR UPDATE" if lock else ""),
                identity.key,
            )
            row = cursor.fetchone()
            if row is None or row[0] != identity.rollout_id:
                raise PackageConflict("migration Episode binding is absent or changed")
            result[identity.episode_id] = row[1]
        return result

    def _verify(self, plans, roots):
        verified = []
        committer = EpisodePackageCommitter(self.packages, self.storage)
        if set(roots) != {p.sha256 for p in plans}:
            raise PackageConflict("migration requires one frozen conversion spool per member")
        for plan in plans:
            assert_scope(plan.legacy.identity)
            identity = plan.legacy.identity
            with self.packages.transaction() as cursor:
                cursor.execute(
                    """SELECT to_jsonb(r)
                    FROM dataset_registry.dataset_version_episode_revisions r
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s
                      AND dataset_id=%s AND episode_id=%s AND version_id=%s""",
                    (*identity.key, "version_lance_" + str(plan.legacy.dataset_version)),
                )
                if sorted(digest(row[0]) for row in cursor.fetchall()) != sorted(
                    plan.revision_row_sha256
                ):
                    raise PackageConflict("migration source revision differs from the inventory")
            root = Path(roots[plan.sha256])
            report = verify_conversion(root, plan, self.legacy)
            if document(root, "hc/migration.json") != report:
                raise PackageConflict(
                    "migration equivalence report differs from source verification"
                )
            manifest = validate_package(root, plan.source)
            steps = iter(restored_steps(root, plan.legacy))
            first = last = next(steps)
            for step in steps:
                last = step
            receipt = self.packages.get(plan.legacy.identity, plan.source.data_version_id)
            if (
                receipt.data.content_hash != manifest.content_hash
                or receipt.data.frame_count != plan.legacy.frame_count
            ):
                raise PackageConflict("migration spool differs from its complete committed package")
            for entry in manifest.files:
                committer._verify_object(
                    f"{receipt.data.package_prefix}/{entry.path}", entry.size, entry.sha256
                )
            # These methods never overwrite an existing object; however a batch
            # verifier must not create a missing manifest/commit either.
            for relative, value in (
                ("hc/manifest.json", manifest.model_dump(mode="json")),
                ("hc/commit.json", receipt.model_dump(mode="json")),
            ):
                body = canonical(value)
                committer._verify_object(
                    f"{receipt.data.package_prefix}/{relative}", len(body), digest(value)
                )
            verified.append(
                {
                    "plan": plan,
                    "data": receipt.data,
                    "receipt": receipt,
                    "info": document(root, "meta/info.json"),
                    "first_timestamp_ns": first.timestamp_ns,
                    "last_timestamp_ns": last.timestamp_ns,
                    "size_bytes": sum(e.size for e in manifest.files),
                }
            )
        self.checkpoint("batch_packages_verified")
        return verified

    def prepare(self, dataset_id, batch_key, plans, roots):
        scope = self._scope(dataset_id)
        if not isinstance(batch_key, str) or not 1 <= len(batch_key) <= 128:
            raise ValueError("batch key must contain 1..128 characters")
        plans = tuple(MigrationEpisodePlan.model_validate(p.model_dump(mode="json")) for p in plans)
        if not 1 <= len(plans) <= 1000:
            raise ValueError("migration batch must contain 1..1000 Episodes")
        plans = tuple(sorted(plans, key=lambda p: p.legacy.identity.episode_id))
        if len({p.legacy.identity.episode_id for p in plans}) != len(plans):
            raise PackageConflict("migration batch repeats an Episode")
        if any((*p.legacy.identity.scope, p.legacy.identity.dataset_id) != scope for p in plans):
            raise PermissionError("migration batch members differ from operator scope")
        batch_id = "mb_" + digest({"scope": scope, "key": batch_key})
        with self._locks(scope, plans, lock_episodes=False):
            try:
                previous = self.get(dataset_id, batch_id)
            except KeyError:
                previous = None
            if previous is not None:
                if [m["plan"] for m in previous["plan"]["members"]] != [
                    p.model_dump(mode="json") for p in plans
                ]:
                    raise PackageConflict("batch key already binds a different frozen plan")
                return previous
            members = self._verify(plans, roots)
            previews = self._previews(members, prepare=True)
            for member in plans:
                EpisodeSchemas(self.packages).register_schema(member.schema_snapshot)
            self.checkpoint("batch_previews_prepared")
            head, legacy = self._heads(scope)
            if head:
                active = CollectionVersion.model_validate(head)
                entries = list(active.episodes)
                source_version = active.version_id
                if legacy and legacy["version"] >= active.version:
                    raise PackageConflict("legacy writer advanced after collection switch")
            else:
                legacy_version = pinned_legacy(self.legacy, plans[0].legacy)[0]
                if legacy is None or legacy != legacy_version.model_dump(mode="json"):
                    raise PackageConflict("migration must replace the active legacy version")
                entries = list(self.collections.legacy_entries(legacy_version))
                source_version = "version_lance_" + str(legacy_version.version)
            replacements = {m["plan"].legacy.identity.episode_id: m for m in members}
            for member in members:
                if member["plan"].legacy not in entries:
                    raise PackageConflict("migration source is not the current collection binding")
                if self.collections.publication(member["data"]) is not None:
                    raise PackageConflict("migration target already has a publication")
            schema = plans[0].schema_snapshot
            if any(p.schema_snapshot != schema for p in plans):
                raise PackageConflict("migration batch schemas differ")
            baseline_schema = head or legacy
            if (
                baseline_schema["schema_fingerprint"] != schema.fingerprint
                or baseline_schema["frequency_hz"] != schema.frequency_hz
            ):
                raise PackageConflict("migration schema differs from the active collection")
            entries = [
                replacements[e.identity.episode_id]["data"]
                if e.identity.episode_id in replacements
                else e
                for e in entries
            ]
            with self.packages.transaction() as cursor:
                baseline = self._pointers(cursor, plans)
                if any(value is not None for value in baseline.values()):
                    raise PackageConflict(
                        "legacy migration cannot replace an active package pointer"
                    )
                cursor.execute(
                    """SELECT COALESCE(max(version),0) FROM episode_store.collections
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s
                    AND dataset_id=%s""",
                    scope,
                )
                number = max(cursor.fetchone()[0], legacy["version"] if legacy else 0) + 1
                rows = read_projection(cursor, scope, source_version)
            fields = dict(
                zip(
                    ("organization_id", "project_id", "region_code", "dataset_id"),
                    scope,
                    strict=True,
                )
            ) | {
                "version": number,
                "schema_snapshot_id": schema.schema_snapshot_id,
                "schema_fingerprint": schema.fingerprint,
                "frequency_hz": schema.frequency_hz,
                "previous_collection_version_id": None
                if head is None
                else head["collection_version_id"],
                "episodes": [e.model_dump(mode="json") for e in entries],
            }
            snapshot = CollectionSnapshot(collection_version_id="cv_" + digest(fields), **fields)
            collection = CollectionVersion(
                **snapshot.model_dump(mode="python"),
                created_at=datetime.now(timezone.utc),
                manifest_sha256=snapshot.content_hash,
            )
            projection = transform_projection(rows, collection, members, legacy=self.legacy)
            plan = {
                "format": "hc-episode-migration-batch/1",
                "batch_id": batch_id,
                "scope": scope,
                "members": [
                    {
                        "plan": m["plan"].model_dump(mode="json"),
                        "receipt": m["receipt"].model_dump(mode="json"),
                    }
                    for m in members
                ],
                "previous_collection": head,
                "legacy": legacy,
                "previous_pointers": baseline,
                "source_version_id": source_version,
                "source_projection_sha256": digest(rows),
                "collection": collection.model_dump(mode="json"),
                "projection_sha256": projection_hash(projection),
                "previews": previews,
            }
            with self.packages.transaction() as cursor:
                cursor.execute(
                    """INSERT INTO episode_store.migration_batches
                    (organization_id,project_id,region_code,dataset_id,batch_id,plan)
                    VALUES (%s,%s,%s,%s,%s,%s::jsonb)""",
                    (*scope, batch_id, canonical(plan).decode()),
                )
            self.checkpoint("batch_prepared")
            return self.get(dataset_id, batch_id)

    def publish(self, dataset_id, batch_id, roots):
        scope = self._scope(dataset_id)
        batch = self.get(dataset_id, batch_id)
        plans = tuple(
            MigrationEpisodePlan.model_validate(m["plan"]) for m in batch["plan"]["members"]
        )
        with self._locks(scope, plans):
            batch = self.get(dataset_id, batch_id)
            if batch["status"] != "PREPARED":
                # A lost successful response or an explicit rollback is terminal.
                # An ordinary retry can never reactivate an archived batch.
                return batch
            plan = batch["plan"]
            members = self._verify(plans, roots)
            if self._previews(members, prepare=False) != plan["previews"]:
                raise PackageConflict("migration browser previews differ from the frozen batch")
            if [m["receipt"].model_dump(mode="json") for m in members] != [
                m["receipt"] for m in plan["members"]
            ]:
                raise PackageConflict("migration committed receipts differ from frozen batch")
            if self._heads(scope) != (plan["previous_collection"], plan["legacy"]):
                raise PackageConflict("migration baseline head changed; prepare a new batch")
            collection = CollectionVersion.model_validate(plan["collection"])
            with self.packages.transaction() as cursor:
                cursor.execute(
                    """SELECT COALESCE(max(version),0) FROM episode_store.collections
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s
                      AND dataset_id=%s""",
                    scope,
                )
                if cursor.fetchone()[0] >= collection.version:
                    raise PackageConflict(
                        "migration version allocation changed; prepare a new batch"
                    )
            snapshot = CollectionSnapshot.model_validate(
                {
                    k: v
                    for k, v in collection.model_dump(mode="json").items()
                    if k in CollectionSnapshot.model_fields
                }
            )
            EpisodePackageCommitter(self.packages, self.storage)._put_json(
                snapshot.manifest_key, snapshot.model_dump(mode="json")
            )
            self.checkpoint("batch_collection_object_verified")
            with self.packages.transaction() as cursor:
                rows = read_projection(cursor, scope, plan["source_version_id"], lock=True)
                if (
                    digest(rows) != plan["source_projection_sha256"]
                    or self._pointers(cursor, plans, lock=True) != plan["previous_pointers"]
                ):
                    raise PackageConflict("migration product or Episode baseline changed")
                projection = transform_projection(rows, collection, members, legacy=self.legacy)
                if projection_hash(projection) != plan["projection_sha256"]:
                    raise PackageConflict("migration product projection differs from frozen plan")
                cursor.execute(
                    """INSERT INTO episode_store.collections
                    (organization_id,project_id,region_code,dataset_id,collection_version_id,version,content_hash,document,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                    (
                        *scope,
                        snapshot.collection_version_id,
                        snapshot.version,
                        snapshot.content_hash,
                        canonical(snapshot.model_dump(mode="json")).decode(),
                        collection.created_at,
                    ),
                )
                cursor.execute(
                    """INSERT INTO episode_store.collection_heads
                    (organization_id,project_id,region_code,dataset_id,collection_version_id)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (organization_id,project_id,region_code,dataset_id)
                    DO UPDATE SET collection_version_id=EXCLUDED.collection_version_id""",
                    (*scope, snapshot.collection_version_id),
                )
                for member in members:
                    data = member["data"]
                    cursor.execute(
                        """UPDATE episode_store.bindings SET current_data_version_id=%s
                        WHERE organization_id=%s AND project_id=%s AND region_code=%s
                    AND dataset_id=%s AND episode_id=%s""",
                        (data.data_version_id, *data.identity.key),
                    )
                    cursor.execute(
                        """INSERT INTO episode_store.collection_publications
                        (organization_id,project_id,region_code,dataset_id,collection_version_id,data_version_id)
                        VALUES (%s,%s,%s,%s,%s,%s)""",
                        (*scope, snapshot.collection_version_id, data.data_version_id),
                    )
                self.checkpoint("batch_pointers_written")
                write_projection(cursor, projection)
                self.checkpoint("batch_product_written")
                receipt = {
                    "batch_id": batch_id,
                    "collection": collection.model_dump(mode="json"),
                    "projection_sha256": plan["projection_sha256"],
                    "member_count": len(members),
                    "annotation_history_rewritten": False,
                    "review_automatically_transferred": False,
                }
                cursor.execute(
                    """UPDATE episode_store.migration_batches SET status='PUBLISHED',
                    publication=%s::jsonb
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s
                    AND dataset_id=%s 
                    AND batch_id=%s AND status='PREPARED'""",
                    (canonical(receipt).decode(), *scope, batch_id),
                )
            self.checkpoint("batch_database_committed")
            return self.get(dataset_id, batch_id)

    def rollback(self, dataset_id, batch_id):
        scope = self._scope(dataset_id)
        batch = self.get(dataset_id, batch_id)
        plans = tuple(
            MigrationEpisodePlan.model_validate(m["plan"]) for m in batch["plan"]["members"]
        )
        with self._locks(scope, plans):
            batch = self.get(dataset_id, batch_id)
            if batch["status"] == "ROLLED_BACK":
                return batch
            if batch["status"] != "PUBLISHED":
                raise PackageConflict("only a published migration batch can roll back")
            plan = batch["plan"]
            if self._heads(scope) != (plan["collection"], plan["legacy"]):
                raise PackageConflict("migration head changed; rollback would hide later work")
            for member in plans:
                pinned_legacy(self.legacy, member.legacy)
                if (
                    compute_fragment_hash(legacy_steps(self.legacy, member.legacy))
                    != member.legacy.content_hash
                ):
                    raise PackageConflict(
                        "rollback legacy physical rows differ from the frozen reference"
                    )
            expected = {p.legacy.identity.episode_id: p.source.data_version_id for p in plans}
            with self.packages.transaction() as cursor:
                if self._pointers(cursor, plans, lock=True) != expected:
                    raise PackageConflict("migration Episode pointer changed; rollback refused")
                if (
                    digest(read_projection(cursor, scope, plan["source_version_id"], lock=True))
                    != plan["source_projection_sha256"]
                ):
                    raise PackageConflict("migration source projection changed; rollback refused")
                previous = plan["previous_collection"]
                if previous is None:
                    cursor.execute(
                        """DELETE FROM episode_store.collection_heads
                        WHERE organization_id=%s AND project_id=%s AND region_code=%s
                    AND dataset_id=%s""",
                        scope,
                    )
                else:
                    cursor.execute(
                        """UPDATE episode_store.collection_heads SET collection_version_id=%s
                        WHERE organization_id=%s AND project_id=%s AND region_code=%s
                    AND dataset_id=%s""",
                        (previous["collection_version_id"], *scope),
                    )
                for member in plans:
                    ident = member.legacy.identity
                    cursor.execute(
                        """UPDATE episode_store.bindings SET current_data_version_id=%s
                        WHERE organization_id=%s AND project_id=%s AND region_code=%s
                    AND dataset_id=%s AND episode_id=%s""",
                        (plan["previous_pointers"][ident.episode_id], *ident.key),
                    )
                self.checkpoint("batch_rollback_pointers_written")
                receipt = {
                    "batch_id": batch_id,
                    "restored_collection": previous,
                    "restored_pointers": plan["previous_pointers"],
                    "packages_retained": True,
                    "legacy_retained": True,
                    "product_history_retained": True,
                }
                cursor.execute(
                    """UPDATE episode_store.migration_batches SET status='ROLLED_BACK',
                    rollback=%s::jsonb
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s
                    AND dataset_id=%s 
                    AND batch_id=%s AND status='PUBLISHED'""",
                    (canonical(receipt).decode(), *scope, batch_id),
                )
            self.checkpoint("batch_rollback_database_committed")
            return self.get(dataset_id, batch_id)
