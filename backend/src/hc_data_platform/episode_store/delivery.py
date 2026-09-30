"""Complete-package archives preserve every primary byte, including collected depth."""

import asyncio
import hashlib
import io
import logging
import time
import zipfile
from contextlib import suppress
from pathlib import Path
from tempfile import SpooledTemporaryFile, TemporaryDirectory

from hc_data_platform.core.artifact_tasks import DeliveryLeaseLost, renewing_lease
from hc_data_platform.object_storage.models import CompletedPart
from hc_data_platform.security import ScopeGuard

from .collection_delivery import CollectionDelivery
from .collection_delivery_models import CollectionDeliveryPlan, EpisodeDeliveryCandidates
from .delivery_models import EpisodeDeliveryDownload, EpisodeDeliveryMember, EpisodeDeliveryPlan
from .delivery_repository import EpisodeDeliveryBusy, failure, public_job, scope_key
from .models import canonical, digest
from .postgres import PackageConflict

logger = logging.getLogger(__name__)


def inventory_document(plan):
    return canonical(
        {
            "format": "hc-episode-delivery/1",
            "job_id": plan.job_id,
            "plan": plan.model_dump(mode="json"),
            "is_reassembled_collection": isinstance(plan, CollectionDeliveryPlan),
            "model_depth_training_support": "NOT_VALIDATED",
        }
    )


def archive_packages(packages, plan, destination, checkpoint, progress):
    """Write deterministic ZIP64 using fixed inventory and bounded read buffers."""
    count = size = 0
    last_report = time.monotonic()

    def member(archive, name, stream, expected_size, expected_hash):
        nonlocal count, size, last_report
        entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
        entry.external_attr = 0o100644 << 16
        checksum, read_size = hashlib.sha256(), 0
        with archive.open(entry, "w", force_zip64=True) as output:
            for block in iter(lambda: stream.read(1024**2), b""):
                checkpoint()
                read_size += len(block)
                if read_size > expected_size:
                    raise PackageConflict("archive member exceeds its manifest")
                checksum.update(block)
                output.write(block)
                size += len(block)
                if time.monotonic() - last_report >= 1:
                    progress(count, size)
                    last_report = time.monotonic()
        if read_size != expected_size or checksum.hexdigest() != expected_hash:
            raise PackageConflict("archive member differs from its manifest")
        count += 1
        progress(count, size)

    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_STORED, allowZip64=True
    ) as archive:
        for selection in plan.members:
            checkpoint()
            package = packages.files.open(selection.data.identity, selection.data.data_version_id)
            if (
                package.receipt.data != selection.data
                or package.receipt.manifest_sha256 != selection.manifest_sha256
            ):
                raise PackageConflict("archive source differs from the frozen plan")
            for entry in package.manifest.files:
                with package.file(entry.path) as path, path.open("rb") as stream:
                    member(
                        archive, f"{selection.root}/{entry.path}", stream, entry.size, entry.sha256
                    )
            for name, document in (
                ("hc/manifest.json", package.manifest),
                ("hc/commit.json", package.receipt),
            ):
                body = canonical(document.model_dump(mode="json"))
                member(
                    archive,
                    f"{selection.root}/{name}",
                    io.BytesIO(body),
                    len(body),
                    hashlib.sha256(body).hexdigest(),
                )
        body = inventory_document(plan)
        member(
            archive, "delivery.json", io.BytesIO(body), len(body), hashlib.sha256(body).hexdigest()
        )
    checksum = hashlib.sha256()
    with destination.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            checkpoint()
            checksum.update(block)
    return checksum.hexdigest(), destination.stat().st_size


