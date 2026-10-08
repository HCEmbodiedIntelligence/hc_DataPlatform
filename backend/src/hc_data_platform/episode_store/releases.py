"""Atomic business release over real mixed collection/data/annotation references.

Collection ingestion remains independent. Publication copies small metadata,
never primary video/numeric objects, and never creates a Lance version.
"""

from datetime import datetime, timezone
from urllib.parse import quote
from uuid import uuid4

from hc_data_platform.annotation.models import (
    AnnotationHistory,
    AnnotationTag,
    AnnotationTask,
    TagSchemaVersion,
)
from hc_data_platform.annotation.postgres import PostgresAnnotationRepository
from hc_data_platform.annotation.repository import validate_aggregate
from hc_data_platform.annotation.revision_files import revision_document
from hc_data_platform.core.context import current_request_context
from hc_data_platform.dataset_registry.migration_projection import (
    _models,
    _single,
    read_projection,
    write_projection,
)
from hc_data_platform.dataset_registry.models import (
    DatasetPageContentSnapshot,
    DatasetPageEpisodeRecord,
    DatasetPageEpisodeRevision,
    DatasetPageManifestEntry,
    DatasetPageManifestSummary,
    DatasetPageReadyVersion,
    DatasetPageSourceProvenance,
    DatasetPageVersionCapacityFacts,
    DatasetPageVersionContentProjection,
    DatasetPageVersionManifestEntryRecord,
    DatasetPageVersionSchemaDetail,
    DatasetPageVersionSchemaSummary,
)
from hc_data_platform.dataset_registry.publication import release_version_id
from hc_data_platform.dataset_registry.publication_postgres import (
    PostgresDatasetPublicationProjection,
)
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.security.versioning import ResourceVersion

from .collections import collection_scope
from .migration_annotations import source_snapshot
from .models import CollectionSnapshot, DataVersionRef, canonical, digest
from .postgres import PackageConflict
from .release_models import (
    CollectionRelease,
    CollectionReleaseExclusion,
    CollectionReleasePreflight,
    PublishCollectionRelease,
)

MAX_BYTES = 64 * 1024**2


