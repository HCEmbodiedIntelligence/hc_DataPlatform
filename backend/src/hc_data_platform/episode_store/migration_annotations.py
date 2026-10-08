"""Frozen historical annotation equivalence, independent editable task and JSON.

Operator writes only. Old tasks, revisions, submissions and reviews remain bound
to their original Lance data. A new draft pins the actual Episode receipt; old
approval is recorded as history and never becomes a new reviewer decision.
"""

import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import quote

from psycopg import sql

from hc_data_platform.annotation.models import (
    AnnotationOperation,
    AnnotationRevision,
    AnnotationStatus,
    AnnotationTag,
    AnnotationTask,
    AnnotationTaskCreationSource,
    OperationKind,
    RevisionOrigin,
    TagSchemaVersion,
)
from hc_data_platform.annotation.ports import AnnotationAggregate
from hc_data_platform.annotation.postgres import PostgresAnnotationRepository
from hc_data_platform.annotation.repository import validate_aggregate
from hc_data_platform.annotation.service import annotation_etag, normalize_operations
from hc_data_platform.annotation.validation import (
    default_flat_schema,
    revision_content_hash,
    validate_tag_revision,
)
from hc_data_platform.core.context import current_request_context
from hc_data_platform.lance_catalog.service import compute_fragment_hash

from .commit import EpisodePackageCommitter
from .errors import PackageConflict
from .migration_conversion import legacy_steps, pinned_legacy
from .models import DataVersionRef, LegacyEpisodeRef, canonical, digest
from .scope import assert_scope

FORMAT = "hc-annotation-migration-plan/1"
MAX_BYTES = 16 * 1024**2
HISTORY_TABLES = (
    "annotation_revisions",
    "annotation_operations",
    "annotation_submissions",
    "annotation_reviews",
    "annotation_mutations",
    "annotation_submission_mutations",
)


