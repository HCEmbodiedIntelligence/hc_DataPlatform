"""The same publication contract runs against real local and object adapters."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
from threading import Barrier

import pytest

from hc_data_platform.object_storage.immutable import MultipartImmutableWriter, verify_object
from hc_data_platform.object_storage.memory import InMemoryObjectStorage
from hc_data_platform.object_storage.models import ObjectIntegrityError
from hc_data_platform.registry.filesystem_storage import FilesystemRobotModelStorage


@pytest.fixture(params=["memory", "filesystem"])
def storage(request, tmp_path):
    if request.param == "memory":
        return InMemoryObjectStorage()
    return FilesystemRobotModelStorage(tmp_path, signing_secret="immutable-test-only")


def write(storage, body, **kwargs):
    return MultipartImmutableWriter(storage).write_immutable(
        "objects/payload", BytesIO(body), size=len(body), sha256=sha256(body).hexdigest(), **kwargs
    )


@pytest.mark.parametrize("body", [b"", b"a" * (8 * 1024**2 + 3)])
def test_bounded_stream_roundtrip_existing_reuse_and_conflict(storage, body):
    class BoundedStream(BytesIO):
        def read(self, size=-1):
            assert 0 <= size <= 1024**2
            return super().read(size)

    source, progress = BoundedStream(body), []
    receipt = MultipartImmutableWriter(storage).write_immutable(
        "objects/payload",
        source,
        size=len(body),
        sha256=sha256(body).hexdigest(),
        progress=progress.append,
    )
    assert not source.closed
    assert receipt == write(storage, body)
    assert progress[-1] == len(body)
    assert progress == sorted(progress)
    assert b"".join(storage.read_chunks(receipt.key)) == body
    with pytest.raises(ObjectIntegrityError):
        write(storage, b"different")
    assert b"".join(storage.read_chunks(receipt.key)) == body


@pytest.mark.parametrize("actual,expected", [(b"ab", b"abc"), (b"abcd", b"abc"), (b"abd", b"abc")])
def test_bad_source_aborts_before_publication_and_allows_retry(
    storage, monkeypatch, actual, expected
):
    abort, calls = storage.abort_multipart, []

    def record_abort(key, upload):
        calls.append(upload)
        return abort(key, upload)

    monkeypatch.setattr(storage, "abort_multipart", record_abort)
    source = BytesIO(actual)
    with pytest.raises(ObjectIntegrityError):
        MultipartImmutableWriter(storage).write_immutable(
            "objects/payload", source, size=len(expected), sha256=sha256(expected).hexdigest()
        )
    assert len(calls) == 1 and not source.closed
    assert storage.head("objects/payload") is None
    assert write(storage, expected).size == len(expected)


def test_cancel_after_part_upload_aborts_and_keeps_caller_stream_open(storage, monkeypatch):
    uploaded, aborts = [], []
    put, abort = storage.upload_part_stream, storage.abort_multipart

    def put_part(*args):
        result = put(*args)
        uploaded.append(result)
        return result

    def abort_part(*args):
        aborts.append(args)
        return abort(*args)

    def check():
        if uploaded:
            raise InterruptedError("cancelled")

    monkeypatch.setattr(storage, "upload_part_stream", put_part)
    monkeypatch.setattr(storage, "abort_multipart", abort_part)
    with pytest.raises(InterruptedError, match="cancelled"):
        write(storage, b"body", checkpoint=check)
    assert aborts and storage.head("objects/payload") is None
    assert write(storage, b"body").size == 4


def test_lost_successful_completion_response_recovers_by_verified_bytes(storage, monkeypatch):
    complete = storage.complete_multipart

    def lost(*args):
        complete(*args)
        raise ConnectionError("lost response")

    monkeypatch.setattr(storage, "complete_multipart", lost)
    receipt = write(storage, b"body")
    assert receipt == write(storage, b"body")


def test_equal_concurrent_writers_do_not_overwrite_winner(storage, monkeypatch):
    barrier, create = Barrier(2), storage.create_multipart

    def synchronized_create(key):
        result = create(key)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(storage, "create_multipart", synchronized_create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: write(storage, b"same"), range(2)))
    assert results[0] == results[1]


def test_verification_closes_response_on_hash_failure_and_cancellation(storage, monkeypatch):
    receipt = write(storage, b"body")
    read, closed = storage.read_chunks, []

    def observed(key):
        try:
            yield from read(key, chunk_size=1)
        finally:
            closed.append(True)

    monkeypatch.setattr(storage, "read_chunks", observed)
    with pytest.raises(ObjectIntegrityError):
        verify_object(storage, receipt.key, receipt.size, "0" * 64)
    checks = 0

    def cancel():
        nonlocal checks
        checks += 1
        if checks > 1:
            raise InterruptedError("cancel")

    with pytest.raises(InterruptedError):
        verify_object(storage, receipt.key, receipt.size, receipt.sha256, checkpoint=cancel)
    assert closed == [True, True]