def release_projection(rows, content, version_id, created_at):
    """Retain exact stream bindings; only approved selected Episodes enter a release."""
    old = _single(rows, "dataset_versions", DatasetPageReadyVersion)
    source = content["source_collection"]
    scope = tuple(source[k] for k in ("organization_id", "project_id", "region_code", "dataset_id"))
    for values in rows.values():
        for row in values:
            if (
                tuple(
                    row[k] for k in ("organization_id", "project_id", "region_code", "dataset_id")
                )
                != scope
                or row["version_id"] != "version_" + source["collection_version_id"]
            ):
                raise PackageConflict("release product source scope/version differs")
    if rows["dataset_versions"][0]["version_scope"] != "INTERNAL":
        raise PackageConflict("release source must be an internal collection projection")
    all_episodes = _models(rows, "dataset_version_episodes", DatasetPageEpisodeRecord)
    if {e.episode_id for e in all_episodes} != {
        d["identity"]["episode_id"] for d in source["episodes"]
    }:
        raise PackageConflict("release product membership differs from pinned collection")
    eligible = {m["data"]["identity"]["episode_id"]: m for m in content["members"]}
    episodes = tuple(e for e in all_episodes if e.episode_id in eligible)
    revisions = tuple(
        r
        for r in _models(rows, "dataset_version_episode_revisions", DatasetPageEpisodeRevision)
        if r.episode_id in eligible
    )
    if len(episodes) != len(eligible) or len(revisions) != len(eligible):
        raise PackageConflict("release lacks exact selected Episode revisions")
    refs = {e.episode_id: e.selected_revision for e in episodes}
    for revision in revisions:
        data = eligible[revision.episode_id]["data"]
        if (
            revision.revision_id != refs[revision.episode_id].revision_id
            or revision.content_sha256 != refs[revision.episode_id].content_sha256
        ):
            raise PackageConflict("release selected revision differs from product snapshot")
        for stream in revision.streams:
            if data["storage_format"] == "lerobot_v3":
                if (
                    stream.package_binding is None
                    or stream.package_binding.data.model_dump(mode="json") != data
                ):
                    raise PackageConflict("release package stream differs from fixed data")
            elif stream.package_binding is not None:
                raise PackageConflict("legacy release stream cannot borrow a package binding")

    def moved(value, **changes):
        return type(value).model_validate(
            value.model_dump(mode="python") | {"version_id": version_id, **changes}
        )

    original = _single(
        rows, "dataset_version_content_projections", DatasetPageVersionContentProjection
    )
    if original.content_snapshot != old.content_snapshot or original.manifest != old.manifest:
        raise PackageConflict("release source content/version disagree")
    snapshot_fields = original.content_snapshot.model_dump(mode="python") | {
        "content_snapshot_id": version_id,
        "revision_refs": tuple(
            e.selected_revision for e in sorted(episodes, key=lambda e: e.selected_revision.ordinal)
        ),
    }
    snapshot_fields["content_snapshot_hash"] = canonical_hash(
        {
            k: v
            for k, v in DatasetPageContentSnapshot(**snapshot_fields)
            .model_dump(mode="json")
            .items()
            if k not in {"content_snapshot_id", "content_snapshot_hash"}
        }
    )
    snapshot = DatasetPageContentSnapshot(**snapshot_fields)
    entries = tuple(
        e
        for e in _models(rows, "dataset_version_manifest_entries", DatasetPageManifestEntry)
        if e.episode_id is None or e.episode_id in eligible
    )
    if {e.episode_id for e in entries if e.role == "REVISION"} != set(eligible):
        raise PackageConflict("release manifest lacks selected Episodes")
    content_hash = digest(content)
    manifest = DatasetPageManifestSummary(
        manifest_id="publication-" + content_hash[:32],
        format_version="hc-collection-release/1",
        canonicalization="hc-json/v1",
        sha256=content_hash,
        entry_count=str(len(entries)),
    )
    token = "publication:" + content_hash
    return {
        "version": moved(
            old,
            display_version=content["dataset_version"],
            created_at=created_at,
            published_at=created_at,
            etag=ResourceVersion().etag,
            version_token=token,
            content_snapshot=snapshot,
            manifest=manifest,
            allowed_actions=(),
            approved_review_decision_id=None,
        ),
        "content": moved(
            original, content_snapshot=snapshot, manifest=manifest, operational_revision=token
        ),
        # Manual data review is independent; retain it rather than assign Tag approval to it.
        "episodes": tuple(moved(e, included=True) for e in episodes),
        "revisions": tuple(moved(r) for r in revisions),
        "entries": tuple(
            DatasetPageVersionManifestEntryRecord(
                scope=old.scope, dataset_id=old.dataset_id, version_id=version_id, entry=e
            )
            for e in entries
        ),
        "schema_summary": moved(
            _single(rows, "dataset_version_schema_summaries", DatasetPageVersionSchemaSummary)
        ),
        "schema_detail": moved(
            _single(rows, "dataset_version_schema_details", DatasetPageVersionSchemaDetail)
        ),
        "provenance": tuple(
            moved(p)
            for p in _models(rows, "dataset_version_source_provenance", DatasetPageSourceProvenance)
        ),
        "capacity": moved(
            _single(rows, "dataset_version_capacity_facts", DatasetPageVersionCapacityFacts),
            state="PARTIAL",
            required_physical_bytes=None,
            actual_oss_bytes=None,
            calculated_at=created_at,
            basis_revision=token,
        ),
    }