def source_snapshot(cursor, scope, task_id):
    """Task lock also fences ordinary saves/submissions/reviews while freezing."""
    cursor.execute(
        """SELECT to_jsonb(t) FROM annotation.annotation_tasks t
        WHERE organization_id=%s AND project_id=%s AND region_code=%s
          AND dataset_id=%s AND task_id=%s FOR SHARE""",
        (*scope, task_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise KeyError("original annotation task not found in exact migration scope")
    task, history = row[0], {}
    for table in HISTORY_TABLES:
        cursor.execute(
            sql.SQL("SELECT to_jsonb(r) FROM annotation.{} r WHERE task_id=%s LIMIT 100001").format(
                sql.Identifier(table)
            ),
            (task_id,),
        )
        values = [r[0] for r in cursor.fetchall()]
        if len(values) > 100000:
            raise ValueError("annotation migration history exceeds explicit row limit")
        history[table] = sorted(values, key=canonical)
    if task["tag_schema_id"] == "default-flat" and task["tag_schema_version"] == 1:
        schema = default_flat_schema(scope[1]).model_dump(mode="json")
    else:
        cursor.execute(
            """SELECT to_jsonb(s) FROM annotation.tag_schema_versions s
            WHERE organization_id=%s AND project_id=%s AND schema_id=%s AND version=%s
            FOR SHARE""",
            (*scope[:2], task["tag_schema_id"], task["tag_schema_version"]),
        )
        row = cursor.fetchone()
        if row is None:
            raise PackageConflict("original pinned Tag Schema is absent")
        schema = row[0]
    snapshot = {"task": task, "history": history, "schema": schema}
    if len(canonical(snapshot)) > MAX_BYTES:
        raise ValueError("annotation migration snapshot exceeds explicit byte limit")
    return snapshot


def selected_content(snapshot, revision_number):
    task = snapshot["task"]
    if (
        task["data_ref"] is not None
        or type(revision_number) is not int
        or not 0 <= revision_number <= task["current_revision"]
    ):
        raise PackageConflict("migration requires an explicit existing legacy revision")
    revisions, cumulative_operations = [], []
    for row in sorted(snapshot["history"]["annotation_revisions"], key=lambda r: r["revision"]):
        if row["revision"] != len(revisions) or row["task_id"] != task["task_id"]:
            raise PackageConflict("original annotation revision history is incomplete")
        operations = tuple(
            AnnotationOperation.model_validate(
                {k: r[k] for k in AnnotationOperation.model_fields if k in r}
            )
            for r in sorted(
                (
                    r
                    for r in snapshot["history"]["annotation_operations"]
                    if r["revision"] == row["revision"]
                ),
                key=lambda r: r["operation_sequence"],
            )
        )
        if any(set(tag) - set(AnnotationTag.model_fields) for tag in row["tags"]):
            raise PackageConflict("original Tag has fields outside the supported complete contract")
        value = {k: row[k] for k in AnnotationRevision.model_fields if k in row}
        revised = AnnotationRevision.model_validate({**value, "operations": operations})
        cumulative_operations.extend(revised.operations)
        if (
            revised.base_lance_version != task["base_lance_version"]
            or revised.data_ref is not None
            or revised.tag_schema_id != task["tag_schema_id"]
            or revised.tag_schema_version != task["tag_schema_version"]
            or revised.content_hash
            != revision_content_hash(
                base_lance_version=revised.base_lance_version,
                tag_schema_id=revised.tag_schema_id,
                tag_schema_version=revised.tag_schema_version,
                tags=revised.tags,
                operations=cumulative_operations,
            )
        ):
            raise PackageConflict("original annotation binding or immutable content hash differs")
        revisions.append(revised)
    if len(revisions) != task["current_revision"] + 1:
        raise PackageConflict("original current revision is absent")
    selected = revisions[revision_number]
    schema = TagSchemaVersion.model_validate(snapshot["schema"])
    validate_tag_revision(
        schema=schema,
        base_step_count=task["base_step_count"],
        tags=selected.tags,
        operations=selected.operations,
    )
    submissions = {r["submission_id"]: r for r in snapshot["history"]["annotation_submissions"]}
    for submitted in submissions.values():
        number = submitted["revision"]
        if (
            not 0 <= number < len(revisions)
            or submitted["data_ref"] is not None
            or submitted["base_lance_version"] != task["base_lance_version"]
            or submitted["tag_schema_id"] != schema.schema_id
            or submitted["tag_schema_version"] != schema.version
            or submitted["tag_schema_hash"] != schema.content_hash
            or submitted["revision_content_hash"] != revisions[number].content_hash
        ):
            raise PackageConflict("original submission differs from its immutable revision/schema")
    for review in snapshot["history"]["annotation_reviews"]:
        submitted = submissions.get(review["submission_id"])
        if (
            review["data_ref"] is not None
            or submitted is None
            or review["revision"] != submitted["revision"]
        ):
            raise PackageConflict("original review differs from its fixed submission")
    return selected, normalize_operations(revisions, revision_number)


class MigrationAnnotations:
    def __init__(self, batches, *, checkpoint=lambda stage: None):
        self.batches = batches
        self.packages, self.storage = batches.packages, batches.storage
        self.checkpoint = checkpoint

    def get(self, dataset_id, mapping_id, *, cursor=None, lock=False):
        scope = self.batches._scope(dataset_id)
        if cursor is None:
            with self.packages.transaction() as current:
                return self.get(dataset_id, mapping_id, cursor=current, lock=lock)
        cursor.execute(
            """SELECT plan,status,result,document_key,document_sha256,document_size
            FROM episode_store.annotation_migrations
            WHERE organization_id=%s AND project_id=%s AND region_code=%s
              AND dataset_id=%s AND mapping_id=%s"""
            + (" FOR UPDATE" if lock else ""),
            (*scope, mapping_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise KeyError("annotation migration not found in operator scope")
        plan = row[0]
        if plan["mapping_id"] != mapping_id or tuple(plan["scope"]) != scope:
            raise PackageConflict("annotation migration persisted scope differs")
        return {
            "mapping_id": mapping_id,
            "plan": plan,
            "status": row[1],
            "result": row[2],
            "document_key": row[3],
            "document_sha256": row[4],
            "document_size": row[5],
        }

    def _equivalence(self, batch, snapshot, source_ref, target_data):
        assert_scope(source_ref.identity)
        assert_scope(target_data.identity)
        task = snapshot["task"]
        members = [
            m
            for m in batch["plan"]["members"]
            if m["receipt"]["data"] == target_data.model_dump(mode="json")
        ]
        if (
            batch["status"] not in {"PUBLISHED", "ROLLED_BACK"}
            or len(members) != 1
            or task["rollout_id"] != source_ref.identity.rollout_id
            or task["base_lance_version"] != source_ref.dataset_version
            or task["base_step_count"] != source_ref.frame_count
            or source_ref.identity != target_data.identity
            or source_ref.schema_snapshot_id != target_data.schema_snapshot_id
            or source_ref.frame_count != target_data.frame_count
        ):
            raise PackageConflict("annotation migration source or target binding differs")
        converted_source = LegacyEpisodeRef.model_validate(members[0]["plan"]["legacy"])
        if (
            converted_source.identity != source_ref.identity
            or converted_source.content_hash != source_ref.content_hash
            or converted_source.frame_count != source_ref.frame_count
            or converted_source.schema_snapshot_id != source_ref.schema_snapshot_id
            or self.packages.get(target_data.identity, target_data.data_version_id).data
            != target_data
        ):
            raise PackageConflict("annotation migration changes the original data/time contract")
        version, _ = pinned_legacy(self.batches.legacy, source_ref)
        if version.frequency_hz != members[0]["plan"]["schema_snapshot"]["frequency_hz"]:
            raise PackageConflict("annotation migration changes original time frequency")
        if (
            compute_fragment_hash(legacy_steps(self.batches.legacy, source_ref))
            != source_ref.content_hash
        ):
            raise PackageConflict("original annotation data rows differ from frozen reference")

    def prepare(self, dataset_id, batch_id, source_task_id, source_revision, source_ref):
        scope = self.batches._scope(dataset_id)
        source_ref = LegacyEpisodeRef.model_validate(source_ref)
        assert_scope(source_ref.identity)
        if source_ref.identity.dataset_id != dataset_id:
            raise PermissionError("annotation migration source dataset differs")
        batch = self.batches.get(dataset_id, batch_id)
        members = [
            m
            for m in batch["plan"]["members"]
            if m["receipt"]["data"]["identity"] == source_ref.identity.model_dump(mode="json")
        ]
        if len(members) != 1:
            raise PackageConflict("annotation migration Episode is absent from batch")
        target = DataVersionRef.model_validate(members[0]["receipt"]["data"])
        with self.packages.transaction() as cursor:
            snapshot = source_snapshot(cursor, scope, source_task_id)
            selected, excluded = selected_content(snapshot, source_revision)
            self._equivalence(batch, snapshot, source_ref, target)
            key = {
                "scope": scope,
                "batch_id": batch_id,
                "source_task_id": source_task_id,
                "source_revision": source_revision,
                "source_snapshot_sha256": digest(snapshot),
                "source_data": source_ref.model_dump(mode="json"),
                "target_data": target.model_dump(mode="json"),
            }
            mapping_id = "am_" + digest(key)
            try:
                return self.get(dataset_id, mapping_id, cursor=cursor)
            except KeyError:
                pass
            task_id = "annotation-migration-" + mapping_id[3:]
            plan = {
                "format": FORMAT,
                **key,
                "mapping_id": mapping_id,
                "target_task_id": task_id,
                "collection_version_id": batch["plan"]["collection"]["collection_version_id"],
                "dataset_version": batch["plan"]["collection"]["version"],
                "source_snapshot": snapshot,
                "source_revision_content_hash": selected.content_hash,
                "tags": [tag.model_dump(mode="json") for tag in selected.tags],
                "excluded_ranges": [value.model_dump(mode="json") for value in excluded],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "created_by": current_request_context().subject_id,
            }
            object_key = "annotation-migrations/" + "/".join(
                (*(quote(value, safe="") for value in scope), mapping_id, "mapping.json")
            )
            cursor.execute(
                """INSERT INTO episode_store.annotation_migrations
                (organization_id,project_id,region_code,dataset_id,mapping_id,batch_id,
                 source_task_id,target_task_id,target_data,plan,document_key)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                ON CONFLICT DO NOTHING""",
                (
                    *scope,
                    mapping_id,
                    batch_id,
                    source_task_id,
                    task_id,
                    canonical(key["target_data"]).decode(),
                    canonical(plan).decode(),
                    object_key,
                ),
            )
        self.checkpoint("annotation_mapping_prepared")
        return self.get(dataset_id, mapping_id)

    @staticmethod
    def _draft(plan):
        data = DataVersionRef.model_validate(plan["target_data"])
        selected, excluded = selected_content(plan["source_snapshot"], plan["source_revision"])
        now = datetime.fromisoformat(plan["created_at"])
        arguments = {
            "task_id": plan["target_task_id"],
            "base_lance_version": None,
            "data_ref": data,
            "tag_schema_id": selected.tag_schema_id,
            "tag_schema_version": selected.tag_schema_version,
            "author_id": plan["created_by"],
            "created_at": now,
        }
        initial = AnnotationRevision(
            **arguments,
            revision=0,
            parent_revision=None,
            client_mutation_id="migration-initial",
            operations=(),
            content_hash=revision_content_hash(
                base_lance_version=None,
                data_ref=data,
                tag_schema_id=selected.tag_schema_id,
                tag_schema_version=selected.tag_schema_version,
                tags=(),
                operations=(),
            ),
        )
        operations = tuple(
            AnnotationOperation(
                operation_id=f"migration-exclusion-{i}",
                kind=OperationKind.EXCLUDE,
                start_step=value.start_step,
                end_step=value.end_step,
                reason="Equivalent original exclusion range",
            )
            for i, value in enumerate(excluded)
        )
        revised = AnnotationRevision(
            **arguments,
            revision=1,
            parent_revision=0,
            client_mutation_id="migration-restore",
            tags=selected.tags,
            operations=operations,
            origin=RevisionOrigin.ANNOTATION_RESTORE,
            content_hash=revision_content_hash(
                base_lance_version=None,
                data_ref=data,
                tag_schema_id=selected.tag_schema_id,
                tag_schema_version=selected.tag_schema_version,
                tags=selected.tags,
                operations=operations,
            ),
        )
        task = AnnotationTask(
            **{
                k: arguments[k]
                for k in (
                    "task_id",
                    "base_lance_version",
                    "data_ref",
                    "tag_schema_id",
                    "tag_schema_version",
                    "created_at",
                )
            },
            project_id=data.identity.project_id,
            region_code=data.identity.region_code,
            dataset_id=data.identity.dataset_id,
            rollout_id=data.identity.rollout_id,
            dataset_version=plan["dataset_version"],
            base_step_count=data.frame_count,
            creation_source=AnnotationTaskCreationSource.SYSTEM_EPISODE,
            source_workflow_id="annotation-migration:" + plan["mapping_id"],
            current_revision=1,
            state_version=1,
            status=AnnotationStatus.DRAFT,
            etag=annotation_etag(plan["target_task_id"], 1, 1),
            updated_at=now,
        )
        aggregate = AnnotationAggregate(task=task, revisions=(initial, revised))
        validate_aggregate(aggregate)
        return aggregate

    def apply(self, dataset_id, mapping_id):
        scope = self.batches._scope(dataset_id)
        with self.packages.transaction() as cursor:
            mapping = self.get(dataset_id, mapping_id, cursor=cursor, lock=True)
            if mapping["status"] == "APPLIED":
                return mapping
            plan = mapping["plan"]
            snapshot = source_snapshot(cursor, scope, plan["source_task_id"])
            if (
                snapshot != plan["source_snapshot"]
                or digest(snapshot) != plan["source_snapshot_sha256"]
            ):
                raise PackageConflict(
                    "original annotation history changed since migration preparation"
                )
            self._equivalence(
                self.batches.get(dataset_id, plan["batch_id"]),
                snapshot,
                LegacyEpisodeRef.model_validate(plan["source_data"]),
                DataVersionRef.model_validate(plan["target_data"]),
            )
            draft = self._draft(plan)
            task, revised = draft.task, draft.revisions[1]
            result = {
                "mapping_id": mapping_id,
                "target_data": plan["target_data"],
                "target_task_id": task.task_id,
                "target_revision": 1,
                "target_revision_content_hash": revised.content_hash,
                "source_revision_content_hash": plan["source_revision_content_hash"],
                "source_snapshot_sha256": plan["source_snapshot_sha256"],
                "annotation_history_rewritten": False,
                "review_automatically_transferred": False,
            }
            document = {"format": "hc-annotation-migration/1", "plan": plan, "application": result}
            body = canonical(document)
            if len(body) > MAX_BYTES:
                raise ValueError("annotation migration document exceeds byte limit")
            EpisodePackageCommitter(self.packages, self.storage)._put_json(
                mapping["document_key"], document
            )
            self.checkpoint("annotation_mapping_object_verified")
            cursor.execute(
                """INSERT INTO annotation.annotation_tasks
                (task_id,organization_id,project_id,region_code,dataset_id,dataset_version,rollout_id,
                 base_lance_version,base_step_count,tag_schema_id,tag_schema_version,
                 task_kind,creation_source,source_workflow_id,current_revision,state_version,status,
                 etag,created_at,updated_at,data_ref)
                VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,
                        1,1,'DRAFT',%s,%s,%s,%s::jsonb)""",
                (
                    task.task_id,
                    *scope,
                    task.dataset_version,
                    task.rollout_id,
                    task.base_step_count,
                    task.tag_schema_id,
                    task.tag_schema_version,
                    task.task_kind.value,
                    task.creation_source.value,
                    task.source_workflow_id,
                    task.etag,
                    task.created_at,
                    task.updated_at,
                    task.data_ref.model_dump_json(),
                ),
            )
            for revision in draft.revisions:
                PostgresAnnotationRepository._insert_revision(cursor, revision)
            self.checkpoint("annotation_mapping_draft_written")
            cursor.execute(
                """UPDATE episode_store.annotation_migrations SET status='APPLIED',result=%s::jsonb,
                document_sha256=%s,document_size=%s,applied_at=clock_timestamp()
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s
                  AND mapping_id=%s AND status='PREPARED'""",
                (canonical(result).decode(), digest(document), len(body), *scope, mapping_id),
            )
        self.checkpoint("annotation_mapping_database_committed")
        return self.get(dataset_id, mapping_id)


def read_annotation_migration(repository, storage, data, mapping_id):
    """Caller checks annotation/read+download; lookup pins the complete data ref."""
    assert_scope(data.identity)
    with repository.transaction() as cursor:
        cursor.execute(
            """SELECT plan,result,document_key,document_sha256,document_size
            FROM episode_store.annotation_migrations
            WHERE organization_id=%s AND project_id=%s AND region_code=%s
              AND dataset_id=%s AND target_data=%s::jsonb AND mapping_id=%s AND status='APPLIED'""",
            (*data.identity.scope, data.identity.dataset_id, data.model_dump_json(), mapping_id),
        )
        row = cursor.fetchone()
    if row is None:
        raise KeyError("annotation migration absent from selected data version")
    plan, result, key, checksum, size = row
    if plan["target_data"] != data.model_dump(mode="json") or not 0 < size <= MAX_BYTES:
        raise PackageConflict("annotation migration data/size differs")
    head = storage.head(key)
    if head is None or head.size != size:
        raise PackageConflict("annotation migration JSON is missing or has changed size")
    body = bytearray()
    for chunk in storage.read_chunks(key):
        body.extend(chunk)
        if len(body) > size:
            raise PackageConflict("annotation migration JSON exceeded pinned size")
    if len(body) != size or hashlib.sha256(body).hexdigest() != checksum:
        raise PackageConflict("annotation migration JSON content hash differs")
    if json.loads(body) != {
        "format": "hc-annotation-migration/1",
        "plan": plan,
        "application": result,
    }:
        raise PackageConflict("annotation migration JSON differs from immutable database snapshot")
    return bytes(body), checksum
