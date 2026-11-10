"""Reconstruct evicted recording media in a job-local directory from frozen Raw."""

import hashlib
from pathlib import Path

from hc_data_platform.episode_store.native_plan import NativeFile
from hc_data_platform.episode_store.original_export import copy_original


def evictable(path):
    return path == "source/selected.mcap" or (path.startswith("videos/") and path.endswith(".mp4"))


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

            destination = folder / "derived"
            prepare(
                source,
                destination,
                cfg,
                preserve_source=bool(
                    reference and reference.get("schema_version") == "capture-recording-snapshot/v1"
                ),
            )
            self.prepared[ident] = destination
        path = self.prepared[ident] / relative
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024**2), b""):
                h.update(block)
        if path.stat().st_size != size or h.hexdigest() != sha256:
            raise ValueError("reconstructed native media differs from frozen receipt")
        return path
