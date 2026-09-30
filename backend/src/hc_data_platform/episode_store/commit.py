"""Recoverable multi-object commit without directory renames or Lance writes."""

import hashlib
import io
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from hc_data_platform.object_storage.models import CompletedPart

from .models import CommitReceipt, DataVersionRef, PackageInput, canonical
from .postgres import PackageConflict, assert_scope
from .validation import package_file, validate_package


class EpisodePackageCommitter:
    def __init__(self, repository, storage, *, checkpoint=lambda stage: None):
        self.repository, self.storage, self.checkpoint = repository, storage, checkpoint

    def _verify_object(self, key, size, sha256):
        metadata = self.storage.head(key)
        if metadata is None or metadata.size != size:
            raise PackageConflict("package object missing or wrong size")
        digest, total = hashlib.sha256(), 0
        for chunk in self.storage.read_chunks(key):
            total += len(chunk)
            if total > size:
                raise PackageConflict("package object exceeds declared size")
            digest.update(chunk)
        if total != size or digest.hexdigest() != sha256:
            raise PackageConflict(
                "package object hash mismatch; existing objects are never overwritten"
            )

    def _put_file(self, root, prefix, entry):
        key = f"{prefix}/{entry.path}"
        if self.storage.head(key) is None:
            upload = self.storage.create_multipart(key)
            try:
                parts = []
                with package_file(root, entry.path).open("rb") as stream:
                    # S3 permits <=10000 parts; 8 MiB also exceeds its minimum part size.
                    part_size = max(8 * 1024 * 1024, (entry.size + 9998) // 9999)
                    for number, block in enumerate(iter(lambda: stream.read(part_size), b""), 1):
                        part = self.storage.upload_part_stream(
                            key, upload, number, io.BytesIO(block), len(block)
                        )
                        parts.append(CompletedPart(part_number=number, etag=part.etag))
                self.storage.complete_multipart(key, upload, parts)
            except BaseException:
                with suppress(Exception):
                    self.storage.abort_multipart(key, upload)
                raise
        self._verify_object(key, entry.size, entry.sha256)

    def _put_json(self, key, value):
        body = canonical(value)
        if self.storage.head(key) is None:
            self.storage.put_json(key, value, if_none_match=True)
        self._verify_object(key, len(body), hashlib.sha256(body).hexdigest())

    def commit(self, root: Path, source: PackageInput, *, activate: bool = True) -> CommitReceipt:
        """Commit verified bytes; migration batches may defer the current pointer.

        Deferred packages remain immutable, explicitly addressable committed
        versions. Only a separately verified batch may activate their pointers.
        Retrying a commit never reactivates a version whose pointer moved later.
        """
        if type(activate) is not bool:
            raise TypeError("activate must be an explicit boolean")
        assert_scope(source.identity)
        # Validate before reserving visibility; the source may still be PROCESSING.
        # Assets here are local, frozen attempt files, never the READY export resolver.
        manifest = validate_package(Path(root), source)
        with self.repository.writer_lock(source.identity):
            version, previous = self.repository.reserve(manifest)
            receipt = CommitReceipt(
                data=DataVersionRef(
                    identity=source.identity,
                    data_version_id=source.data_version_id,
                    version=version,
                    content_hash=manifest.content_hash,
                    schema_snapshot_id=source.schema_snapshot_id,
                    package_prefix=source.package_prefix,
                    frame_count=manifest.frame_count,
                ),
                idempotency_key=source.idempotency_key,
                manifest_sha256=manifest.content_hash,
            )
            if previous is not None and previous != receipt:
                raise PackageConflict("idempotent receipt mismatch")
            # Even a retry after a lost response verifies the objects before reporting success.
            attempt = uuid4()
            self.repository.start_attempt(source, attempt)
            try:
                self.checkpoint("reserved")
                for entry in manifest.files:
                    self._put_file(Path(root), source.package_prefix, entry)
                    self.checkpoint("file_verified")
                self._put_json(
                    f"{source.package_prefix}/hc/manifest.json", manifest.model_dump(mode="json")
                )
                self._put_json(
                    f"{source.package_prefix}/hc/commit.json", receipt.model_dump(mode="json")
                )
                self.repository.set_attempt(source, attempt, "OBJECTS_VERIFIED")
                self.checkpoint("objects_verified")
                self.repository.commit(receipt, attempt, activate=activate)
                self.checkpoint("database_committed")
                return receipt
            except BaseException as exc:
                with suppress(Exception):
                    self.repository.set_attempt(source, attempt, "FAILED", type(exc).__name__)
                raise
