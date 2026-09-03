from __future__ import annotations

import io
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, BinaryIO, cast
from uuid import UUID

import pytest

from hc_data_platform.backup.objects import (
    ObjectBackupError,
    ObjectBackupVerifier,
    S3ObjectBackupAdapter,
    S3ObjectRestoreAdapter,
)
from hc_data_platform.backup.postgresql import MaintenanceBackupLease

NOW = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)
RECIPIENT = "age1" + "q" * 58


class _Body(io.BytesIO):
    pass


class FakeVersionedS3:
    def __init__(self, objects: Mapping[str, bytes]) -> None:
        self.versioning = "Enabled"
        self.multipart = False
        self._lock = Lock()
        self._versions: dict[str, tuple[str, bytes]] = {
            key: (f"version-{index:04d}", value)
            for index, (key, value) in enumerate(sorted(objects.items()), start=1)
        }
        self._deleted: set[str] = set()
        self.list_calls = 0
        self.get_calls: list[dict[str, object]] = []
        self.mutate_on_list_call: int | None = None
        self.fail_ranges: set[str] = set()
        self.fail_once: set[tuple[str, str | None]] = set()

    def get_bucket_versioning(self, **kwargs: object) -> Mapping[str, Any]:
        assert kwargs == {"Bucket": "source-bucket"}
        return {"Status": self.versioning}

    def list_multipart_uploads(self, **kwargs: object) -> Mapping[str, Any]:
        assert kwargs["Bucket"] == "source-bucket"
        return {
            "IsTruncated": False,
            "Uploads": [{"Key": "unfinished", "UploadId": "upload-1"}] if self.multipart else [],
        }

    def list_object_versions(self, **kwargs: object) -> Mapping[str, Any]:
        assert kwargs["Bucket"] == "source-bucket"
        prefix = str(kwargs.get("Prefix", ""))
        with self._lock:
            self.list_calls += 1
            if self.mutate_on_list_call == self.list_calls:
                self._versions["scope/changed.bin"] = ("version-changed", b"changed")
            versions = [
                {
                    "Key": key,
                    "VersionId": version,
                    "ETag": f'"etag-{version}"',
                    "Size": len(body),
                    "LastModified": NOW,
                    "IsLatest": True,
                }
                for key, (version, body) in sorted(self._versions.items())
                if key.startswith(prefix) and key not in self._deleted
            ]
            markers = [
                {
                    "Key": key,
                    "VersionId": "delete-version",
                    "LastModified": NOW,
                    "IsLatest": True,
                }
                for key in sorted(self._deleted)
                if key.startswith(prefix)
            ]
        return {
            "IsTruncated": False,
            "Versions": versions,
            "DeleteMarkers": markers,
        }

    def get_object(self, **kwargs: object) -> Mapping[str, Any]:
        request = dict(kwargs)
        self.get_calls.append(request)
        key = str(request["Key"])
        version = str(request["VersionId"])
        expected_version, body = self._versions[key]
        assert version == expected_version
        raw_range = request.get("Range")
        range_key = None if raw_range is None else str(raw_range)
        failure_key = (key, range_key)
        if range_key in self.fail_ranges:
            raise OSError("injected range failure")
        if failure_key in self.fail_once:
            self.fail_once.remove(failure_key)
            raise OSError("injected transient failure")
        if raw_range is not None:
            start_raw, end_raw = str(raw_range).removeprefix("bytes=").split("-", 1)
            body = body[int(start_raw) : int(end_raw) + 1]
        return {
            "VersionId": version,
            "ETag": f'"etag-{version}"',
            "ContentLength": len(body),
            "Body": _Body(body),
        }


