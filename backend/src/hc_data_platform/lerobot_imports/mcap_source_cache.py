"""Share already verified MCAP inputs between Raw registration and preparation."""

import hashlib
import json
from contextlib import ExitStack, contextmanager
from pathlib import Path

from hc_data_platform.storage.local_cache import local_cache_for

from .cache import SourceCache


@contextmanager
def _entry(storage, scope, key, size):
    backend = local_cache_for(storage)
    if backend is None:
        yield None
        return
    cache = SourceCache(Path(backend.root) / "registered-mcap", max_bytes=24 * 1024**3, ttl_seconds=24 * 3600)
    identity = hashlib.sha256(json.dumps([*scope, key, size]).encode()).hexdigest()
    if not (cache.root / identity / "receipt.json").is_file():
        yield None
        return
    with ExitStack() as stack:
        try:
            stack.enter_context(cache.pin(identity))
        except OSError:
            yield None
            return
        yield cache.root / identity


def _signature(path):
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def _receipt(directory, size):
    try:
        digest, etag, signature = json.loads((directory / "receipt.json").read_text())
        if (isinstance(digest, str) and len(digest) == 64 and signature == _signature(directory / "source.mcap")
                and signature[2] == size):
            return digest, etag
    except (OSError, ValueError, TypeError):
        pass
    return None


def registration_digest(storage, scope, key, metadata, *, cacheable):
    """Reuse a verified legacy cache entry or hash in memory before admission."""
    if cacheable and metadata.etag:
        with _entry(storage, scope, key, metadata.size) as entry:
            if entry is not None:
                receipt = _receipt(entry, metadata.size)
                if receipt and receipt[1] == metadata.etag:
                    return receipt[0], metadata.size
    digest, count = hashlib.sha256(), 0
    for chunk in storage.read_chunks(key):
        count += len(chunk)
        if count > metadata.size:
            break
        digest.update(chunk)
    return digest.hexdigest(), count


def copy_registered_input(storage, scope, original, destination):
    if local_cache_for(storage) is None:
        return False
    key, size = original["object_key"], original["size"]
    with _entry(storage, scope, key, size) as entry:
        if entry is None:
            return False
        directory = entry
        receipt = _receipt(directory, size)
        if not receipt or receipt[0] != original["sha256"]:
            return False
        metadata = storage.head(key)
        if metadata is None or metadata.size != size or metadata.etag != receipt[1]:
            return False
        digest = hashlib.sha256()
        count = 0
        try:
            with (directory / "source.mcap").open("rb") as source, destination.open("wb") as target:
                while chunk := source.read(1024**2):
                    count += len(chunk)
                    if count > size:
                        break
                    digest.update(chunk)
                    target.write(chunk)
            if count == size and digest.hexdigest() == receipt[0]:
                return True
            (directory / "receipt.json").unlink(missing_ok=True)
        except OSError:
            pass
        return False
