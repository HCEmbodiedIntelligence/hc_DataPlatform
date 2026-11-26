import asyncio
import hashlib
import io
import json
from zipfile import ZipFile

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.object_storage.memory import InMemoryObjectStorage
from hc_data_platform.object_storage.providers import OssObjectStorage
from hc_data_platform.storage import export_archive
from hc_data_platform.storage.local_cache import CacheRoutingBucket
from hc_data_platform.storage.oss_prefix import PrefixedOssBucket
from tests.ingest.test_adapters import FakeOssBucket, OssPart


class ExportBucket(FakeOssBucket):
    bucket_name = "managed-datasets"

    def upload_part(self, key, upload_id, number, part, headers):
        body = part.read()
        assert len(body) == int(headers["Content-Length"])
        self.uploads[key, upload_id][number] = body
        return OssPart(number, body)


async def consume(response):
    return [json.loads(line) async for line in response.body_iterator]


def chunks(body):
    yield body[:5]
    yield body[5:]


def test_export_writes_real_object_under_configured_prefix_and_supports_repeated_reads(monkeypatch):
    monkeypatch.setattr(export_archive, "PART_SIZE", 32)
    # Actual adapter and production cache routing with an in-process OSS transport.
    bucket = ExportBucket()
    storage = OssObjectStorage(
        CacheRoutingBucket(PrefixedOssBucket(bucket, "managed"), None),
        part_info_factory=lambda number, etag, size: (number, etag, size),
    )
    zipped = io.BytesIO()
    with ZipFile(zipped, "w") as archive:
        archive.writestr("episode/a.bin", b"data")
    body = zipped.getvalue()
    response = export_archive.publish_archive(
        storage,
        ("org", "project", "cn"),
        "dataset.zip",
        chunks(body),
        expected_sha256=hashlib.sha256(body).hexdigest(),
    )
    events = asyncio.run(consume(response))
    uri = events[-1]["oss_uri"]
    assert events[0] == {"uploaded_bytes": 32}
    assert uri.startswith("oss://managed-datasets/managed/exports/org/project/cn/")
    assert uri.endswith("/dataset.zip") and "?" not in uri
    assert events[-1]["uploaded_bytes"] == len(body)
    for _ in range(2):
        assert b"".join(storage.read_chunks(uri)) == body
    assert not bucket.uploads


def test_failed_or_cancelled_upload_leaves_no_incomplete_object(monkeypatch):
    monkeypatch.setattr(export_archive, "PART_SIZE", 2)
    storage = InMemoryObjectStorage()
    events = export_archive.upload_events(storage, "k", "oss://b/k", chunks(b"broken"), "wrong")
    assert "error" in json.loads(list(events)[-1])
    assert not storage.objects and not storage._uploads
    events = export_archive.upload_events(storage, "k", "oss://b/k", chunks(b"cancel"))
    assert "uploaded_bytes" in json.loads(next(events))
    events.close()
    assert not storage.objects and not storage._uploads


def test_other_providers_cannot_issue_oss_addresses():
    with pytest.raises(ProblemException) as exc:
        export_archive.publish_archive(
            InMemoryObjectStorage(), ("o", "p", "r"), "data.zip", chunks(b"")
        )
    assert exc.value.problem.code == "OSS_EXPORT_UNAVAILABLE"

def test_complete_export_reads_multiple_oss_listparts_pages():
    # oss2 parses the XML NextPartNumberMarker into ListPartsResult.next_marker.
    from types import SimpleNamespace

    class PaginatedBucket(ExportBucket):
        def list_parts(self, key, upload_id, *, marker, max_parts):
            entries = self.uploads[key, upload_id]
            numbers = [number for number in sorted(entries) if number > int(marker)][:max_parts]
            return SimpleNamespace(
                parts=[OssPart(number, entries[number]) for number in numbers],
                is_truncated=numbers[-1] < max(entries),
                next_marker=str(numbers[-1]),
            )

    bucket = PaginatedBucket()
    storage = OssObjectStorage(bucket, part_info_factory=lambda number, etag, size: (number, etag, size))
    key = "exports/scoped/archive.zip"
    upload = storage.create_multipart(key)
    receipts = []
    for number in range(1, 1002):
        part = storage.upload_part_stream(key, upload, number, io.BytesIO(b"x"), 1)
        receipts.append(export_archive.CompletedPart(number, part.etag))
    assert storage.complete_multipart(key, upload, receipts).size == 1001

@pytest.mark.parametrize(("items", "lanes"), [(50, 1), (51, 4), (1200, 4)])
def test_bundle_parallelism_keeps_part_numbers_and_bytes(monkeypatch, items, lanes):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    gate, filled, lock = Event(), Event(), Lock()
    active = peak = 0

    class MeasuredStorage(InMemoryObjectStorage):
        def upload_part_stream(self, *args):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(active, peak)
                if active == lanes:
                    filled.set()
            try:
                assert gate.wait(4)
                return super().upload_part_stream(*args)
            finally:
                with lock:
                    active -= 1

    storage = MeasuredStorage()
    monkeypatch.setattr(export_archive, "PART_SIZE", 8)
    data = bytes(range(120)) * items
    with ThreadPoolExecutor(max_workers=1) as reader:
        stream = export_archive.upload_events(storage, "test.zip", "oss://test/test.zip",
                                              chunks(data), item_count=items)
        result = reader.submit(list, stream)
        try:
            assert filled.wait(4)
        finally:
            gate.set()
        events = [json.loads(e) for e in result.result()]
    assert peak == lanes
    assert events[-1] == {"uploaded_bytes": len(data), "oss_uri": "oss://test/test.zip"}
    assert b"".join(storage.read_chunks("test.zip")) == data


def test_parallel_failure_aborts_upload_after_workers_exit(monkeypatch):
    from threading import Event
    failed = Event()

    class BrokenStorage(InMemoryObjectStorage):
        def upload_part_stream(self, key, upload, number, stream, size):
            if number == 2:
                failed.set()
                raise OSError("part failed")
            assert failed.wait(3)
            return super().upload_part_stream(key, upload, number, stream, size)

    storage = BrokenStorage()
    monkeypatch.setattr(export_archive, "PART_SIZE", 2)
    events = list(export_archive.upload_events(storage, "failed.zip", "unused", chunks(b"12345678"), item_count=51))
    assert "error" in json.loads(events[-1])
    assert not storage.objects and not storage._uploads