class MalformedVersionListS3(FakeVersionedS3):
    def __init__(self, kind: str) -> None:
        super().__init__({"scope/a.bin": b"payload"})
        self.kind = kind

    def list_object_versions(self, **kwargs: object) -> Mapping[str, Any]:
        response = dict(super().list_object_versions(**kwargs))
        versions = [dict(value) for value in response["Versions"]]
        if self.kind == "outside-prefix":
            versions.append(
                {
                    "Key": "outside/leaked.bin",
                    "VersionId": "version-outside",
                    "ETag": '"etag-version-outside"',
                    "Size": 1,
                    "LastModified": NOW,
                    "IsLatest": True,
                }
            )
        elif self.kind == "naive-time":
            versions[0]["LastModified"] = NOW.replace(tzinfo=None)
        elif self.kind == "boolean-size":
            versions[0]["Size"] = True
        elif self.kind == "bad-etag-quoting":
            versions[0]["ETag"] = '""etag-version-0001""'
        elif self.kind == "bad-latest-flag":
            versions[0]["IsLatest"] = "true"
        elif self.kind == "repeated-cursor":
            versions = []
            response.update(
                {
                    "IsTruncated": True,
                    "NextKeyMarker": "scope/cursor",
                    "NextVersionIdMarker": "version-cursor",
                }
            )
        response["Versions"] = versions
        return response


class PassthroughAge:
    """Unit-only streaming port; real age interoperability has an integration gate."""

    @contextmanager
    def open(self, path: Path, *, cwd: Path) -> Iterator[BinaryIO]:
        assert path.parent == cwd
        if path.exists():
            with path.open("rb") as reader:
                yield reader
            return
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as writer:
            yield writer


class FakeRestoreTarget:
    def __init__(self) -> None:
        self.versioning = "Enabled"
        self.objects: dict[str, tuple[str, bytes, dict[str, str]]] = {}
        self.put_calls = 0

    def get_bucket_versioning(self, **kwargs: object) -> Mapping[str, Any]:
        assert kwargs == {"Bucket": "target-bucket"}
        return {"Status": self.versioning}

    def head_object(self, **kwargs: object) -> Mapping[str, Any]:
        key = str(kwargs["Key"])
        if key not in self.objects:
            raise KeyError(key)
        version, body, metadata = self.objects[key]
        requested_version = kwargs.get("VersionId")
        if requested_version is not None and requested_version != version:
            raise KeyError(str(requested_version))
        return {
            "VersionId": version,
            "ContentLength": len(body),
            "Metadata": metadata,
        }

    def get_object(self, **kwargs: object) -> Mapping[str, Any]:
        key = str(kwargs["Key"])
        version, body, _metadata = self.objects[key]
        assert kwargs["VersionId"] == version
        return {
            "VersionId": version,
            "ContentLength": len(body),
            "Body": _Body(body),
        }

    def put_object(self, **kwargs: object) -> Mapping[str, Any]:
        key = str(kwargs["Key"])
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise RuntimeError("precondition failed")
        source = cast(BinaryIO, kwargs["Body"])
        body = source.read()
        assert isinstance(body, bytes)
        assert len(body) == kwargs["ContentLength"]
        self.put_calls += 1
        version = f"target-version-{self.put_calls:04d}"
        raw_metadata = cast(Mapping[object, object], kwargs["Metadata"])
        metadata = {str(key): str(value) for key, value in raw_metadata.items()}
        self.objects[key] = (version, body, metadata)
        return {"VersionId": version}


def _lease() -> MaintenanceBackupLease:
    return MaintenanceBackupLease(
        environment_id="object-backup-test",
        operation_id="object-backup-operation",
        owner_instance_id=UUID("70707070-7070-4070-8070-707070707070"),
        fencing_token=707,
    )


def _staging(path: Path) -> Path:
    path.mkdir(mode=0o700)
    return path


def _adapter(client: FakeVersionedS3, **kwargs: Any) -> S3ObjectBackupAdapter:
    return S3ObjectBackupAdapter(
        client,
        bucket="source-bucket",
        bucket_reference="object-store:primary",
        sleeper=lambda _: None,
        **kwargs,
    )


