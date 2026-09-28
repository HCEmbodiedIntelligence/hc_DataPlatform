"""Authorized immutable package files and a bounded, process-private disk cache.

Public callers supply identity/version and a manifest-relative name. Object keys
come exclusively from a committed PostgreSQL receipt and its verified manifest.
Initial access verifies all bytes; later seeks reuse the verified local file.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path

from .models import CommitReceipt, EpisodeIdentity, FileEntry, PackageManifest
from .postgres import PackageConflict, assert_scope


class VerifiedFileCache:
    def __init__(
        self, directory: Path, *, capacity_bytes=2 * 1024**3, max_object_bytes=32 * 1024**3
    ):
        if capacity_bytes < 1 or max_object_bytes < capacity_bytes:
            raise ValueError("invalid package cache limits")
        directory.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(prefix="episode-reader-", dir=directory)
        self.root = Path(self._temporary.name)
        self.capacity, self.max_object = capacity_bytes, max_object_bytes
        self.entries = OrderedDict()
        self.total = 0
        # Hold the lease during a read: cached files cannot be evicted underneath
        # Arrow/FFmpeg. Each service instance has a bounded independent cache.
        self.lock = threading.RLock()

    def close(self):
        with self.lock:
            self.entries.clear()
            self.total = 0
            self._temporary.cleanup()

    @contextmanager
    def file(self, storage, key: str, entry: FileEntry):
        if entry.size > self.max_object:
            raise ValueError("package object exceeds configured local reader disk limit")
        with self.lock:
            cached = self.entries.get(entry.sha256)
            if cached is not None:
                path, size = cached
                if size != entry.size or path.stat().st_size != size:
                    raise PackageConflict("cached package file size mismatch")
                self.entries.move_to_end(entry.sha256)
                yield path
                return
            path = self.root / entry.sha256
            digest, total = hashlib.sha256(), 0
            try:
                with path.open("xb") as output:
                    for chunk in storage.read_chunks(key):
                        total += len(chunk)
                        if total > entry.size:
                            raise PackageConflict("package object exceeds manifest size")
                        digest.update(chunk)
                        output.write(chunk)
                if total != entry.size or digest.hexdigest() != entry.sha256:
                    raise PackageConflict("package file hash differs from committed manifest")
                if entry.size <= self.capacity:
                    while self.total + entry.size > self.capacity:
                        _, (old, size) = self.entries.popitem(last=False)
                        old.unlink()
                        self.total -= size
                    self.entries[entry.sha256] = (path, total)
                    self.total += total
                yield path
            finally:
                # Only private cache/temp copies are removed; no storage deletes.
                if entry.sha256 not in self.entries:
                    path.unlink(missing_ok=True)


class AuthorizedPackage:
    def __init__(self, receipt, manifest, storage, cache):
        self.receipt: CommitReceipt = receipt
        self.manifest: PackageManifest = manifest
        self.storage, self.cache = storage, cache
        self.entries = {entry.path: entry for entry in manifest.files}

    @contextmanager
    def file(self, relative: str):
        assert_scope(self.receipt.data.identity)
        if relative not in self.entries:
            raise KeyError("file is not part of the authorized committed package")
        key = f"{self.receipt.data.package_prefix}/{relative}"
        with self.cache.file(self.storage, key, self.entries[relative]) as path:
            yield path

    def document(self, relative: str):
        if self.entries[relative].size > 16 * 1024**2:
            raise ValueError("package JSON exceeds reader limit")
        with self.file(relative) as path:
            return json.loads(path.read_bytes())


class PackageFiles:
    def __init__(self, repository, storage, cache: VerifiedFileCache):
        self.repository, self.storage, self.cache = repository, storage, cache

    def open(self, identity: EpisodeIdentity, data_version_id: str) -> AuthorizedPackage:
        assert_scope(identity)
        # This exact-version lookup is mandatory even on a warm content cache.
        receipt = self.repository.get(identity, data_version_id)
        key = f"{receipt.data.package_prefix}/hc/manifest.json"
        head = self.storage.head(key)
        if head is None or not 0 < head.size <= 16 * 1024**2:
            raise PackageConflict("committed package manifest missing or too large")
        entry = FileEntry(
            path="manifest.json",
            size=head.size,
            sha256=receipt.manifest_sha256,
            media_type="application/json",
        )
        with self.cache.file(self.storage, key, entry) as path:
            manifest = PackageManifest.model_validate_json(path.read_bytes())
        source, data = manifest.input, receipt.data
        if (
            manifest.content_hash != data.content_hash
            or source.identity != identity
            or source.data_version_id != data.data_version_id
            or source.package_prefix != data.package_prefix
            or source.schema_snapshot_id != data.schema_snapshot_id
            or manifest.frame_count != data.frame_count
        ):
            raise PackageConflict("manifest differs from authorized data version")
        return AuthorizedPackage(receipt, manifest, self.storage, self.cache)
