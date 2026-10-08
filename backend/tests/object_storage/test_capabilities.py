"""Consumers can replace storage without acquiring unrelated upload methods."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.manifest import ObjectStorageManifestParser
from hc_data_platform.ingest.models import CompletedPart as WireCompletedPart
from hc_data_platform.object_storage.memory import InMemoryObjectStorage
from hc_data_platform.object_storage.models import CompletedPart, ObjectMetadata
from hc_data_platform.object_storage.ports import ObjectReader, ObjectStoragePort
from hc_data_platform.registry.filesystem_storage import FilesystemRobotModelStorage
from tests.ingest.test_ingest import manifest_for


@pytest.fixture(params=["memory", "filesystem"])
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> ObjectStoragePort:
    if request.param == "memory":
        return InMemoryObjectStorage()
    return FilesystemRobotModelStorage(tmp_path, signing_secret="object-contract-test-secret")


def test_transfer_and_read_contract(storage: ObjectStoragePort) -> None:
    key = "assets/recording.bin"
    body = b"first-second"
    assert storage.head(key) is None
    upload = storage.create_multipart(key)
    first = storage.upload_part_stream(key, upload, 1, BytesIO(body[:5]), 5)
    second = storage.upload_part_stream(key, upload, 2, BytesIO(body[5:]), len(body) - 5)
    # Historical HTTP values and neutral values share a read-only protocol;
    # neither adapter imports the HTTP model to consume them.
    completed = storage.complete_multipart(
        key,
        upload,
        [WireCompletedPart(part_number=1, etag=first.etag), CompletedPart(2, second.etag)],
    )
    assert completed.key == key
    assert completed.size == len(body)
    assert b"".join(storage.read_chunks(key, chunk_size=3)) == body
    assert storage.read_range(key, 2, 8) == body[2:8]
    assert storage.read_range(key, 5, 5) == b""
    assert storage.head(key) == completed
    with pytest.raises(ProblemException):
        storage.create_multipart(key)


def test_failed_completion_preserves_parts_for_retry(storage: ObjectStoragePort) -> None:
    key = "assets/retry.bin"
    upload = storage.create_multipart(key)
    part = storage.upload_part_stream(key, upload, 1, BytesIO(b"abc"), 3)
    with pytest.raises(ProblemException) as conflict:
        storage.complete_multipart(key, upload, [CompletedPart(1, "wrong-etag")])
    assert conflict.value.problem.code == "MULTIPART_PARTS_MISMATCH"
    assert storage.head(key) is None
    assert storage.list_parts(key, upload)[0].etag == part.etag
    storage.complete_multipart(key, upload, [CompletedPart(1, part.etag)])
    assert b"".join(storage.read_chunks(key)) == b"abc"


def test_abort_and_immutable_json_contract(storage: ObjectStoragePort) -> None:
    upload = storage.create_multipart("assets/aborted")
    storage.upload_part_stream("assets/aborted", upload, 1, BytesIO(b"x"), 1)
    storage.abort_multipart("assets/aborted", upload)
    storage.abort_multipart("assets/aborted", upload)
    assert storage.head("assets/aborted") is None
    storage.put_json("assets/manifest", {"sample": "first"}, if_none_match=True)
    with pytest.raises(ProblemException):
        storage.put_json("assets/manifest", {"sample": "second"}, if_none_match=True)
    assert json.loads(b"".join(storage.read_chunks("assets/manifest"))) == {"sample": "first"}


def test_manifest_accepts_a_reader_with_no_upload_or_signing_capability() -> None:
    manifest = manifest_for(b"recording")
    body = manifest.model_dump_json().encode()

    class ReadOnlySource:
        def head(self, key: str) -> ObjectMetadata | None:
            return ObjectMetadata(key, len(body), None, "frozen")

        def read_chunks(self, key: str, chunk_size: int = 8 * 1024 * 1024) -> Iterator[bytes]:
            del key
            for offset in range(0, len(body), chunk_size):
                yield body[offset : offset + chunk_size]

    reader: ObjectReader = ReadOnlySource()
    result = ObjectStorageManifestParser(reader).parse("manifest.json")
    assert result.identifiers.data_package_id == manifest.data_package_id


def test_contract_import_has_no_upload_database_or_sdk_side_effects() -> None:
    code = """
import sys
from hc_data_platform.object_storage.ports import ObjectReader
from hc_data_platform.object_storage.models import CompletedPart
for prefix in ('hc_data_platform.ingest', 'hc_data_platform.runtime',
               'hc_data_platform.object_storage.memory',
               'hc_data_platform.object_storage.providers',
               'boto3', 'oss2', 'psycopg', 'fastapi'):
    assert not any(n == prefix or n.startswith(prefix + '.') for n in sys.modules), prefix
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