def test_snapshot_binds_exact_versions_and_verifies_inventory(tmp_path: Path) -> None:
    client = FakeVersionedS3(
        {
            "scope/b.bin": b"second-object",
            "scope/a.bin": b"first-object",
            "outside/ignored.bin": b"not-in-scope",
        }
    )
    lease_calls: list[MaintenanceBackupLease] = []
    artifact = _adapter(client, max_workers=2).create_snapshot(
        lease=_lease(),
        lease_verifier=lease_calls.append,
        staging_directory=_staging(tmp_path / "snapshot"),
        prefix="scope/",
    )

    assert len(lease_calls) == 2
    assert artifact.inventory.object_count == 2
    assert artifact.inventory.total_bytes == len(b"first-objectsecond-object")
    assert artifact.inventory.path.stat().st_mode & 0o077 == 0
    assert all(call["VersionId"] != "null" for call in client.get_calls)
    assert all(str(call["Key"]).startswith("scope/") for call in client.get_calls)
    report = ObjectBackupVerifier().verify(artifact)
    assert report.mode == "snapshot"
    assert report.object_count == 2
    assert report.verified_part_count == 0


def test_snapshot_rejects_stale_fence_before_artifact(tmp_path: Path) -> None:
    client = FakeVersionedS3({"scope/a.bin": b"payload"})
    staging = _staging(tmp_path / "stale")

    def reject(_lease: MaintenanceBackupLease) -> None:
        raise RuntimeError("stale")

    with pytest.raises(ObjectBackupError) as captured:
        _adapter(client).create_snapshot(
            lease=_lease(),
            lease_verifier=reject,
            staging_directory=staging,
            prefix="scope/",
        )
    assert captured.value.code == "BACKUP_OBJECT_FENCE_REJECTED"
    assert not (staging / "objects" / "inventory.jsonl.zst").exists()
    assert client.get_calls == []


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        (
            lambda client: setattr(client, "versioning", "Suspended"),
            "BACKUP_OBJECT_VERSIONING_REQUIRED",
        ),
        (lambda client: setattr(client, "multipart", True), "BACKUP_OBJECT_MULTIPART_INCOMPLETE"),
        (
            lambda client: setattr(client, "mutate_on_list_call", 2),
            "BACKUP_OBJECT_VERSION_SET_CHANGED",
        ),
    ],
)
def test_snapshot_fails_closed_on_unstable_source(
    tmp_path: Path,
    setup: Any,
    code: str,
) -> None:
    client = FakeVersionedS3({"scope/a.bin": b"payload"})
    setup(client)
    with pytest.raises(ObjectBackupError) as captured:
        _adapter(client).create_snapshot(
            lease=_lease(),
            lease_verifier=lambda _: None,
            staging_directory=_staging(tmp_path / code.lower()),
            prefix="scope/",
        )
    assert captured.value.code == code


def test_snapshot_retries_transient_exact_version_reads(tmp_path: Path) -> None:
    client = FakeVersionedS3({"scope/a.bin": b"payload"})
    client.fail_once.add(("scope/a.bin", None))

    artifact = _adapter(client, retry_attempts=2).create_snapshot(
        lease=_lease(),
        lease_verifier=lambda _: None,
        staging_directory=_staging(tmp_path / "retry"),
        prefix="scope/",
    )

    assert artifact.inventory.object_count == 1
    assert len(client.get_calls) == 2


@pytest.mark.parametrize(
    "kind",
    [
        "outside-prefix",
        "naive-time",
        "boolean-size",
        "bad-etag-quoting",
        "bad-latest-flag",
        "repeated-cursor",
    ],
)
def test_snapshot_rejects_malformed_provider_responses(tmp_path: Path, kind: str) -> None:
    client = MalformedVersionListS3(kind)

    with pytest.raises(ObjectBackupError) as captured:
        _adapter(client).create_snapshot(
            lease=_lease(),
            lease_verifier=lambda _: None,
            staging_directory=_staging(tmp_path / kind),
            prefix="scope/",
        )

    assert captured.value.code == "BACKUP_OBJECT_SOURCE_RESPONSE_INVALID"
    assert client.get_calls == []


