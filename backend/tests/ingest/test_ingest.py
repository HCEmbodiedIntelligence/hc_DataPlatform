from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from typing import Any

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.models import (
    CollectionJobStatus,
    CompletedPart,
    RolloutManifestV1,
    RolloutStatus,
    UploadObjectStatus,
    UploadStatus,
    manifest_object_key,
    raw_object_key,
)
from hc_data_platform.ingest.persistence import InMemoryIngestPersistence
from hc_data_platform.ingest.ports import (
    InMemoryObjectStorage,
    MultipartPart,
    ObjectMetadata,
    crc64_ecma,
)
from hc_data_platform.ingest.service import UploadSessionService


def manifest_for(
    body: bytes,
    rollout_id: str = "r1",
    *,
    file_size: int | None = None,
    sha256: str | None = None,
    crc64: int | None = None,
) -> RolloutManifestV1:
    start = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)
    return RolloutManifestV1(
        project_id="p1",
        task_id="t1",
        collection_job_id="j1",
        rollout_id=rollout_id,
        sequence_no=1,
        robot_id="robot1",
        start_time=start,
        end_time=start + timedelta(seconds=1),
        expected_topics=["camera"],
        actual_topics=["camera"],
        file_size=len(body) if file_size is None else file_size,
        sha256=hashlib.sha256(body).hexdigest() if sha256 is None else sha256,
        crc64=crc64_ecma(body) if crc64 is None else crc64,
        compression="zstd",
        recorder_version="1.0",
    )


def upload_parts(
    service: UploadSessionService,
    storage: InMemoryObjectStorage,
    manifest: RolloutManifestV1,
    body: bytes,
    *,
    idempotency_key: str = "key",
) -> tuple[Any, list[CompletedPart]]:
    session = service.create_session(
        manifest=manifest,
        region_code="cn-hz",
        idempotency_key=idempotency_key,
    )
    first = storage.upload_part(session.multipart_upload_id, 1, body[:2], key=session.object_key)
    second = storage.upload_part(session.multipart_upload_id, 2, body[2:], key=session.object_key)
    parts = [
        CompletedPart(part_number=first.part_number, etag=first.etag),
        CompletedPart(part_number=second.part_number, etag=second.etag),
    ]
    return session, parts


def assert_problem(exc: pytest.ExceptionInfo[ProblemException], code: str) -> None:
    assert exc.value.problem.code == code


def test_manifest_is_last_commit_marker_and_replay_is_idempotent() -> None:
    body = b"valid-mcap-placeholder"
    manifest = manifest_for(body)
    storage = InMemoryObjectStorage()
    persistence = InMemoryIngestPersistence()
    service = UploadSessionService(storage, persistence)
    session, parts = upload_parts(service, storage, manifest, body)

    service.complete_upload(session.session_id, parts)
    assert storage.head(manifest_object_key(session.object_key)) is None
    event = service.commit_manifest(session_id=session.session_id, manifest=manifest)
    replay = service.commit_manifest(session_id=session.session_id, manifest=manifest)

    assert event == replay
    assert service.get_session(session.session_id).status is UploadStatus.RAW_COMMITTED
    upload_object = persistence.get_upload_object(session.session_id)
    job = persistence.get_collection_job(manifest.project_id, manifest.collection_job_id)
    rollout = persistence.get_rollout(manifest.project_id, manifest.rollout_id)
    assert upload_object is not None
    assert job is not None
    assert rollout is not None
    assert upload_object.status is UploadObjectStatus.COMMITTED
    assert job.status is CollectionJobStatus.COLLECTING
    assert rollout.status is RolloutStatus.RAW_COMMITTED
    assert storage.head(event.manifest_key) is not None
    assert event.object_key == raw_object_key(manifest)


