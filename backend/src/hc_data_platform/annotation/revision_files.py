"""Durable, revision-pinned annotation files. GET never schedules generation."""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.episode_store.models import canonical
from hc_data_platform.ingest.ports import ObjectStoragePort
from hc_data_platform.security import AuthContext, ScopeGuard

from .models import AnnotationHistory, ReviewDecision, TagSchemaVersion
from .postgres import ConnectionFactory, _row
from .service import AnnotationService, normalize_operations
from .validation import revision_content_hash, schema_content_hash

FORMAT = "hc-annotation/1"
MAX_BYTES = 64 * 1024 * 1024
logger = logging.getLogger(__name__)
_SCOPE = """
organization_id = NULLIF(current_setting('app.organization_id', true), '')
AND project_id = NULLIF(current_setting('app.project_id', true), '')
AND region_code = NULLIF(current_setting('app.region_code', true), '')
"""
_IDENTITY = f"task_id = %s AND revision = %s AND format_version = %s AND {_SCOPE}"


class AnnotationRevisionFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    revision: int = Field(ge=0)
    revision_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    format_version: Literal["hc-annotation/1"] = FORMAT
    status: Literal["NOT_REQUESTED", "PENDING", "GENERATING", "READY", "FAILED"]
    generation: int = Field(ge=0)
    attempts: int = Field(ge=0)
    content_sha256: str | None = None
    size_bytes: int | None = None
    error_code: str | None = None
    updated_at: datetime | None = None


class SnapshotIntegrityError(RuntimeError):
    pass


class SnapshotBusy(RuntimeError):
    pass


def _failure(code: str, detail: str, status: int = 409):
    return problem(status=status, code=code, title="Annotation file unavailable", detail=detail)


def _public(row: dict[str, Any]) -> AnnotationRevisionFile:
    return AnnotationRevisionFile.model_validate(
        {key: row[key] for key in AnnotationRevisionFile.model_fields}
    )