def test_portable_parts_have_hard_bound_and_round_trip_content(tmp_path: Path) -> None:
    body = bytes(range(256)) * 1_300
    client = FakeVersionedS3({"scope/large.bin": body, "scope/empty.bin": b""})
    crypto = PassthroughAge()
    artifact = _adapter(client, max_workers=3).create_portable(
        lease=_lease(),
        lease_verifier=lambda _: None,
        staging_directory=_staging(tmp_path / "portable"),
        encryptor=crypto,
        age_recipient=RECIPIENT,
        prefix="scope/",
        max_part_bytes=256 * 1024,
    )

    assert len(artifact.parts) == 4
    assert all(part.size_bytes <= 256 * 1024 for part in artifact.parts)
    assert all(part.path.stat().st_mode & 0o077 == 0 for part in artifact.parts)
    assert not (tmp_path / "portable" / ".hc-object-portable-checkpoint.json").exists()
    report = ObjectBackupVerifier().verify(artifact, decryptor=crypto)
    assert report.mode == "portable"
    assert report.object_count == 2
    assert report.total_bytes == len(body)
    assert report.verified_part_count == 4

    artifact.parts[0].path.unlink()
    with pytest.raises(ObjectBackupError) as missing:
        ObjectBackupVerifier().verify(artifact, decryptor=crypto)
    assert missing.value.code == "BACKUP_OBJECT_PART_MISSING"


def test_portable_verifier_rejects_wrong_part_hash(tmp_path: Path) -> None:
    client = FakeVersionedS3({"scope/a.bin": b"payload" * 4096})
    crypto = PassthroughAge()
    artifact = _adapter(client).create_portable(
        lease=_lease(),
        lease_verifier=lambda _: None,
        staging_directory=_staging(tmp_path / "tamper"),
        encryptor=crypto,
        age_recipient=RECIPIENT,
        prefix="scope/",
        max_part_bytes=256 * 1024,
    )
    part = artifact.parts[0]
    with part.path.open("r+b") as stream:
        stream.seek(-1, os.SEEK_END)
        original = stream.read(1)
        stream.seek(-1, os.SEEK_END)
        stream.write(bytes([original[0] ^ 1]))

    with pytest.raises(ObjectBackupError) as captured:
        ObjectBackupVerifier().verify(artifact, decryptor=crypto)
    assert captured.value.code == "BACKUP_OBJECT_PART_HASH_MISMATCH"


def test_portable_resume_reuses_checkpointed_parts(tmp_path: Path) -> None:
    body = bytes(range(251)) * 2_000
    client = FakeVersionedS3({"scope/large.bin": body})
    client.fail_ranges.add("bytes=131072-262143")
    staging = _staging(tmp_path / "resume")
    adapter = _adapter(client, max_workers=3, retry_attempts=1)
    crypto = PassthroughAge()

    with pytest.raises(ObjectBackupError) as interrupted:
        adapter.create_portable(
            lease=_lease(),
            lease_verifier=lambda _: None,
            staging_directory=staging,
            encryptor=crypto,
            age_recipient=RECIPIENT,
            prefix="scope/",
            max_part_bytes=256 * 1024,
        )
    assert interrupted.value.code == "BACKUP_OBJECT_PORTABLE_CREATE_FAILED"
    checkpoint = staging / ".hc-object-portable-checkpoint.json"
    assert checkpoint.exists()
    assert checkpoint.stat().st_mode & 0o077 == 0
    completed_before = tuple((staging / "objects" / "parts").glob("*.age"))
    assert completed_before

    client.fail_ranges.clear()
    artifact = adapter.create_portable(
        lease=_lease(),
        lease_verifier=lambda _: None,
        staging_directory=staging,
        encryptor=crypto,
        age_recipient=RECIPIENT,
        prefix="scope/",
        max_part_bytes=256 * 1024,
    )

    assert artifact.resumed_part_count == len(completed_before)
    assert not checkpoint.exists()
    assert ObjectBackupVerifier().verify(artifact, decryptor=crypto).total_bytes == len(body)