def test_retry_reconciles_manifest_marker_after_persistence_crash() -> None:
    class FailOncePersistence(InMemoryIngestPersistence):
        def __init__(self) -> None:
            super().__init__()
            self.fail = True

        def save_committed(self, event: Any) -> None:
            if self.fail:
                self.fail = False
                raise RuntimeError("simulated database outage after marker write")
            super().save_committed(event)

    body = b"crash-recovery"
    manifest = manifest_for(body)
    storage = InMemoryObjectStorage()
    persistence = FailOncePersistence()
    service = UploadSessionService(storage, persistence)
    session, parts = upload_parts(service, storage, manifest, body)
    service.complete_upload(session.session_id, parts)

    with pytest.raises(RuntimeError, match="database outage"):
        service.commit_manifest(session_id=session.session_id, manifest=manifest)
    marker_key = manifest_object_key(session.object_key)
    assert storage.head(marker_key) is not None

    recovered = service.commit_manifest(session_id=session.session_id, manifest=manifest)
    assert recovered.manifest_key == marker_key
    assert service.get_session(session.session_id).status is UploadStatus.RAW_COMMITTED


def test_persistence_fake_keeps_resume_state_across_service_instances() -> None:
    body = b"interrupted-upload"
    manifest = manifest_for(body)
    storage = InMemoryObjectStorage()
    persistence = InMemoryIngestPersistence()
    first_service = UploadSessionService(storage, persistence)
    session = first_service.create_session(
        manifest=manifest,
        region_code="cn-hz",
        idempotency_key="create",
    )
    first_service.renew_part_authorizations(session.session_id, [1, 2])
    first = storage.upload_part(session.multipart_upload_id, 1, body[:5], key=session.object_key)
    first_service.pause_upload(session.session_id)

    restarted = UploadSessionService(storage, persistence)
    grant = restarted.resume_upload(session.session_id, [2])
    assert grant.session.session_id == session.session_id
    assert grant.session.status is UploadStatus.UPLOADING
    assert grant.parts[0].part_number == 2
    second = storage.upload_part(session.multipart_upload_id, 2, body[5:], key=session.object_key)
    listed = restarted.list_parts(session.session_id)
    assert [part.part_number for part in listed] == [1, 2]

    restarted.complete_upload(
        session.session_id,
        [
            CompletedPart(part_number=1, etag=first.etag),
            CompletedPart(part_number=2, etag=second.etag),
        ],
    )
    assert restarted.commit_manifest(session_id=session.session_id, manifest=manifest).object_key


def test_authorization_renewal_is_short_lived_and_does_not_accept_bytes() -> None:
    manifest = manifest_for(b"body")
    service = UploadSessionService(InMemoryObjectStorage(), authorization_ttl_seconds=120)
    grant = service.create_upload(
        manifest=manifest,
        region_code="cn-hz",
        idempotency_key="create",
        part_numbers=[1, 2],
    )
    assert [part.part_number for part in grant.parts] == [1, 2]
    assert all(part.url.startswith("memory://") for part in grant.parts)
    assert all(
        part.expires_at <= datetime.now(timezone.utc) + timedelta(seconds=121)
        for part in grant.parts
    )

    renewed = service.renew_part_authorizations(grant.session.session_id, [2])
    assert renewed[0].part_number == 2
    assert not hasattr(service, "upload_bytes")


def test_complete_rejects_unsorted_missing_and_wrong_etag_parts() -> None:
    body = b"four-parts"
    manifest = manifest_for(body)

    for expected_code, transform in (
        ("PARTS_NOT_SORTED", lambda parts: list(reversed(parts))),
        ("MULTIPART_PARTS_MISMATCH", lambda parts: parts[:1]),
        (
            "MULTIPART_PARTS_MISMATCH",
            lambda parts: [parts[0], parts[1].model_copy(update={"etag": "wrong"})],
        ),
    ):
        storage = InMemoryObjectStorage()
        service = UploadSessionService(storage)
        session, parts = upload_parts(service, storage, manifest, body)
        with pytest.raises(ProblemException) as captured:
            service.complete_upload(session.session_id, transform(parts))
        assert_problem(captured, expected_code)
        assert service.get_session(session.session_id).status is UploadStatus.UPLOADING


def test_pause_resume_and_cancel_abort_multipart_idempotently() -> None:
    manifest = manifest_for(b"body")
    storage = InMemoryObjectStorage()
    service = UploadSessionService(storage)
    session = service.create_session(
        manifest=manifest,
        region_code="cn-hz",
        idempotency_key="key",
    )
    assert service.pause_upload(session.session_id).status is UploadStatus.PAUSED
    with pytest.raises(ProblemException):
        service.renew_part_authorizations(session.session_id, [1])
    assert service.resume_upload(session.session_id).session.status is UploadStatus.UPLOADING
    assert service.cancel_upload(session.session_id).status is UploadStatus.CANCELLED
    assert service.cancel_upload(session.session_id).status is UploadStatus.CANCELLED
    with pytest.raises(KeyError):
        storage.list_parts(session.object_key, session.multipart_upload_id)