class PostgresRevisionFiles:
    def __init__(self, connections: ConnectionFactory):
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

    @staticmethod
    def _get(cursor, task_id, revision, *, lock=False):
        cursor.execute(
            f"SELECT * FROM annotation.revision_files WHERE {_IDENTITY}"
            + (" FOR UPDATE" if lock else ""),
            (task_id, revision, FORMAT),
        )
        raw = cursor.fetchone()
        return None if raw is None else _row(cursor, raw)

    def get(self, task_id, revision):
        with self.transaction() as cursor:
            return self._get(cursor, task_id, revision)

    def request(self, task_id, revision, expected_generation, actor_id):
        with self.transaction() as cursor:
            # Also serializes first-time historical requests where no job row exists.
            cursor.execute(
                f"SELECT task_id FROM annotation.annotation_tasks WHERE task_id = %s "
                f"AND {_SCOPE} FOR UPDATE",
                (task_id,),
            )
            if cursor.fetchone() is None:
                raise _failure("ANNOTATION_NOT_FOUND", "Task is unavailable in this scope.", 404)
            old = self._get(cursor, task_id, revision, lock=True)
            if expected_generation != (0 if old is None else old["generation"]):
                raise _failure("ANNOTATION_FILE_GENERATION_CONFLICT", "Refresh the file status.")
            if old is not None and old["status"] in {"PENDING", "GENERATING"}:
                return old
            if old is None:
                cursor.execute(
                    """INSERT INTO annotation.revision_files
                        (organization_id, project_id, region_code, task_id, revision,
                         revision_content_hash, status)
                        SELECT t.organization_id, t.project_id, t.region_code, r.task_id,
                            r.revision, r.content_hash, 'PENDING'
                        FROM annotation.annotation_tasks t
                        JOIN annotation.annotation_revisions r USING (task_id)
                        WHERE r.task_id = %s AND r.revision = %s""",
                    (task_id, revision),
                )
            else:
                cursor.execute(
                    f"""UPDATE annotation.revision_files SET status = 'PENDING',
                        generation = generation + 1, error_code = NULL,
                        updated_at = clock_timestamp() WHERE {_IDENTITY}""",
                    (task_id, revision, FORMAT),
                )
            row = self._get(cursor, task_id, revision)
            if row is None:
                raise _failure("ANNOTATION_NOT_FOUND", "Revision does not exist.", 404)
            self._audit(cursor, row, actor_id, "annotation.file.requested", {})
            return row

    def claim(self, task_id, revision, generation, content_hash):
        with self.transaction() as cursor:
            row = self._get(cursor, task_id, revision, lock=True)
            if row is None or row["revision_content_hash"] != content_hash:
                raise SnapshotIntegrityError("outbox revision identity mismatch")
            if row["generation"] != generation or row["status"] in {"READY", "FAILED"}:
                return None
            cursor.execute("SELECT clock_timestamp() AS now")
            now = _row(cursor, cursor.fetchone())["now"]
            if row["lease_expires_at"] is not None and row["lease_expires_at"] > now:
                raise SnapshotBusy("annotation file generation is leased")
            token = uuid4()
            cursor.execute(
                f"""UPDATE annotation.revision_files SET status = 'GENERATING',
                    lease_token = %s, lease_expires_at = clock_timestamp() + interval '5 minutes',
                    attempts = attempts + 1, updated_at = clock_timestamp()
                    WHERE {_IDENTITY}""",
                (token, task_id, revision, FORMAT),
            )
            return self._get(cursor, task_id, revision)

    def pin(self, row, sha256, size):
        with self.transaction() as cursor:
            cursor.execute(
                f"""UPDATE annotation.revision_files SET content_sha256 = %s, size_bytes = %s
                    WHERE {_IDENTITY} AND generation = %s AND lease_token = %s
                    AND (content_sha256 IS NULL OR (content_sha256 = %s AND size_bytes = %s))
                    RETURNING task_id""",
                (
                    sha256,
                    size,
                    row["task_id"],
                    row["revision"],
                    FORMAT,
                    row["generation"],
                    row["lease_token"],
                    sha256,
                    size,
                ),
            )
            if cursor.fetchone() is None:
                raise SnapshotIntegrityError("file content changed or worker lease was replaced")

    def finish(self, row, *, object_key=None, error_code=None):
        with self.transaction() as cursor:
            cursor.execute(
                f"""UPDATE annotation.revision_files SET status = %s, object_key = %s,
                    error_code = %s, lease_token = NULL, lease_expires_at = NULL,
                    updated_at = clock_timestamp()
                    WHERE {_IDENTITY} AND generation = %s AND lease_token = %s
                    RETURNING task_id""",
                (
                    "READY" if error_code is None else "FAILED",
                    object_key,
                    error_code,
                    row["task_id"],
                    row["revision"],
                    FORMAT,
                    row["generation"],
                    row["lease_token"],
                ),
            )
            # An expired worker cannot finalize a newer attempt or generation.
            return cursor.fetchone() is not None

    @staticmethod
    def _audit(cursor, row, actor_id, action, details):
        cursor.execute(
            """INSERT INTO core.audit_events
                (audit_id, organization_id, project_id, region_code, actor_id, action,
                 resource_type, resource_id, request_id, details, occurred_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'annotation_revision_file', %s,
                    NULLIF(current_setting('app.request_id', true), ''), %s::jsonb,
                    clock_timestamp())""",
            (
                str(uuid4()),
                row["organization_id"],
                row["project_id"],
                row["region_code"],
                actor_id,
                action,
                f"{row['task_id']}:{row['revision']}:{FORMAT}",
                json.dumps(
                    {
                        "revision": row["revision"],
                        "generation": row["generation"],
                        "revision_content_hash": row["revision_content_hash"],
                        "content_sha256": row["content_sha256"],
                        **details,
                    }
                ),
            ),
        )

    def audit_download(self, row, actor_id, details):
        with self.transaction() as cursor:
            self._audit(cursor, row, actor_id, "annotation.file.downloaded", details)


def revision_document(
    history: AnnotationHistory, revision: int, schema: TagSchemaVersion, organization_id: str
) -> dict[str, Any]:
    task = history.task
    saved = history.revisions[revision]
    operations = tuple(op for rev in history.revisions[: revision + 1] for op in rev.operations)
    expected = revision_content_hash(
        base_lance_version=task.base_lance_version,
        data_ref=task.data_ref,
        tag_schema_id=task.tag_schema_id,
        tag_schema_version=task.tag_schema_version,
        tags=saved.tags,
        operations=operations,
    )
    if schema_content_hash(schema.document) != schema.content_hash:
        raise SnapshotIntegrityError("schema content hash mismatch")
    if expected != saved.content_hash or (schema.schema_id, schema.version) != (
        task.tag_schema_id,
        task.tag_schema_version,
    ):
        raise SnapshotIntegrityError("revision or schema identity mismatch")
    return {
        "format_version": FORMAT,
        "data_binding": {
            "organization_id": organization_id,
            "project_id": task.project_id,
            "region_code": task.region_code,
            "dataset_id": task.dataset_id,
            "dataset_version": task.dataset_version,
            "rollout_id": task.rollout_id,
            "base_step_count": task.base_step_count,
            **(
                {"data_ref": task.data_ref.model_dump(mode="json")}
                if task.data_ref
                else {"base_lance_version": task.base_lance_version}
            ),
        },
        "revision": saved.model_copy(
            update={"created_at": saved.created_at.astimezone(timezone.utc)}
        ).model_dump(mode="json"),
        "tag_schema": {
            "schema_id": schema.schema_id,
            "version": schema.version,
            "content_hash": schema.content_hash,
            "document": schema.document.model_dump(mode="json"),
        },
        "interval_convention": {
            "unit": "step",
            "origin": 0,
            "bounds": "[start_step,end_step)",
            "timeline": "bound_data_version",
            "exclusion_scope": "ALL_MODALITIES",
        },
        "cumulative_operations": [op.model_dump(mode="json") for op in operations],
        "effective_exclusions": [
            item.model_dump(mode="json")
            for item in normalize_operations(history.revisions, revision)
        ],
        "source": {
            "task_id": task.task_id,
            "creation_source": task.creation_source.value,
            "source_workflow_id": task.source_workflow_id,
            "task_created_at": task.created_at.astimezone(timezone.utc).isoformat(),
        },
        # A later review must never change these bytes. Review records stay independently bound.
        "review_binding": "separate_submission_and_review_records",
    }


