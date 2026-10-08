from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import pytest

from hc_data_platform.object_storage.artifact_adapters import (
    LocalProjectionArtifactStore,
    S3ProjectionArtifactStore,
)
from hc_data_platform.object_storage.artifacts import StagedArtifactStore


def test_local_artifact_contract_preserves_existing_content(tmp_path: Path) -> None:
    store: StagedArtifactStore = LocalProjectionArtifactStore(tmp_path / "store")
    source = tmp_path / "source"
    source.write_bytes(b"original")
    digest = hashlib.sha256(b"original").hexdigest()
    assert store.publish_file("artifact", source, sha256=digest) == 8
    assert store.publish_file("artifact", source, sha256=digest) == 8
    source.write_bytes(b"different")
    with pytest.raises(RuntimeError, match="collision"):
        store.publish_file("artifact", source, sha256=hashlib.sha256(b"different").hexdigest())
    with store.local_file("artifact", expected_sha256=digest, expected_size=8) as local:
        assert local.read_bytes() == b"original"
    with pytest.raises(ValueError, match="checksum"):
        store.publish_file("bad", source, sha256=digest)
    assert not (tmp_path / "store/bad").exists()
    json_digest, size = store.publish_json("manifest", {"value": "原始"})
    assert size > 0
    assert store.read_json("manifest", expected_sha256=json_digest) == {"value": "原始"}
    with pytest.raises(RuntimeError, match="collision"):
        store.publish_json("manifest", {"value": "changed"})
    store.delete("artifact")
    store.delete("artifact")
    with (
        pytest.raises(FileNotFoundError),
        store.local_file("artifact", expected_sha256=digest, expected_size=8),
    ):
        pytest.fail("deleted artifact was exposed")


class _Client:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.body = io.BytesIO(b"payload")

    def get_object(self, **kwargs: object) -> dict[str, Any]:
        if self.mode == "download_failure":
            raise OSError("download failed")
        return {"Body": self.body, "ContentLength": 7}


@pytest.mark.parametrize("mode", ["download_failure", "too_large", "bad_hash", "consumer_failure"])
def test_remote_artifact_failure_releases_local_copy(tmp_path: Path, mode: str) -> None:
    client = _Client(mode)
    store = S3ProjectionArtifactStore(client, "bucket", tmp_path)
    digest = hashlib.sha256(b"payload").hexdigest() if mode == "consumer_failure" else "0" * 64
    with (
        pytest.raises((OSError, ValueError)),
        store.local_file(
            "key", expected_sha256=digest, expected_size=1 if mode == "too_large" else 7
        ),
    ):
        assert mode == "consumer_failure"
        raise OSError("consumer failed")
    assert list(tmp_path.iterdir()) == []
    if mode != "download_failure":
        assert client.body.closed


def test_json_size_rejection_closes_remote_stream(tmp_path: Path) -> None:
    client = _Client("size_rejection")
    store = S3ProjectionArtifactStore(client, "bucket", tmp_path)
    with pytest.raises(ValueError, match="size"):
        store.read_json("key", expected_sha256="0" * 64, max_bytes=1)
    assert client.body.closed
