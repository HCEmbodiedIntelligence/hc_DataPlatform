import hashlib
import io
import json
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from hc_data_platform.episode_store.bundle_archive import stream_episode_bundle
from hc_data_platform.episode_store.collection_records import CollectionRecord
from hc_data_platform.lerobot_imports.source_archive import ArchiveFile


def prepared(name, kind, files):
    payload = io.BytesIO()
    with ZipFile(payload, "w") as archive:
        if kind == "mcap_collection_zip/7":
            archive.comment = kind.encode("ascii")
        else:
            archive.writestr("delivery.json", json.dumps({"plan": {"archive_format": kind}}))
        for path, body in files.items():
            archive.writestr(path, body)
    data = payload.getvalue()
    return ArchiveFile(name, name, len(data), hashlib.sha256(data).hexdigest()), data


def test_mcap_bundle_reuses_cached_parts_and_preserves_files(tmp_path):
    files = {
        "collection/episode-000000.mcap": b"first",
        "collection/episode-000001.mcap": b"split",
        "collection/episode-000000-frames.json": b"[]",
        "collection/hc/collection_manifest.json": b"{}",
        "collection_records.json": b"{}",
        "collection/episode-000000-normalization.json": b"{}",
    }
    a, first = prepared("000001-a.zip", "mcap_collection_zip/4", files)
    b, second = prepared(
        "000002-b.zip",
        "mcap_collection_zip/5",
        {**files, "collection/episode-000000.mcap": b"second"},
    )
    c, other = prepared("000003-c.zip", "independent_episode_zip/1", {"episode/other": b"x"})
    data = {a.key: first, b.key: second, c.key: other}
    storage = SimpleNamespace(read_chunks=lambda key, chunk_size: iter([data[key]]))
    record = CollectionRecord(collection_task_id=None, collection_description="pick", uploads=())
    result = stream_episode_bundle(storage, [a, b, c], {a.path: record, c.path: record}, tmp_path)
    with ZipFile(io.BytesIO(b"".join(result))) as outer:
        assert outer.testzip() is None
        assert [n for n in outer.namelist() if n.endswith(".zip")] == [c.path]
        for part in (a, b):
            with ZipFile(io.BytesIO(data[part.key])) as inner:
                for name in inner.namelist():
                    exported = part.path[:-4] + "/" + name
                    if name in {
                        "delivery.json",
                        "collection_records.json",
                        "collection/hc/collection_manifest.json",
                    }:
                        assert exported not in outer.namelist()
                    else:
                        assert outer.read(exported) == inner.read(name)
        assert outer.read(c.path) == other
        assert set(json.loads(outer.read("collection_records.json"))["records"]) == {
            c.path,
        }


def test_mcap_bundle_rejects_damaged_part(tmp_path):
    part, data = prepared("000001-a.zip", "mcap_collection_zip/5", {})
    storage = SimpleNamespace(read_chunks=lambda key, chunk_size: [b"x" + data[1:]])
    with pytest.raises(ValueError, match="receipt"):
        b"".join(stream_episode_bundle(storage, [part], {}, tmp_path))


@pytest.mark.parametrize("kind", ["mcap_collection_zip/6", "mcap_collection_zip/7", "mcap_collection_zip/8", "mcap_collection_zip/9"])
def test_mcap_only_bundle_has_no_platform_files(kind, tmp_path):
    payload = {
        "collection/upload_hash.mcap": b"capture",
        "collection/upload_hash-frames.json": b"[]",
    }
    files = dict(payload)
    if kind.endswith("/6"):
        files.update(
            {"collection_records.json": b"{}", "collection/hc/collection_manifest.json": b"{}"}
        )
    part, data = prepared("000001-a.zip", kind, files)
    storage = SimpleNamespace(read_chunks=lambda key, chunk_size: [data])
    record = CollectionRecord(collection_task_id=None, collection_description="pick", uploads=())
    chunks = stream_episode_bundle(storage, [part], {part.path: record}, tmp_path)
    with ZipFile(io.BytesIO(b"".join(chunks))) as archive:
        assert archive.testzip() is None
        assert {n: archive.read(n) for n in archive.namelist()} == {
            "000001-a/" + key: content for key, content in payload.items()
        }

@pytest.mark.parametrize(("count", "width"), [(50, 1), (51, 4)])
def test_verified_sources_are_bounded_ordered_and_closed_on_cancel(monkeypatch, count, width):
    from contextlib import contextmanager
    from threading import Event, Lock
    from hc_data_platform.episode_store import bundle_archive

    lock, window_filled = Lock(), Event()
    opened, closed = [], []

    @contextmanager
    def checked(storage, part, directory):
        with lock:
            opened.append(part)
            if len(opened) == width:
                window_filled.set()
        try:
            assert window_filled.wait(3)
            yield part
        finally:
            with lock:
                closed.append(part)

    monkeypatch.setattr(bundle_archive, "verified_part", checked)
    sources = bundle_archive.verified_parts(None, list(range(count)), None)
    try:
        assert next(sources) == (0, 0)
        assert sorted(opened) == list(range(width))
    finally:
        sources.close()
    assert sorted(closed) == sorted(opened)