@dataclass(frozen=True)
class RevisionFileDownload:
    content: bytes
    file: AnnotationRevisionFile
    approved: bool
    review_id: str | None
    submission_id: str | None
    # Internal locator, never serialized in public responses or supplied by a client.
    object_key: str | None = None


class AnnotationRevisionFileService:
    def __init__(
        self,
        annotation: AnnotationService,
        repository: PostgresRevisionFiles,
        storage: ObjectStoragePort,
    ):
        self.annotation, self.repository, self.storage = annotation, repository, storage

    def _authorize(self, task_id, auth, *, capability="annotation_task.read"):
        context = current_request_context()
        if not all((context.organization_id, context.project_id, context.region_code)):
            raise _failure(
                "ANNOTATION_FILE_SCOPE_REQUIRED", "Select an organization/project/region.", 403
            )
        ScopeGuard.require(auth, context.project_id, context.region_code, context.organization_id)
        auth.require_capability(capability, context.project_id, context.organization_id)
        return self.annotation.read_task(task_id, auth)

    def status(self, task_id, revision, auth):
        self._authorize(task_id, auth)
        saved = self.annotation.get_revision(task_id, revision, auth)
        row = self.repository.get(task_id, revision)
        if row is not None:
            return _public(row)
        return AnnotationRevisionFile(
            task_id=task_id,
            revision=revision,
            revision_content_hash=saved.content_hash,
            status="NOT_REQUESTED",
            generation=0,
            attempts=0,
        )

    def request(self, task_id, revision, expected_generation, auth):
        self._authorize(task_id, auth, capability="annotation.save")
        self.annotation.get_revision(task_id, revision, auth)
        return _public(
            self.repository.request(task_id, revision, expected_generation, auth.subject_id)
        )

    def generate(self, task_id, revision, generation, content_hash, auth):
        self._authorize(task_id, auth)
        row = self.repository.claim(task_id, revision, generation, content_hash)
        if row is None:
            return
        try:
            history = self.annotation.get_history(task_id, auth)
            schema = self.annotation.get_bound_tag_schema(task_id, auth)
            document = revision_document(history, revision, schema, row["organization_id"])
            body = canonical(document)
            if len(body) > MAX_BYTES:
                raise SnapshotIntegrityError("annotation snapshot exceeds maximum size")
            sha256 = hashlib.sha256(body).hexdigest()
            self.repository.pin(row, sha256, len(body))
            key = (
                "annotation-files/"
                + "/".join(
                    "id-" + quote(str(value), safe="-_")
                    for value in (
                        row["organization_id"],
                        row["project_id"],
                        row["region_code"],
                        task_id,
                        revision,
                        FORMAT,
                        generation,
                    )
                )
                + "/annotation.json"
            )
            if self.storage.head(key) is None:
                try:
                    self.storage.put_json(key, document, if_none_match=True)
                except Exception:
                    # A previous lease can finish its conditional PUT concurrently.
                    # Reuse only after the same size/hash verification below.
                    if self.storage.head(key) is None:
                        raise
            self._read_verified(key, sha256, len(body))
            self.repository.finish(row, object_key=key)
        except SnapshotIntegrityError:
            self.repository.finish(row, error_code="ANNOTATION_FILE_INTEGRITY_FAILED")
        except Exception as exc:
            # Never persist raw adapter errors (which can contain credentials or object paths).
            logger.warning("Annotation revision file generation failed (%s)", type(exc).__name__)
            self.repository.finish(row, error_code="ANNOTATION_FILE_GENERATION_FAILED")

    def _read_verified(self, key, sha256, size):
        metadata = self.storage.head(key)
        if metadata is None or metadata.size != size or not 0 < size <= MAX_BYTES:
            raise SnapshotIntegrityError("annotation object missing or size mismatch")
        body = bytearray()
        for chunk in self.storage.read_chunks(key, chunk_size=1024 * 1024):
            if len(body) + len(chunk) > size:
                raise SnapshotIntegrityError("annotation object exceeded pinned size")
            body.extend(chunk)
        if len(body) != size or hashlib.sha256(body).hexdigest() != sha256:
            raise SnapshotIntegrityError("annotation object hash mismatch")
        return bytes(body)

    def download(self, task_id, auth, *, revision=None, allow_unreviewed=False):
        self._authorize(task_id, auth, capability="export.download")
        history = self.annotation.get_history(task_id, auth)
        task = history.task
        selected = task.approved_revision if revision is None else revision
        if selected is None:
            raise _failure(
                "ANNOTATION_APPROVED_REVISION_REQUIRED",
                "No current approved revision is available.",
            )
        if not 0 <= selected < len(history.revisions):
            raise _failure("ANNOTATION_NOT_FOUND", "Revision does not exist.", 404)
        approved = selected == task.approved_revision
        review = next((r for r in history.reviews if r.review_id == task.approved_review_id), None)
        submission = next(
            (s for s in history.submissions if review and s.submission_id == review.submission_id),
            None,
        )
        if approved and (
            review is None
            or review.revision != selected
            or review.decision != ReviewDecision.APPROVE
            or submission is None
            or submission.revision != selected
            or submission.revision_content_hash != history.revisions[selected].content_hash
        ):
            raise _failure(
                "ANNOTATION_FILE_INTEGRITY_FAILED", "Approval does not bind this revision."
            )
        if not approved and not allow_unreviewed:
            raise _failure(
                "ANNOTATION_UNREVIEWED_DOWNLOAD_REQUIRED",
                "Explicitly allow this unreviewed revision.",
            )
        row = self.repository.get(task_id, selected)
        if row is None or row["status"] != "READY":
            raise _failure("ANNOTATION_FILE_NOT_READY", "Wait for generation or request a retry.")
        try:
            body = self._read_verified(row["object_key"], row["content_sha256"], row["size_bytes"])
        except SnapshotIntegrityError as exc:
            raise _failure(
                "ANNOTATION_FILE_INTEGRITY_FAILED",
                "The stored file failed verification; request regeneration.",
            ) from exc
        except Exception as exc:
            raise _failure(
                "ANNOTATION_FILE_STORAGE_UNAVAILABLE", "The file store is unavailable.", 503
            ) from exc
        details = {
            "approved": approved,
            "review_id": review.review_id if approved else None,
            "submission_id": submission.submission_id if approved else None,
        }
        self.repository.audit_download(row, auth.subject_id, details)
        return RevisionFileDownload(body, _public(row), **details, object_key=row["object_key"])