def test_snapshot_restore_writes_exact_versions_and_resumes_without_overwrite(
    tmp_path: Path,
) -> None:
    source = FakeVersionedS3(
        {
            "scope/b.bin": b"second-object",
            "scope/a.bin": b"first-object",
        }
    )
    staging = _staging(tmp_path / "snapshot-restore")
    artifact = _adapter(source).create_snapshot(
        lease=_lease(),
        lease_verifier=lambda _: None,
        staging_directory=staging,
        prefix="scope/",
    )
    target = FakeRestoreTarget()
    restore = S3ObjectRestoreAdapter(
        source,
        target,
        source_bucket="source-bucket",
        source_bucket_reference="object-store:primary",
        target_bucket="target-bucket",
        target_bucket_reference="object-store:restore",
        target_prefix="rehearsal/restore-001",
        restore_plan_id="restore-plan-001",
    )

    first = restore.restore(
        artifact,
        source_prefix="scope/",
        staging_directory=staging,
    )
    second = restore.restore(
        artifact,
        source_prefix="scope/",
        staging_directory=staging,
    )

    assert first.object_count == 2
    assert first.resumed_object_count == 0
    assert second.resumed_object_count == 2
    assert first.target_version_set_sha256 == second.target_version_set_sha256
    assert target.put_calls == 2
    assert target.objects["rehearsal/restore-001/a.bin"][1] == b"first-object"
    assert target.objects["rehearsal/restore-001/b.bin"][1] == b"second-object"
    assert tuple((staging / "restore-objects").iterdir()) == ()


def test_restored_inventory_verification_is_full_and_has_no_write_path(tmp_path: Path) -> None:
    source = FakeVersionedS3({"scope/a.bin": b"first", "scope/b.bin": b"second"})
    staging = _staging(tmp_path / "snapshot-reconcile")
    artifact = _adapter(source).create_snapshot(
        lease=_lease(),
        lease_verifier=lambda _: None,
        staging_directory=staging,
        prefix="scope/",
    )
    target = FakeRestoreTarget()
    restore = S3ObjectRestoreAdapter(
        source,
        target,
        source_bucket="source-bucket",
        source_bucket_reference="object-store:primary",
        target_bucket="target-bucket",
        target_bucket_reference="object-store:restore",
        target_prefix="rehearsal/reconcile-001",
        restore_plan_id="restore-plan-reconcile-001",
    )
    restored = restore.restore(
        artifact,
        source_prefix="scope/",
        staging_directory=staging,
    )
    puts_before = target.put_calls

    verified = restore.verify_restored(artifact, source_prefix="scope/")

    assert verified.object_count == 2
    assert verified.resumed_object_count == 2
    assert verified.total_bytes == restored.total_bytes
    assert verified.target_version_set_sha256 == restored.target_version_set_sha256
    assert target.put_calls == puts_before

    version, _body, metadata = target.objects["rehearsal/reconcile-001/a.bin"]
    target.objects["rehearsal/reconcile-001/a.bin"] = (version, b"tampered", metadata)
    with pytest.raises(ObjectBackupError) as tampered:
        restore.verify_restored(artifact, source_prefix="scope/")
    assert tampered.value.code == "RESTORE_OBJECT_TARGET_CONFLICT"
    assert target.put_calls == puts_before