def test_manifest_cannot_commit_before_object_completion() -> None:
    manifest = manifest_for(b"body")
    storage = InMemoryObjectStorage()
    service = UploadSessionService(storage)
    session = service.create_session(
        manifest=manifest,
        region_code="cn-hz",
        idempotency_key="key",
    )
    with pytest.raises(ProblemException) as captured:
        service.commit_manifest(session_id=session.session_id, manifest=manifest)
    assert_problem(captured, "RAW_OBJECT_INCOMPLETE")
    assert storage.head(manifest_object_key(session.object_key)) is None


@pytest.mark.parametrize(
    ("manifest", "expected_code"),
    [
        (manifest_for(b"body", file_size=5), "OBJECT_SIZE_MISMATCH"),
        (manifest_for(b"body", crc64=0), "CRC64_MISMATCH"),
        (manifest_for(b"body", sha256="0" * 64), "SHA256_MISMATCH"),
    ],
)
def test_integrity_failure_never_writes_manifest(
    manifest: RolloutManifestV1,
    expected_code: str,
) -> None:
    body = b"body"
    storage = InMemoryObjectStorage()
    service = UploadSessionService(storage)
    session, parts = upload_parts(service, storage, manifest, body)
    service.complete_upload(session.session_id, parts)

    with pytest.raises(ProblemException) as captured:
        service.commit_manifest(session_id=session.session_id, manifest=manifest)
    assert_problem(captured, expected_code)
    assert service.get_session(session.session_id).status is UploadStatus.FAILED
    assert storage.head(manifest_object_key(session.object_key)) is None


def test_same_rollout_and_hash_reuses_resource_different_hash_conflicts() -> None:
    storage = InMemoryObjectStorage()
    service = UploadSessionService(storage)
    first = manifest_for(b"first")
    original = service.create_session(
        manifest=first,
        region_code="cn-hz",
        idempotency_key="first",
    )
    retry = service.create_session(
        manifest=first.model_copy(update={"expected_topics": ["corrected-metadata"]}),
        region_code="cn-hz",
        idempotency_key="retry",
    )
    assert retry.session_id == original.session_id
    assert retry.multipart_upload_id == original.multipart_upload_id

    with pytest.raises(ProblemException) as captured:
        service.create_session(
            manifest=manifest_for(b"second"),
            region_code="cn-hz",
            idempotency_key="different",
        )
    assert_problem(captured, "ROLLOUT_CONTENT_CONFLICT")


