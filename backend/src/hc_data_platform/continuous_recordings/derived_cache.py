"""Reconstruct evicted recording media in a job-local directory from frozen Raw."""

import hashlib
import os
from pathlib import Path

from hc_data_platform.episode_store.native_plan import NativeFile
from hc_data_platform.episode_store.original_export import copy_original


def evictable(path):
    return path == "source/selected.mcap" or (path.startswith("videos/") and path.endswith(".mp4"))


def link_cached(storage, key, size, sha256, destination):
    """Hard-link an immutable local cache payload instead of copying its bytes.

    The cache recorded the payload's SHA-256 when it was written and never
    overwrites it, so the link shares those verified bytes. Any other case,
    including a different filesystem, returns False and the caller copies.
    """
    from hc_data_platform.storage.local_cache import CacheError, CacheRoutingBucket

    bucket = getattr(storage, "_bucket", None)
    if not isinstance(bucket, CacheRoutingBucket):
        return False
    local = bucket.local_key(key)
    if not local:
        return False
    cache = bucket.cache
    try:
        with cache.lock(local):
            meta = cache.metadata(local)
            if meta["size"] != size or meta.get("sha256") != sha256:
                return False
            payload = (cache.directory(local) / "payload").resolve(strict=True)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.link(payload, destination)
    except (CacheError, OSError, ValueError, KeyError):
        return False
    return True


class DerivedCache:
    def __init__(self, storage, connections, scope, root):
        self.storage, self.connections, self.scope = storage, connections, tuple(scope)
        self.root, self.prepared = Path(root), {}

    def localize(self, key, relative, size, sha256, destination):
        file = NativeFile.model_validate(
            {
                "relative_path": relative or Path(destination).name,
                "object": {"key": key, "size": size, "sha256": sha256},
            }
        )
        destination = Path(destination)
        if link_cached(self.storage, key, size, sha256, destination):
            return destination
        try:
            copy_original(self.storage, file, destination, lambda: None)
            return destination
        except Exception:
            # A cache may expire between HEAD and GET. Never mask corruption or
            # provider errors while the committed object is still present.
            if self.storage.head(key) is not None:
                raise
            destination.unlink(missing_ok=True)
            recovered = self.recover(key, relative, size, sha256, force=True)
            if recovered is None:
                raise
            return recovered

    def recover(self, key, relative, size, sha256, *, force=False):
        parts = key.split("/")
        if (
            len(parts) < 7
            or parts[0] != "derived-mcap"
            or tuple(parts[1:4]) != self.scope
            or parts[5] != sha256
            or "/".join(parts[6:]) != relative
            or not evictable(relative)
        ):
            return None
        if not force and self.storage.head(key) is not None:
            return None
        ident = parts[4]
        if ident not in self.prepared:
            with self.connections() as db:
                row = db.execute(
                    """SELECT source_document,configuration,config_reference
                    FROM ingest.mcap_processing_files WHERE organization_id=%s AND project_id=%s
                    AND region_code=%s AND processing_id=%s AND status='READY' """,
                    (*self.scope, ident),
                ).fetchone()
            if row is None:
                raise ValueError("missing committed native processing recipe")
            original, cfg, reference = row
            folder = self.root / ("rebuild-" + ident)
            folder.mkdir(parents=True, exist_ok=True)
            source = folder / "original.mcap"
            descriptor = NativeFile.model_validate(
                {
                    "relative_path": "original.mcap",
                    "object": {
                        "key": original["object_key"],
                        "size": original["size"],
                        "sha256": original["sha256"],
                    },
                }
            )
            copy_original(self.storage, descriptor, source, lambda: None)
            from .mcap_native import prepare

            preserve = bool(
                reference and reference.get("schema_version") == "capture-recording-snapshot/v1"
            )

            def rebuild(name, **options):
                destination = folder / name
                prepare(source, destination, cfg, preserve_source=preserve, **options)
                return destination

            self.prepared[ident] = (rebuild, {})
        rebuild, variants = self.prepared[ident]
        # Recordings derived before RGB_KEYFRAME_INTERVAL/DEPTH_ENCODER_POOLS used
        # x264's default GOP and a single-thread x265; their frozen hashes are
        # reproduced only by those encoders.
        legacy = {"rgb_keyframe_interval": None, "depth_encoder_pools": None}
        for name, options in (("derived", {}), ("derived-legacy", legacy)):
            if name not in variants:
                variants[name] = rebuild(name, **options)
            path = variants[name] / relative
            if path.stat().st_size == size and _sha256(path) == sha256:
                return path
            if not relative.startswith("videos/"):
                break
        raise ValueError("reconstructed native media differs from frozen receipt")


def _sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            h.update(block)
    return h.hexdigest()