def test_external_reuse_stream_verifies_source_versions_without_writes(tmp_path: Path) -> None:
    source = FakeVersionedS3({"scope/a.bin": b"external-source"})
    staging = _staging(tmp_path / "external-reuse")
    artifact = _adapter(source).create_snapshot(
        lease=_lease(),
        lease_verifier=lambda _: None,
        staging_directory=staging,
        prefix="scope/",
    )
    target = FakeRestoreTarget()
    restore = S3ObjectRestoreAdapter(
        source,
        target,
        source_bucket="source-bucket",
        source_bucket_reference="object-store:primary",
        target_bucket="target-bucket",
        target_bucket_reference="object-store:primary",
        target_prefix="scope",
        restore_plan_id="restore-plan-external-001",
    )

    verified = restore.verify_external(artifact, source_prefix="scope/")

    assert verified.object_count == 1
    assert verified.total_bytes == len(b"external-source")
    assert verified.resumed_object_count == 1
    assert target.put_calls == 0
    assert source.get_calls[-1]["VersionId"] == "version-0001"

    source._versions["scope/a.bin"] = ("version-0001", b"tampered-source")
    with pytest.raises(ObjectBackupError) as tampered:
        restore.verify_external(artifact, source_prefix="scope/")
    assert tampered.value.code == "RESTORE_OBJECT_CONTENT_MISMATCH"
    assert target.put_calls == 0


def test_portable_restore_reconstructs_without_reading_source_bucket(tmp_path: Path) -> None:
    body = bytes(range(256)) * 1_300
    source = FakeVersionedS3({"scope/large.bin": body})
    crypto = PassthroughAge()
    staging = _staging(tmp_path / "portable-restore")
    artifact = _adapter(source).create_portable(
        lease=_lease(),
        lease_verifier=lambda _: None,
        staging_directory=staging,
        encryptor=crypto,
        age_recipient=RECIPIENT,
        prefix="scope/",
        max_part_bytes=256 * 1024,
    )
    source.get_calls.clear()
    target = FakeRestoreTarget()

    report = S3ObjectRestoreAdapter(
        source,
        target,
        source_bucket="source-bucket",
        source_bucket_reference="object-store:primary",
        target_bucket="target-bucket",
        target_bucket_reference="object-store:restore",
        target_prefix="rehearsal/restore-002",
        restore_plan_id="restore-plan-002",
    ).restore(
        artifact,
        source_prefix="scope/",
        staging_directory=staging,
        decryptor=crypto,
    )

    assert report.object_count == 1
    assert target.objects["rehearsal/restore-002/large.bin"][1] == body
    assert source.get_calls == []


def test_object_restore_rejects_target_conflict_and_source_hash_change_before_put(
    tmp_path: Path,
) -> None:
    source = FakeVersionedS3({"scope/a.bin": b"signed-payload"})
    staging = _staging(tmp_path / "restore-failures")
    artifact = _adapter(source).create_snapshot(
        lease=_lease(),
        lease_verifier=lambda _: None,
        staging_directory=staging,
        prefix="scope/",
    )
    target = FakeRestoreTarget()
    target.objects["rehearsal/restore-003/a.bin"] = (
        "foreign-version",
        b"foreign",
        {},
    )
    restore = S3ObjectRestoreAdapter(
        source,
        target,
        source_bucket="source-bucket",
        source_bucket_reference="object-store:primary",
        target_bucket="target-bucket",
        target_bucket_reference="object-store:restore",
        target_prefix="rehearsal/restore-003",
        restore_plan_id="restore-plan-003",
    )
    with pytest.raises(ObjectBackupError) as conflict:
        restore.restore(
            artifact,
            source_prefix="scope/",
            staging_directory=staging,
        )
    assert conflict.value.code == "RESTORE_OBJECT_TARGET_CONFLICT"
    assert target.put_calls == 0
    assert target.objects["rehearsal/restore-003/a.bin"][1] == b"foreign"

    target.objects.clear()
    source._versions["scope/a.bin"] = ("version-0001", b"changed-source")
    with pytest.raises(ObjectBackupError) as changed:
        restore.restore(
            artifact,
            source_prefix="scope/",
            staging_directory=staging,
        )
    assert changed.value.code == "RESTORE_OBJECT_CONTENT_MISMATCH"
    assert target.put_calls == 0
    assert target.objects == {}