class EpisodeDeliveryService:
    def __init__(self, packages, repository):
        self.packages, self.repository = packages, repository
        self.storage = packages.files.storage
        self.annotations = None
        self.collection = CollectionDelivery(self)

    @staticmethod
    def authorize(auth, *, command=False):
        organization, project, region = scope_key()
        ScopeGuard.require(auth, project, region, organization)
        for capability in ("dataset_version.read", "export.download") + (
            ("export.create",) if command else ()
        ):
            auth.require_capability(capability, project, organization)

    def _freeze(self, request):
        members = []
        for index, data in enumerate(sorted(request.data, key=lambda d: d.data_version_id)):
            if data.identity.scope != scope_key():
                raise PermissionError("delivery data is outside the authorized scope")
            package = self.packages.files.open(data.identity, data.data_version_id)
            if package.receipt.data != data:
                raise PackageConflict("requested data differs from its committed reference")
            metadata_size = sum(
                len(canonical(doc.model_dump(mode="json")))
                for doc in (package.manifest, package.receipt)
            )
            members.append(
                EpisodeDeliveryMember(
                    data=data,
                    manifest_sha256=package.receipt.manifest_sha256,
                    root="episode"
                    if request.archive_format == "independent_episode_zip/1"
                    else f"episodes/{index:06d}",
                    file_count=len(package.manifest.files) + 2,
                    byte_count=sum(e.size for e in package.manifest.files) + metadata_size,
                )
            )
        return EpisodeDeliveryPlan(members=tuple(members), archive_format=request.archive_format)

    def request(self, body, key, auth):
        self.authorize(auth, command=True)
        plan = self._freeze(body)
        row = self.repository.request_delivery(
            plan,
            key,
            auth.subject_id,
            sum(m.file_count for m in plan.members) + 1,
            sum(m.byte_count for m in plan.members) + len(inventory_document(plan)),
        )
        if row["status"] == "READY":
            self._verify_artifact(row)
        return public_job(row)

    def request_collection(self, body, key, auth):
        self.authorize(auth, command=True)
        row = self.repository.request_frozen(
            key,
            auth.subject_id,
            body.request_sha256,
            lambda: self.collection.freeze(body, auth),
        )
        self._authorize_plan(public_job(row).plan, auth)
        if row["status"] == "READY":
            self._verify_artifact(row)
        return public_job(row)

    def candidates(self, dataset_id, auth, *, collection_version_id=None, offset=0, limit=25):
        self.authorize(auth)
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("invalid candidate page")
        snapshot = (
            self.packages.collections.get(dataset_id, collection_version_id=collection_version_id)
            if collection_version_id
            else self.packages.collections.current(dataset_id)
        )
        if snapshot is None:
            return EpisodeDeliveryCandidates(
                dataset_id=dataset_id,
                collection_version_id=None,
                version=None,
                items=(),
                total=0,
                legacy_episode_count=0,
                next_offset=None,
            )
        entries = [e for e in snapshot.episodes if e.storage_format == "lerobot_v3"]
        return EpisodeDeliveryCandidates(
            dataset_id=dataset_id,
            collection_version_id=snapshot.collection_version_id,
            version=snapshot.version,
            items=tuple(entries[offset : offset + limit]),
            total=len(entries),
            legacy_episode_count=len(snapshot.episodes) - len(entries),
            next_offset=offset + limit if offset + limit < len(entries) else None,
        )

    def _authorize_plan(self, plan, auth):
        if isinstance(plan, CollectionDeliveryPlan):
            self.collection.authorize(plan, auth)

    def status(self, job_id, auth):
        self.authorize(auth)
        job = public_job(self.repository.get_job(job_id))
        self._authorize_plan(job.plan, auth)
        return job

    def list(self, auth, **page):
        self.authorize(auth)
        organization, project, _ = scope_key()
        jobs = self.repository.list_jobs(
            **page,
            include_annotated=auth.has_capability("annotation_task.read", project, organization),
        )
        for job in jobs:
            self._authorize_plan(job.plan, auth)
        return jobs

    def command(self, job_id, generation, auth, *, retry):
        self.authorize(auth, command=True)
        self._authorize_plan(public_job(self.repository.get_job(job_id)).plan, auth)
        return public_job(self.repository.command(job_id, generation, auth.subject_id, retry=retry))

    def _verify_artifact(self, row, checkpoint=lambda: None):
        head = self.storage.head(row["artifact_key"])
        if head is None or head.size != row["size_bytes"]:
            raise PackageConflict("archive is missing or differs from its receipt")
        count, checksum = 0, hashlib.sha256()
        for block in self.storage.read_chunks(row["artifact_key"]):
            checkpoint()
            count += len(block)
            if count > row["size_bytes"]:
                raise PackageConflict("archive exceeds its frozen size")
            checksum.update(block)
        if count != row["size_bytes"] or checksum.hexdigest() != row["artifact_sha256"]:
            raise PackageConflict("archive differs from its content hash")

    def download(self, job_id, auth):
        self.authorize(auth)
        row = self.repository.get_job(job_id)
        self._authorize_plan(public_job(row).plan, auth)
        if row["status"] != "READY":
            raise failure("NOT_READY", "Wait for packaging or retry a failed delivery.")
        self._verify_artifact(row)
        plan = public_job(row).plan
        name = (
            f"lerobot-collection-{plan.modalities}"
            if isinstance(plan, CollectionDeliveryPlan)
            else "episode"
            if len(plan.members) == 1
            else "independent-episodes"
        )
        url = self.storage.presign_read(
            row["artifact_key"], 300, download_name=f"{name}-{job_id}.zip"
        )
        self.repository.audit_download(row, auth.subject_id)
        return EpisodeDeliveryDownload(
            job_id=job_id,
            artifact_sha256=row["artifact_sha256"],
            size_bytes=row["size_bytes"],
            url=url,
        )

    def generate(self, job_id, generation):
        row = self.repository.claim(job_id, generation)
        if row is None:
            return
        try:
            with renewing_lease(self.repository, row) as checkpoint:
                plan = public_job(row).plan
                for selection in plan.members:
                    if selection.data.identity.scope != scope_key():
                        raise PackageConflict("delivery plan is outside worker scope")
                    receipt = self.packages.repository.get(
                        selection.data.identity, selection.data.data_version_id
                    )
                    if (
                        receipt.data != selection.data
                        or receipt.manifest_sha256 != selection.manifest_sha256
                    ):
                        raise PackageConflict("delivery package receipt changed")
                if row["artifact_key"] and self.storage.head(row["artifact_key"]) is not None:
                    self._verify_artifact(row, checkpoint)
                    self.repository.finish(row)
                    return
                directory = self.packages.directory / "complete-deliveries"
                directory.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(prefix="attempt-", dir=directory) as temporary:
                    archive = Path(temporary) / "delivery.zip"
                    if isinstance(plan, CollectionDeliveryPlan):
                        checksum, size = self.collection.build(
                            plan, Path(temporary), archive, row, checkpoint
                        )
                    else:
                        self.repository.phase(row, "archiving")
                        checksum, size = archive_packages(
                            self.packages,
                            plan,
                            archive,
                            checkpoint,
                            lambda files, size: self.repository.progress(row, files, size),
                        )
                    key = f"episode-deliveries/{digest(scope_key())}/{job_id}/{checksum}.zip"
                    self.repository.pin_artifact(row, key, checksum, size)
                    row.update(artifact_key=key, artifact_sha256=checksum, size_bytes=size)
                    self.repository.phase(row, "uploading")
                    self._upload(archive, row, checkpoint)
                    self.repository.finish(row)
        except DeliveryLeaseLost:
            current = self.repository.get_job(job_id)
            if current["generation"] == generation and current["status"] in {"PENDING", "RUNNING"}:
                raise EpisodeDeliveryBusy("delivery awaits lease recovery") from None
        except Exception as exc:
            code = (
                "EPISODE_DELIVERY_INTEGRITY_FAILED"
                if isinstance(exc, PackageConflict)
                else "EPISODE_DELIVERY_PACKAGING_FAILED"
            )
            logger.warning("Complete Episode delivery failed (%s)", type(exc).__name__)
            with suppress(DeliveryLeaseLost):
                self.repository.finish(row, error_code=code)

    def _upload(self, path, row, checkpoint):
        key = row["artifact_key"]
        if self.storage.head(key) is None:
            upload = self.storage.create_multipart(key)
            try:
                parts, uploaded = [], 0
                part_size = max(8 * 1024**2, (row["size_bytes"] + 9998) // 9999)
                with path.open("rb") as source:
                    while uploaded < row["size_bytes"]:
                        size = min(part_size, row["size_bytes"] - uploaded)
                        with SpooledTemporaryFile(max_size=8 * 1024**2, dir=path.parent) as part:
                            remaining = size
                            while remaining:
                                checkpoint()
                                block = source.read(min(1024**2, remaining))
                                if not block:
                                    raise PackageConflict("archive ended during upload")
                                part.write(block)
                                remaining -= len(block)
                            part.seek(0)
                            result = self.storage.upload_part_stream(
                                key, upload, len(parts) + 1, part, size
                            )
                        parts.append(CompletedPart(part_number=len(parts) + 1, etag=result.etag))
                        uploaded += size
                        self.repository.leased_update(row, "uploaded_bytes=%s", (uploaded,))
                self.repository.heartbeat(row)
                self.storage.complete_multipart(key, upload, parts)
            except BaseException:
                with suppress(Exception):
                    self.storage.abort_multipart(key, upload)
                raise
        self._verify_artifact(row, checkpoint)


class EpisodeDeliveryOutboxHandler:
    EVENT_TYPE = "episode.delivery.requested.v1"

    def __init__(self, service):
        self.service = service

    async def __call__(self, event):
        payload = event.payload
        if (
            event.event_type != self.EVENT_TYPE
            or event.schema_version != 1
            or event.aggregate_type != "episode_delivery"
            or (event.organization_id, event.project_id, event.region_code) != scope_key()
            or set(payload) != {"job_id", "generation"}
            or type(payload["generation"]) is not int
            or payload["generation"] < 1
            or event.aggregate_id != payload["job_id"]
        ):
            raise ValueError("invalid complete Episode delivery event")
        self.service.repository.validate_event(event)
        await asyncio.to_thread(self.service.generate, payload["job_id"], payload["generation"])
