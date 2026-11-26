"""Stream prepared MCAP collections as directories in the downloaded Episode bundle."""

import hashlib
import json
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing, contextmanager
from contextvars import copy_context
from tempfile import TemporaryFile

from hc_data_platform.lerobot_imports.local_files import safe_relative
from hc_data_platform.lerobot_imports.source_archive import _ZipBuffer
from hc_data_platform.storage.local_cache import local_cache_for

from .collection_records import record_document
from .export_formats import MCAP_PAYLOAD_ARCHIVES

BLOCK = 1024 * 1024


@contextmanager
def verified_part(storage, part, directory):
    cache = local_cache_for(storage)
    if cache is not None:
        # Opening through the cache pins an open descriptor even if the entry expires.
        with closing(cache.get_object(part.key)) as body:
            source = body.stream
            if (
                body.content_length != part.size
                or hashlib.file_digest(source, "sha256").hexdigest() != part.sha256
            ):
                raise ValueError("Episode archive differs from its prepared receipt")
            source.seek(0)
            yield source
        return

    with TemporaryFile(dir=directory) as source:
        checksum = hashlib.sha256()
        for chunk in storage.read_chunks(part.key, chunk_size=BLOCK):
            if source.tell() + len(chunk) > part.size:
                raise ValueError("Episode archive exceeds its prepared size")
            source.write(chunk)
            checksum.update(chunk)
        if source.tell() != part.size or checksum.hexdigest() != part.sha256:
            raise ValueError("Episode archive differs from its prepared receipt")
        source.seek(0)
        yield source


def verified_parts(storage, parts, directory):
    if len(parts) <= 50:
        for part in parts:
            with verified_part(storage, part, directory) as source:
                yield part, source
        return

    # Hold at most four open verified sources, even when the first is slow.
    pending = deque()
    remaining = iter(parts)
    pool = ThreadPoolExecutor(max_workers=4)

    def schedule(part):
        owner = ExitStack()
        future = pool.submit(copy_context().run, owner.enter_context,
                             verified_part(storage, part, directory))
        pending.append((part, owner, future))

    try:
        for _ in range(4):
            schedule(next(remaining))
        while pending:
            part, owner, future = pending.popleft()
            with owner:
                yield part, future.result()
            if (following := next(remaining, None)) is not None:
                schedule(following)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        for _, owner, _ in pending:
            owner.close()


def write_member(archive, buffer, name, source):
    entry = zipfile.ZipInfo(name)
    entry.external_attr = 0o100644 << 16
    with archive.open(entry, "w", force_zip64=True) as target:
        yield buffer.drain()
        while chunk := source.read(BLOCK):
            target.write(chunk)
            yield buffer.drain()
    yield buffer.drain()


def stream_episode_bundle(storage, parts, records, directory, *, on_files=None):
    file_names = {}
    buffer = _ZipBuffer()
    exported_records = {}
    with (closing(verified_parts(storage, parts, directory)) as sources,
          zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as outer):
        for part, source in sources:
            path = part.path
            with zipfile.ZipFile(source) as inner:
                # Read the frozen format from the artifact itself, including pre-upgrade caches.
                is_mcap = inner.comment in {kind.encode("ascii") for kind in MCAP_PAYLOAD_ARCHIVES}
                if "delivery.json" in inner.namelist():
                    with inner.open("delivery.json") as inventory:
                        kind = json.load(inventory)["plan"].get("archive_format", "")
                        is_mcap = kind.startswith("mcap_collection_zip/")
                if on_files is not None:
                    file_names[part.path] = [m.filename for m in inner.infolist() if not m.is_dir()
                                             and (not is_mcap or (not m.filename.startswith("collection/hc/")
                                                  and m.filename not in {"delivery.json", "collection_records.json"}))]
                    if not is_mcap:
                        file_names[part.path].append(part.path)
                if is_mcap:
                    path = path.removesuffix(".zip")
                    for member in inner.infolist():
                        if (
                            member.is_dir()
                            or member.filename in {"collection_records.json", "delivery.json"}
                            or member.filename.startswith("collection/hc/")
                        ):
                            continue
                        name = path + "/" + safe_relative(member.orig_filename)
                        if name in outer.NameToInfo:
                            raise ValueError("Duplicate MCAP bundle path")
                        with inner.open(member) as data:
                            yield from write_member(outer, buffer, name, data)
                else:
                    source.seek(0)
                    yield from write_member(outer, buffer, path, source)
                    if part.path in records:
                        exported_records[path] = records[part.path]
        if exported_records:
            outer.writestr(
                zipfile.ZipInfo("collection_records.json"), record_document(exported_records)
            )
            yield buffer.drain()
    if on_files is not None:
        on_files(file_names)
    yield buffer.drain()
