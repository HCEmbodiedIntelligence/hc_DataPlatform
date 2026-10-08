"""Recoverable multi-object commit without directory renames or Lance writes."""

import hashlib
import io
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from hc_data_platform.object_storage.immutable import MultipartImmutableWriter, verify_object
from hc_data_platform.object_storage.models import ObjectIntegrityError
from hc_data_platform.object_storage.ports import ImmutableObjectWriter

from .models import CommitReceipt, DataVersionRef, PackageInput, canonical
from .postgres import PackageConflict, assert_scope
from .validation import package_file, validate_package


class EpisodePackageCommitter:
    def __init__(
        self,
        repository,
        storage,
        *,
        checkpoint=lambda stage: None,
        writer: ImmutableObjectWriter | None = None,
    ):
        self.repository, self.storage, self.checkpoint = repository, storage, checkpoint
        self.writer = writer if writer is not None else MultipartImmutableWriter(storage)

    def _verify_object(self, key, size, sha256):
        try:
            verify_object(self.storage, key, size, sha256)
        except ObjectIntegrityError as exc:
            raise PackageConflict(str(exc)) from exc

    def _put_file(self, root, prefix, entry):
        key = f"{prefix}/{entry.path}"
        try:
            with package_file(root, entry.path).open("rb") as stream:
                self.writer.write_immutable(key, stream, size=entry.size, sha256=entry.sha256)
        except ObjectIntegrityError as exc:
            raise PackageConflict(str(exc)) from exc

    def _put_json(self, key, value):
        body = canonical(value)
        try:
            self.writer.write_immutable(
                key, io.BytesIO(body), size=len(body), sha256=hashlib.sha256(body).hexdigest()
            )
        except ObjectIntegrityError as exc:
            raise PackageConflict(str(exc)) from exc

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
