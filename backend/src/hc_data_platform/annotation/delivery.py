"""Durable, authorized annotated RGB-D delivery; never a primary-data publication."""

import asyncio
import hashlib
import io
import json
import logging
import threading
import zipfile
from contextlib import contextmanager, suppress
from contextvars import copy_context
from pathlib import Path
from tempfile import TemporaryDirectory

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.episode_store.annotated_delivery import (
    ReviewReference,
    write_annotated_delivery,
)
from hc_data_platform.episode_store.models import canonical, digest
from hc_data_platform.episode_store.postgres import PackageConflict
from hc_data_platform.ingest.models import CompletedPart
from hc_data_platform.security import AuthContext

from .delivery_models import AnnotationDeliveryDownload, AnnotationDeliveryPlan
from .delivery_repository import DeliveryLeaseLost, public_job
from .revision_files import (
    AnnotationRevisionFile,
    RevisionFileDownload,
    SnapshotBusy,
    SnapshotIntegrityError,
    _failure,
)
from .subtasks import SubtaskProjection, SubtaskProjectionError

logger = logging.getLogger(__name__)


@contextmanager
def renewing_lease(repository, row):
    stopped, lost = threading.Event(), threading.Event()

    def renew():
        while not stopped.wait(15):
            try:
                repository.heartbeat(row)
            except Exception:
                lost.set()
                return

    def checkpoint():
        if lost.is_set():
            raise DeliveryLeaseLost("delivery lease renewal stopped")

    thread = threading.Thread(target=copy_context().run, args=(renew,), daemon=True)
    thread.start()
    try:
        yield checkpoint
    finally:
        stopped.set()
        thread.join(timeout=1)


def archive_delivery(root, manifest, destination, checkpoint):
    """Deterministic ZIP64 with per-member SHA verification and bounded buffers."""
    inventory = [(e.path, e.sha256, e.size) for e in manifest.files]
    manifest_body = canonical(manifest.model_dump(mode="json"))
    inventory.append(
        ("hc/delivery_manifest.json", hashlib.sha256(manifest_body).hexdigest(), len(manifest_body))
    )
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_STORED, allowZip64=True
    ) as archive:
        for relative, sha256, size in sorted(inventory):
            member = zipfile.ZipInfo("episode/" + relative, (1980, 1, 1, 0, 0, 0))
            member.external_attr = 0o100644 << 16
            checksum, count = hashlib.sha256(), 0
            with (
                (root / relative).open("rb") as source,
                archive.open(member, "w", force_zip64=True) as output,
            ):
                for block in iter(lambda: source.read(1024**2), b""):
                    checkpoint()
                    checksum.update(block)
                    count += len(block)
                    output.write(block)
            if checksum.hexdigest() != sha256 or count != size:
                raise SnapshotIntegrityError("delivery member changed during archiving")
    checksum = hashlib.sha256()
    with destination.open("rb") as source:
        for block in iter(lambda: source.read(1024**2), b""):
            checkpoint()
            checksum.update(block)
    return checksum.hexdigest(), destination.stat().st_size