class CollectionReleases:
    def __init__(self, packages, *, checkpoint=lambda stage: None):
        self.packages = packages
        self.repository = packages.repository
        self.checkpoint = checkpoint

    @staticmethod
    def request_identity(command):
        request = command.model_dump(mode="json", exclude={"expected_content_hash"})
        # Preserve retries of releases created before explicit Episode selection.
        if request["episode_ids"] is None:
            del request["episode_ids"]
        return request

    def _get(self, cursor, scope, name, *, by_version_id=False):
        column = "version_id" if by_version_id else "dataset_version"
        cursor.execute(
            f"""SELECT request_document,document,content_hash,document_sha256,document_size,
                object_key FROM episode_store.collection_releases
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s
              AND {column}=%s""",
            (*scope, name),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        request, document, checksum, object_sha, size, key = row
        content = document["content"]
        if (
            digest(content) != checksum
            or digest(document) != object_sha
            or len(canonical(document)) != size
            or tuple(
                content["source_collection"][k]
                for k in ("organization_id", "project_id", "region_code", "dataset_id")
            )
            != scope
        ):
            raise PackageConflict("release frozen document or scope differs")
        self.packages.committer._verify_object(key, size, object_sha)
        return request, document, self._view(document, object_sha, size)

    @staticmethod
    def _view(document, checksum, size):
        content = document["content"]
        return CollectionRelease(
            dataset_id=content["source_collection"]["dataset_id"],
            dataset_version=content["dataset_version"],
            version_id=document["version_id"],
            collection_version_id=content["source_collection"]["collection_version_id"],
            collection_manifest_sha256=digest(content["source_collection"]),
            content_hash=digest(content),
            document_sha256=checksum,
            document_size=size,
            eligible_episode_ids=tuple(
                m["data"]["identity"]["episode_id"] for m in content["members"]
            ),
            excluded_episodes=tuple(
                CollectionReleaseExclusion.model_validate(e) for e in content["excluded_episodes"]
            ),
            created_at=document["created_at"],
        )

    def get(self, dataset_id, name):
        scope = collection_scope(dataset_id)
        with self.repository.transaction() as cursor:
            result = self._get(cursor, scope, name, by_version_id=True)
            if result is None:
                result = self._get(cursor, scope, name)
            if result is None:
                raise KeyError("collection release is unavailable in exact scope")
            return result[2]

    def _freeze(self, cursor, scope, command):
        cursor.execute(
            """SELECT collection_version_id FROM episode_store.collection_heads
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s
            FOR SHARE""",
            scope,
        )
        head = cursor.fetchone()
        if head is None or head[0] != command.collection_version_id:
            raise PackageConflict("release requires the current pinned working collection")
        collection = self.packages.collections.get(scope[3], collection_version_id=head[0])
        source = CollectionSnapshot.model_validate(
            collection.model_dump(mode="json", include=set(CollectionSnapshot.model_fields))
        )
        episode_ids = {d.identity.episode_id for d in source.episodes}
        if command.episode_ids is not None and set(command.episode_ids) - episode_ids:
            raise ValueError("Episode selection contains an Episode outside this collection")
        if set(command.annotation_tasks) - episode_ids:
            raise ValueError(
                "annotation task selection contains an Episode outside this collection"
            )
        rows = read_projection(cursor, scope, collection.version_id, lock=True)
        members, excluded = [], []
        task_options = {}
        for data in sorted(source.episodes, key=lambda d: d.identity.episode_id):
            cursor.execute(
                """SELECT task_id FROM annotation.annotation_tasks
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s
                  AND rollout_id=%s AND status='APPROVED' AND approved_revision=current_revision
                  AND ((%s::jsonb IS NOT NULL AND data_ref=%s::jsonb)
                    OR (%s::jsonb IS NULL AND data_ref IS NULL AND base_lance_version=%s))
                ORDER BY task_id FOR SHARE""",
                (
                    *scope,
                    data.identity.rollout_id,
                    data.model_dump_json() if isinstance(data, DataVersionRef) else None,
                    data.model_dump_json() if isinstance(data, DataVersionRef) else None,
                    data.model_dump_json() if isinstance(data, DataVersionRef) else None,
                    data.dataset_version if not isinstance(data, DataVersionRef) else None,
                ),
            )
            tasks = [r[0] for r in cursor.fetchall()]
            task_options[data.identity.episode_id] = tuple(tasks)
            if (
                command.episode_ids is not None
                and data.identity.episode_id not in command.episode_ids
            ):
                excluded.append({"episode_id": data.identity.episode_id, "reason": "NOT_SELECTED"})
                continue
            selected = command.annotation_tasks.get(data.identity.episode_id)
            if selected is not None:
                if selected not in tasks:
                    raise PackageConflict("selected task is not currently approved for exact data")
                tasks = [selected]
            if len(tasks) != 1:
                excluded.append(
                    {
                        "episode_id": data.identity.episode_id,
                        "reason": "ANNOTATION_NOT_APPROVED"
                        if not tasks
                        else "ANNOTATION_TASK_SELECTION_REQUIRED",
                    }
                )
                continue
            frozen = source_snapshot(cursor, scope, tasks[0])
            aggregate = PostgresAnnotationRepository(self.repository.connections)._load_related(
                AnnotationTask.model_validate(
                    {k: v for k, v in frozen["task"].items() if k in AnnotationTask.model_fields}
                )
            )
            validate_aggregate(aggregate)
            task = aggregate.task
            if any(
                set(tag) - set(AnnotationTag.model_fields)
                for r in frozen["history"]["annotation_revisions"]
                for tag in r["tags"]
            ):
                raise PackageConflict("release cannot silently drop unknown Tag fields")
            review = next(
                (r for r in aggregate.reviews if r.review_id == task.approved_review_id), None
            )
            submission = next(
                (
                    s
                    for s in aggregate.submissions
                    if review and s.submission_id == review.submission_id
                ),
                None,
            )
            if (
                review is None
                or submission is None
                or review.decision.value != "APPROVE"
                or review.revision != task.current_revision
                or submission.revision != review.revision
                or submission.submission_id != task.current_submission_id
                or review.data_ref != task.data_ref
                or submission.data_ref != task.data_ref
                or submission.revision_content_hash
                != aggregate.revisions[review.revision].content_hash
            ):
                raise PackageConflict("release approval differs from immutable submission/revision")
            schema = TagSchemaVersion.model_validate(frozen["schema"])
            annotation = revision_document(
                AnnotationHistory(
                    task=task,
                    revisions=aggregate.revisions,
                    submissions=aggregate.submissions,
                    reviews=aggregate.reviews,
                ),
                task.current_revision,
                schema,
                scope[0],
            )
            if (
                submission.tag_schema_hash != schema.content_hash
                or task.base_step_count != data.frame_count
            ):
                raise PackageConflict("release schema/frame count differs from approved binding")
            if isinstance(data, DataVersionRef):
                package = self.packages.files.open(data.identity, data.data_version_id)
                if package.receipt.data != data:
                    raise PackageConflict("release package differs from committed reference")
                for entry in package.manifest.files:
                    with package.file(entry.path):
                        pass
            else:
                if self.packages.catalog is None:
                    raise PackageConflict("legacy release requires a configured pinned reader")
                self.packages.catalog._legacy_lineage(data)
            members.append(
                {
                    "data": data.model_dump(mode="json"),
                    "annotation": annotation,
                    "annotation_sha256": digest(annotation),
                    "submission": submission.model_dump(mode="json"),
                    "review": review.model_dump(mode="json"),
                }
            )
        content = {
            "format": "hc-collection-release-content/1",
            "dataset_version": command.dataset_version,
            "source_collection": source.model_dump(mode="json"),
            "source_projection_sha256": digest(rows),
            "members": members,
            "excluded_episodes": excluded,
        }
        if len(canonical(content)) > MAX_BYTES:
            raise ValueError("release frozen content exceeds explicit byte limit")
        return content, rows, task_options

    def preflight(self, dataset_id, command):
        command = PublishCollectionRelease.model_validate(command.model_dump(mode="json"))
        scope = collection_scope(dataset_id)
        with self.repository.transaction() as cursor:
            content, _, task_options = self._freeze(cursor, scope, command)
        return CollectionReleasePreflight(
            dataset_id=dataset_id,
            dataset_version=command.dataset_version,
            collection_version_id=command.collection_version_id,
            collection_manifest_sha256=digest(content["source_collection"]),
            content_hash=digest(content),
            annotation_task_options=task_options,
            eligible_episode_ids=tuple(
                m["data"]["identity"]["episode_id"] for m in content["members"]
            ),
            excluded_episodes=tuple(
                CollectionReleaseExclusion.model_validate(e) for e in content["excluded_episodes"]
            ),
        )

    def publish(self, dataset_id, command):
        command = PublishCollectionRelease.model_validate(command.model_dump(mode="json"))
        scope = collection_scope(dataset_id)
        context = current_request_context()
        if not context.subject_id:
            raise PermissionError("collection publication requires an attributed actor")
        if command.expected_content_hash is None:
            raise ValueError("publication requires the exact preflight content hash")
        with self.packages.collections.writer_lock(scope), self.repository.transaction() as cursor:
            existing = self._get(cursor, scope, command.dataset_version)
            if existing is not None:
                if (
                    existing[0] != self.request_identity(command)
                    or existing[2].content_hash != command.expected_content_hash
                ):
                    raise PackageConflict(
                        "business release name is already frozen for different content"
                    )
                return existing[2]
            content, rows, _ = self._freeze(cursor, scope, command)
            if not content["members"] or digest(content) != command.expected_content_hash:
                raise PackageConflict(
                    "release has no approved Episodes or preflight content changed"
                )
            created = datetime.now(timezone.utc)
            version_id = release_version_id(command.dataset_version)
            cursor.execute(
                """SELECT 1 FROM dataset_registry.dataset_versions WHERE organization_id=%s
                AND project_id=%s AND region_code=%s AND dataset_id=%s AND version_id=%s""",
                (*scope, version_id),
            )
            if cursor.fetchone() is not None:
                raise PackageConflict("release identity is already used by another publication")
            document = {
                "format": "hc-collection-release/1",
                "content": content,
                "content_hash": digest(content),
                "version_id": version_id,
                "created_at": created.isoformat(),
                "created_by": context.subject_id,
            }
            body = canonical(document)
            if len(body) > MAX_BYTES:
                raise ValueError("release document exceeds explicit byte limit")
            key = (
                "dataset-releases/"
                + "/".join(quote(s, safe="") for s in scope)
                + "/"
                + digest(document)
                + "/manifest.json"
            )
            self.packages.committer._put_json(key, document)
            self.checkpoint("release_object_verified")
            projection = release_projection(rows, content, version_id, created)
            write_projection(cursor, projection)
            cursor.execute(
                """UPDATE dataset_registry.dataset_versions SET version_scope='DATASET_RELEASE'
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND dataset_id=%s AND version_id=%s""",
                (*scope, version_id),
            )
            self.checkpoint("release_projection_written")
            cursor.execute(
                """INSERT INTO episode_store.collection_releases
                (organization_id,project_id,region_code,dataset_id,dataset_version,version_id,
                 collection_version_id,request_document,document,content_hash,document_sha256,
                 document_size,object_key,actor_id,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s)""",
                (
                    *scope,
                    command.dataset_version,
                    version_id,
                    command.collection_version_id,
                    canonical(self.request_identity(command)).decode(),
                    body.decode(),
                    digest(content),
                    digest(document),
                    len(body),
                    key,
                    context.subject_id,
                    created,
                ),
            )
            PostgresDatasetPublicationProjection(cursor).promote_current_release(
                organization_id=scope[0],
                project_id=scope[1],
                region_code=scope[2],
                dataset_id=scope[3],
                release_version_id=version_id,
                display_version=command.dataset_version,
                version_kind=projection["version"].kind,
                published_at=created,
                manifest_sha256=digest(content),
            )
            cursor.execute(
                """INSERT INTO core.audit_events
                (audit_id,organization_id,project_id,region_code,actor_id,action,resource_type,
                 resource_id,request_id,details,occurred_at)
                VALUES (%s,%s,%s,%s,%s,'dataset.collection_released','dataset_version',%s,
                  NULLIF(current_setting('app.request_id',true),''),%s::jsonb,%s)""",
                (
                    uuid4(),
                    *scope[:3],
                    context.subject_id,
                    version_id,
                    canonical(
                        {
                            "dataset_id": dataset_id,
                            "content_hash": digest(content),
                            "document_sha256": digest(document),
                            "collection_version_id": command.collection_version_id,
                        }
                    ).decode(),
                    created,
                ),
            )
            self.checkpoint("release_before_commit")
            return self._view(document, digest(document), len(body))