def test_concurrent_same_rollout_and_hash_returns_one_persisted_session() -> None:
    class ConcurrentStorage(InMemoryObjectStorage):
        def __init__(self) -> None:
            super().__init__()
            self.barrier = Barrier(2)

        def create_multipart(self, key: str) -> str:
            upload_id = super().create_multipart(key)
            self.barrier.wait(timeout=5)
            return upload_id

    manifest = manifest_for(b"body")
    storage = ConcurrentStorage()
    persistence = InMemoryIngestPersistence()
    services = [
        UploadSessionService(storage, persistence),
        UploadSessionService(storage, persistence),
    ]

    def create(index: int) -> Any:
        return services[index].create_session(
            manifest=manifest,
            region_code="cn-hz",
            idempotency_key=f"concurrent-{index}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        sessions = list(executor.map(create, range(2)))
    assert sessions[0].session_id == sessions[1].session_id
    assert sessions[0].multipart_upload_id == sessions[1].multipart_upload_id


def test_collection_job_sequence_identifies_one_rollout() -> None:
    service = UploadSessionService(InMemoryObjectStorage())
    service.create_session(
        manifest=manifest_for(b"first", rollout_id="r1"),
        region_code="cn-hz",
        idempotency_key="first",
    )
    with pytest.raises(ProblemException) as captured:
        service.create_session(
            manifest=manifest_for(b"second", rollout_id="r2"),
            region_code="cn-hz",
            idempotency_key="second",
        )
    assert_problem(captured, "ROLLOUT_SEQUENCE_CONFLICT")


class SparseStreamingStorage:
    def __init__(self, chunk: bytes, chunk_count: int, manifest: RolloutManifestV1) -> None:
        self.chunk = chunk
        self.chunk_count = chunk_count
        self.manifest = manifest
        self.key: str | None = None
        self.completed = False
        self.marker: bytes | None = None
        self.max_yielded = 0

    def create_multipart(self, key: str) -> str:
        self.key = key
        return "durable-upload-id"

    def presign_part(self, key: str, upload_id: str, part_number: int, expires_seconds: int) -> str:
        return f"https://storage.invalid/{part_number}?uploadId={upload_id}"

    def list_parts(self, key: str, upload_id: str) -> list[MultipartPart]:
        return [MultipartPart(part_number=1, etag="etag-1", size=self.manifest.file_size)]

    def complete_multipart(
        self,
        key: str,
        upload_id: str,
        parts: Sequence[CompletedPart],
    ) -> ObjectMetadata:
        self.completed = True
        return self._raw_metadata(key)

    def abort_multipart(self, key: str, upload_id: str) -> None:
        self.completed = False

    def head(self, key: str) -> ObjectMetadata | None:
        if key.endswith("rollout_manifest.json"):
            if self.marker is None:
                return None
            return ObjectMetadata(key=key, size=len(self.marker), crc64=None, etag="marker")
        return self._raw_metadata(key) if self.completed else None

    def read_chunks(self, key: str, chunk_size: int = 8 * 1024 * 1024) -> Iterable[bytes]:
        if key.endswith("rollout_manifest.json"):
            assert self.marker is not None
            yield self.marker
            return
        for _ in range(self.chunk_count):
            self.max_yielded = max(self.max_yielded, len(self.chunk))
            yield self.chunk

    def put_json(self, key: str, value: dict[str, Any], *, if_none_match: bool) -> ObjectMetadata:
        self.marker = json_bytes(value)
        return ObjectMetadata(key=key, size=len(self.marker), crc64=None, etag="marker")

    def _raw_metadata(self, key: str) -> ObjectMetadata:
        return ObjectMetadata(
            key=key,
            size=self.manifest.file_size,
            crc64=self.manifest.crc64,
            etag="raw",
        )


def json_bytes(value: dict[str, Any]) -> bytes:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def test_sha_and_crc_validation_streams_without_whole_object_read() -> None:
    chunk = bytes(range(256)) * 4096  # 1 MiB yielded at a time
    chunk_count = 16
    sha256 = hashlib.sha256()
    crc64 = 0
    for _ in range(chunk_count):
        sha256.update(chunk)
        crc64 = crc64_ecma(chunk, crc64)
    manifest = manifest_for(
        b"placeholder",
        file_size=len(chunk) * chunk_count,
        sha256=sha256.hexdigest(),
        crc64=crc64,
    )
    storage = SparseStreamingStorage(chunk, chunk_count, manifest)
    service = UploadSessionService(storage)
    session = service.create_session(
        manifest=manifest,
        region_code="cn-hz",
        idempotency_key="stream",
    )
    service.complete_upload(
        session.session_id,
        [CompletedPart(part_number=1, etag="etag-1")],
    )

    service.commit_manifest(session_id=session.session_id, manifest=manifest)
    assert storage.max_yielded == 1024 * 1024
    assert storage.max_yielded < manifest.file_size


def test_sparse_20gb_control_plane_issues_direct_urls_without_allocating_body() -> None:
    size_20gb = 20 * 1024**3
    manifest = manifest_for(
        b"placeholder",
        file_size=size_20gb,
        sha256="a" * 64,
        crc64=0,
    )
    storage = InMemoryObjectStorage()
    service = UploadSessionService(storage)
    grant = service.create_upload(
        manifest=manifest,
        region_code="cn-hz",
        idempotency_key="20gb",
        part_numbers=[1, 160, 320],
    )
    assert grant.session.expected_size == size_20gb
    assert len(grant.parts) == 3
    assert storage.objects == {}
    assert all(part.url.startswith("memory://") for part in grant.parts)


def test_crc64_matches_oss_crc64_xz_known_vector_and_incremental_semantics() -> None:
    assert crc64_ecma(b"123456789") == 0x995DC9BBDF1939FA
    assert crc64_ecma(b"56789", crc64_ecma(b"1234")) == crc64_ecma(b"123456789")