class AnnotationDeliveryService:
    def __init__(self, files, packages, repository):
        self.files, self.packages, self.repository = files, packages, repository
        self.storage = files.storage

    def authorize(self, task_id, auth, *, command=False):
        task = self.files._authorize(task_id, auth, capability="export.download")
        if command:
            scope = current_request_context()
            auth.require_capability("export.create", scope.project_id, scope.organization_id)
        if task.data_ref is None:
            raise _failure(
                "ANNOTATION_DELIVERY_REQUIRES_EPISODE",
                "This delivery requires an independent Episode data version.",
            )
        return task

    def _freeze(self, task_id, body, auth):
        download = self.files.download(
            task_id, auth, revision=body.revision, allow_unreviewed=body.allow_unreviewed
        )
        history = self.files.annotation.get_history(task_id, auth)
        data = history.task.data_ref
        package = self.packages.files.open(data.identity, data.data_version_id)
        try:
            projection = SubtaskProjection.freeze(
                json.loads(download.content), data, download.file.content_sha256, body.track
            )
        except SubtaskProjectionError as exc:
            raise problem(
                status=422,
                code=exc.code,
                title="Invalid subtask selection",
                detail=str(exc),
                details={"annotation_ids": list(exc.annotation_ids)},
            ) from exc
        review = next((r for r in history.reviews if r.review_id == download.review_id), None)
        submission = next(
            (s for s in history.submissions if s.submission_id == download.submission_id), None
        )
        plan = AnnotationDeliveryPlan(
            binding=projection.binding,
            annotation_size=len(download.content),
            review=ReviewReference(
                status="APPROVED" if download.approved else "UNREVIEWED",
                review_id=download.review_id,
                submission_id=download.submission_id,
            ),
            approval=review,
            submission=submission,
            parent_manifest_sha256=package.receipt.manifest_sha256,
        )
        return plan, download.object_key

    def request(self, task_id, body, key, auth):
        self.authorize(task_id, auth, command=True)
        row = self.repository.request_delivery(
            task_id,
            auth.subject_id,
            key,
            digest(body.model_dump(mode="json")),
            lambda: self._freeze(task_id, body, auth),
        )
        if row["status"] == "READY":
            self._verify_artifact(row)
        return public_job(row)

    def status(self, task_id, job_id, auth):
        self.authorize(task_id, auth)
        return public_job(self.repository.get_job(task_id, job_id))

    def list(self, task_id, auth, *, limit=25, before=None):
        self.authorize(task_id, auth)
        return self.repository.list_jobs(task_id, limit, before)

    def command(self, task_id, job_id, generation, auth, *, retry):
        self.authorize(task_id, auth, command=True)
        return public_job(
            self.repository.command(task_id, job_id, generation, auth.subject_id, retry=retry)
        )

    def _verify_artifact(self, row, checkpoint=lambda: None):
        head = self.storage.head(row["artifact_key"])
        if head is None or head.size != row["size_bytes"]:
            raise _failure(
                "ANNOTATION_DELIVERY_INTEGRITY_FAILED",
                "The archive is missing or differs from its receipt.",
            )
        checksum, count = hashlib.sha256(), 0
        for chunk in self.storage.read_chunks(row["artifact_key"]):
            checkpoint()
            count += len(chunk)
            if count > row["size_bytes"]:
                raise _failure(
                    "ANNOTATION_DELIVERY_INTEGRITY_FAILED", "The archive exceeds its receipt size."
                )
            checksum.update(chunk)
        if count != row["size_bytes"] or checksum.hexdigest() != row["artifact_sha256"]:
            raise _failure(
                "ANNOTATION_DELIVERY_INTEGRITY_FAILED", "The archive failed content verification."
            )

    def download(self, task_id, job_id, auth):
        self.authorize(task_id, auth)
        row = self.repository.get_job(task_id, job_id)
        if row["status"] != "READY":
            raise _failure(
                "ANNOTATION_DELIVERY_NOT_READY", "Wait for packaging or retry a failed delivery."
            )
        self._verify_artifact(row)
        plan = public_job(row).plan
        url = self.storage.presign_read(
            row["artifact_key"],
            300,
            download_name=f"episode-{plan.binding.data.data_version_id}-r{plan.binding.revision}-{plan.review.status.lower()}.zip",
        )
        self.repository.audit_delivery_download(row, auth.subject_id)
        return AnnotationDeliveryDownload(
            job_id=job_id,
            artifact_sha256=row["artifact_sha256"],
            size_bytes=row["size_bytes"],
            url=url,
        )

    def _snapshot(self, row, plan, auth):
        task = self.authorize(row["task_id"], auth)
        if task.data_ref != plan.binding.data or plan.job_id != row["job_id"]:
            raise SnapshotIntegrityError("delivery plan changed")
        history = self.files.annotation.get_history(row["task_id"], auth)
        saved = history.revisions[plan.binding.revision]
        if saved.content_hash != plan.binding.revision_content_hash:
            raise SnapshotIntegrityError("delivery revision changed")
        if plan.approval is not None and (
            plan.approval not in history.reviews or plan.submission not in history.submissions
        ):
            raise SnapshotIntegrityError("frozen delivery approval is unavailable")
        body = self.files._read_verified(
            row["annotation_key"], plan.binding.annotation_file_sha256, plan.annotation_size
        )
        return RevisionFileDownload(
            body,
            AnnotationRevisionFile(
                task_id=row["task_id"],
                revision=plan.binding.revision,
                revision_content_hash=plan.binding.revision_content_hash,
                content_sha256=plan.binding.annotation_file_sha256,
                size_bytes=plan.annotation_size,
                status="READY",
                generation=1,
                attempts=0,
            ),
            plan.review.status == "APPROVED",
            plan.review.review_id,
            plan.review.submission_id,
        )

    def generate(self, task_id, job_id, generation, auth):
        self.authorize(task_id, auth)
        row = self.repository.claim_delivery(task_id, job_id, generation)
        if row is None:
            return
        try:
            with renewing_lease(self.repository, row) as checkpoint:
                plan = public_job(row).plan
                annotation = self._snapshot(row, plan, auth)
                package = self.packages.files.open(
                    plan.binding.data.identity, plan.binding.data.data_version_id
                )
                if package.receipt.manifest_sha256 != plan.parent_manifest_sha256:
                    raise SnapshotIntegrityError("delivery parent manifest changed")
                # Recover after a lost upload/DB response without rebuilding or transcoding.
                if row["artifact_key"] and self.storage.head(row["artifact_key"]) is not None:
                    self._verify_artifact(row, checkpoint)
                    self.repository.finish_delivery(row)
                    return
                directory = self.packages.directory / "annotation-deliveries"
                directory.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(prefix="attempt-", dir=directory) as temporary:
                    root, archive = Path(temporary) / "episode", Path(temporary) / "episode.zip"
                    self.repository.phase(row, "projecting")
                    manifest, manifest_sha = write_annotated_delivery(
                        root,
                        package,
                        annotation,
                        plan.binding.track,
                        checkpoint=checkpoint,
                        review_document={
                            "format": "hc-annotation-review/1",
                            "review": plan.review.model_dump(mode="json"),
                            "approval": plan.approval.model_dump(mode="json")
                            if plan.approval
                            else None,
                            "submission": plan.submission.model_dump(mode="json")
                            if plan.submission
                            else None,
                        },
                    )
                    self.repository.phase(row, "archiving")
                    checksum, size = archive_delivery(root, manifest, archive, checkpoint)
                    key = f"annotation-deliveries/{job_id}/{checksum}.zip"
                    self.repository.pin_artifact(row, key, checksum, size, manifest_sha)
                    row.update(artifact_key=key, artifact_sha256=checksum, size_bytes=size)
                    self.repository.phase(row, "uploading")
                    self._upload(archive, row, checkpoint)
                    self.repository.finish_delivery(row)
        except DeliveryLeaseLost:
            # A renewal failure/expired lease must remain retryable in the outbox.
            # Only a terminal state or a separately queued generation can be acked.
            current = self.repository.get_job(task_id, job_id)
            if current["generation"] == generation and current["status"] in {"PENDING", "RUNNING"}:
                raise SnapshotBusy("delivery is awaiting lease recovery") from None
            return
        except Exception as exc:
            code = (
                "ANNOTATION_DELIVERY_INTEGRITY_FAILED"
                if isinstance(exc, (SnapshotIntegrityError, PackageConflict))
                else "ANNOTATION_DELIVERY_PACKAGING_FAILED"
            )
            logger.warning("Annotation delivery failed (%s)", type(exc).__name__)
            with suppress(DeliveryLeaseLost):
                self.repository.finish_delivery(row, error_code=code)

    def _upload(self, path, row, checkpoint):
        key = row["artifact_key"]
        if self.storage.head(key) is None:
            upload = self.storage.create_multipart(key)
            try:
                parts = []
                part_size = max(8 * 1024**2, (row["size_bytes"] + 9998) // 9999)
                with path.open("rb") as source:
                    for number, block in enumerate(iter(lambda: source.read(part_size), b""), 1):
                        checkpoint()
                        part = self.storage.upload_part_stream(
                            key, upload, number, io.BytesIO(block), len(block)
                        )
                        parts.append(CompletedPart(part_number=number, etag=part.etag))
                checkpoint()
                self.storage.complete_multipart(key, upload, parts)
            except BaseException:
                with suppress(Exception):
                    self.storage.abort_multipart(key, upload)
                raise
        self._verify_artifact(row, checkpoint)


class AnnotationDeliveryOutboxHandler:
    EVENT_TYPE = "annotation.delivery.requested.v1"

    def __init__(self, service):
        self.service = service

    async def __call__(self, event):
        scope, payload = current_request_context(), event.payload
        if (
            event.event_type != self.EVENT_TYPE
            or event.schema_version != 1
            or event.aggregate_type != "annotation_delivery"
            or (scope.organization_id, scope.project_id, scope.region_code)
            != (event.organization_id, event.project_id, event.region_code)
            or set(payload) != {"task_id", "job_id", "generation"}
            or type(payload["generation"]) is not int
            or payload["generation"] < 1
            or not isinstance(payload["task_id"], str)
            or not payload["task_id"]
            or event.aggregate_id != payload["job_id"]
        ):
            raise ValueError("invalid annotation delivery event")
        self.service.repository.validate_event(event)
        auth = AuthContext(
            subject_id="annotation-delivery-worker",
            service_identity=True,
            capabilities=frozenset({"annotation_task.read", "export.download"}),
            organization_ids=frozenset({event.organization_id}),
            project_ids=frozenset({event.project_id}),
            region_codes=frozenset({event.region_code}),
            scope_pairs=frozenset({(event.project_id, event.region_code)}),
            organization_scope_triples=frozenset(
                {(event.organization_id, event.project_id, event.region_code)}
            ),
        )
        await asyncio.to_thread(
            self.service.generate,
            payload["task_id"],
            payload["job_id"],
            payload["generation"],
            auth,
        )
