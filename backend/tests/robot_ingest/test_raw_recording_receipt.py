"""Recovery checks in addition to E's actual object-store receipt replay."""

import hashlib
from types import SimpleNamespace

import pytest

from hc_data_platform.robot_ingest.raw_recording import publish


def test_lost_complete_response_verifies_actual_bytes(tmp_path):
    content = b"immutable recording derivative"
    path = tmp_path / "part"
    path.write_bytes(content)

    class Storage:
        present = False

        def head(self, key):
            return SimpleNamespace(etag="stored") if self.present else None

        def create_multipart(self, key):
            return "multipart"

        def upload_part_stream(self, key, upload, number, stream, length):
            assert stream.read() == content and length == len(content)
            return SimpleNamespace(part_number=number, etag="part")

        def complete_multipart(self, *args):
            self.present = True
            raise ConnectionError("response lost after durable completion")

        def read_chunks(self, key):
            yield content

        def abort_multipart(self, *args):
            raise AssertionError("completed upload cannot be aborted")

    storage = Storage()
    assert (
        publish(
            storage, "key", path, len(content), hashlib.sha256(content).hexdigest(), lambda: None
        ).etag
        == "stored"
    )
    # An existing object with another hash is never silently accepted or overwritten.
    with pytest.raises(ValueError, match="immutable receipt"):
        publish(storage, "key", path, len(content), "0" * 64, lambda: None)