class AnnotationRevisionFileOutboxHandler:
    EVENT_TYPE = "annotation.revision_file.requested.v1"

    def __init__(self, service: AnnotationRevisionFileService):
        self.service = service

    def __call__(self, event: DomainEventEnvelope):
        context = current_request_context()
        if (
            event.event_type != self.EVENT_TYPE
            or event.schema_version != 1
            or event.aggregate_type != "annotation_revision_file"
            or not event.organization_id
            or not event.region_code
            or (context.organization_id, context.project_id, context.region_code)
            != (event.organization_id, event.project_id, event.region_code)
        ):
            raise ValueError("invalid annotation file event scope")
        payload = event.payload
        if set(payload) != {
            "task_id",
            "revision",
            "format_version",
            "generation",
            "revision_content_hash",
        }:
            raise ValueError("invalid annotation file event payload")
        task_id, revision, generation = (
            payload["task_id"],
            payload["revision"],
            payload["generation"],
        )
        if (
            not isinstance(task_id, str)
            or not task_id
            or type(revision) is not int
            or revision < 0
            or type(generation) is not int
            or generation < 1
            or payload["format_version"] != FORMAT
            or event.aggregate_id != f"{task_id}/{revision}/{FORMAT}"
        ):
            raise ValueError("invalid annotation file event identity")
        auth = AuthContext(
            subject_id="annotation-file-worker",
            service_identity=True,
            capabilities=frozenset({"annotation_task.read"}),
            organization_ids=frozenset({event.organization_id}),
            project_ids=frozenset({event.project_id}),
            region_codes=frozenset({event.region_code}),
            scope_pairs=frozenset({(event.project_id, event.region_code)}),
            organization_scope_triples=frozenset(
                {(event.organization_id, event.project_id, event.region_code)}
            ),
        )
        self.service.generate(task_id, revision, generation, payload["revision_content_hash"], auth)
