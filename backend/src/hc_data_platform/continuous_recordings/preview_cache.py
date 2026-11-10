"""Rehydrate local recording previews, including evicted derived source media."""

from pathlib import Path
from tempfile import TemporaryDirectory

from hc_data_platform.storage.local_cache import local_cache_for

from .derived_cache import DerivedCache
from .previews import prepare_recording_previews, preview_key, read_preview


def restore_preview(storage, upload, asset, assets, connections):
    cache = local_cache_for(storage)
    if cache is None:
        return
    key = preview_key(upload, asset, assets)
    with cache.rebuild_lock("recording-previews/rebuild/" + str(upload.upload_id)):
        if read_preview(storage, upload, asset, assets):
            return
        # An independently expired video must not leave a receipt blocking publication.
        if storage.head(key) is None and storage.head(key + ".json") is not None:
            cache.delete_object(key + ".json")
        with TemporaryDirectory(prefix="recording-preview-cache-") as root:
            scope = upload.scope
            recovery = DerivedCache(
                storage,
                connections,
                (scope.organization_id, scope.project_id, scope.region_code),
                root,
            )
            known = {item.object_key: item for item in assets}

            class RecoveringStorage:
                def __getattr__(self, name):
                    return getattr(storage, name)

                def read_chunks(self, object_key):
                    item = known.get(object_key)
                    if item is None or storage.head(object_key) is not None:
                        yield from storage.read_chunks(object_key)
                        return
                    path = recovery.recover(
                        object_key,
                        item.manifest.path,
                        item.manifest.size,
                        item.manifest.sha256,
                        force=True,
                    )
                    if path is None:
                        raise FileNotFoundError("preview source is unavailable")
                    with Path(path).open("rb") as stream:
                        while block := stream.read(1024**2):
                            yield block

            prepare_recording_previews(RecoveringStorage(), upload, assets)


def cache_original_video(storage, upload, asset, assets):
    """Local browser copy for recordings that do not need a generated preview."""
    cache = local_cache_for(storage)
    if cache is None:
        return None
    key = preview_key(upload, asset, assets, selected=False)
    with cache.rebuild_lock(key):
        if storage.head(key) is None:
            from hc_data_platform.object_storage.immutable import MultipartImmutableWriter

            with TemporaryDirectory(prefix="recording-playback-") as root:
                from .previews import _download

                path = Path(root) / "media.mp4"
                _download(storage, asset, path, lambda: None)
                with path.open("rb") as stream:
                    MultipartImmutableWriter(storage).write_immutable(
                        key, stream, size=asset.manifest.size, sha256=asset.manifest.sha256
                    )
    return key
