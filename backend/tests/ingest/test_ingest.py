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
    FailedPartV1,
    HuggingFaceEpisodeSourceV1,
    IdempotencyOutcome,
    IngestTriggerStatus,
    ManifestCameraV1,
    ManifestFileV1,
    ManifestTopicV1,
    RolloutManifestV1,
    RolloutStatus,
    UploadObjectStatus,
    UploadPartStatus,
    UploadSession,
    UploadSourceType,
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
    resolved_size = len(body) if file_size is None else file_size
    resolved_sha = hashlib.sha256(body).hexdigest() if sha256 is None else sha256
    resolved_crc = crc64_ecma(body) if crc64 is None else crc64
    return RolloutManifestV1(
        project_id="p1",
        task_id="t1",
        collection_job_id="j1",
        rollout_id=rollout_id,
        collection_session_id="session1",
        recording_request_id=f"request-{rollout_id}",
        data_package_id=f"package-{rollout_id}",
        pico_instance_id="pico1",
        sequence_no=1,
        robot_id="robot1",
        start_time=start,
        end_time=start + timedelta(seconds=1),
        cameras=[ManifestCameraV1(camera_id="front", topic="/camera")],
        topics=[ManifestTopicV1(name="/camera", required=True)],
        expected_topics=["/camera"],
        actual_topics=["/camera"],
        files=[
            ManifestFileV1(
                path="recording.mcap",
                size=resolved_size,
                sha256=resolved_sha,
                crc64=resolved_crc,
            )
        ],
        file_size=resolved_size,
        sha256=resolved_sha,
        crc64=resolved_crc,
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


def test_upload_session_listing_uses_scope_bound_cursor_pagination() -> None:
    persistence = InMemoryIngestPersistence()
    service = UploadSessionService(
        InMemoryObjectStorage(),
        persistence,
        cursor_secret="test-ingest-session-pagination-secret",
    )
    updated_at = datetime(2026, 8, 21, 8, tzinfo=timezone.utc)
    for index in range(3):
        persistence.save_session(
            UploadSession(
                session_id=f"00000000-0000-0000-0000-{index + 1:012d}",
                project_id="p1",
                region_code="cn-hz",
                rollout_id=f"rollout-{index}",
                data_package_id=f"package-{index}",
                object_key=f"raw/v1/package-{index}.mcap",
                expected_sha256="a" * 64,
                expected_size=1,
                expected_crc64="1",
                manifest_fingerprint="b" * 64,
                updated_at=updated_at - timedelta(seconds=index),
            )
        )

    first = service.list_sessions("p1", "cn-hz", limit=1)
    assert first.total == 1
    assert [item.data_package_id for item in first.items] == ["package-0"]
    assert first.next_cursor is not None

    second = service.list_sessions("p1", "cn-hz", cursor=first.next_cursor, limit=1)
    third = service.list_sessions("p1", "cn-hz", cursor=second.next_cursor, limit=1)
    assert [item.data_package_id for item in second.items] == ["package-1"]
    assert [item.data_package_id for item in third.items] == ["package-2"]
    assert third.next_cursor is None

    with pytest.raises(ProblemException) as wrong_filter:
        service.list_sessions(
            "p1",
            "cn-hz",
            cursor=first.next_cursor,
            data_package_id="package-0",
            limit=1,
        )
    assert_problem(wrong_filter, "INVALID_CURSOR")


def test_manifest_is_last_commit_marker_and_replay_is_idempotent() -> None:
    body = b"valid-mcap-placeholder"
    manifest = manifest_for(body)
    storage = InMemoryObjectStorage()
    persistence = InMemoryIngestPersistence()
    service = UploadSessionService(storage, persistence)
    session, parts = upload_parts(service, storage, manifest, body)

    service.complete_upload(session.session_id, parts)
    assert storage.head(manifest_object_key(session.object_key)) is None
    event = service.commit_manifest(
        organization_id="org-a", session_id=session.session_id, manifest=manifest
    )
    replay = service.commit_manifest(
        organization_id="org-a", session_id=session.session_id, manifest=manifest
    )

    assert event == replay
    assert event.workflow is not None
    assert event.workflow.status is IngestTriggerStatus.PENDING
    assert event.workflow.workflow_id == "ingest-rollout:v1:p1:cn-hz%2Fr1"
    assert service.get_session(session.session_id).workflow == event.workflow
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
    raw_source_id = f"upload-{session.session_id.replace('-', '')}"
    raw_source = service.raw_sources.get_source(
        organization_id="org-a",
        project_id="p1",
        region_code="cn-hz",
        raw_source_id=raw_source_id,
    )
    assert raw_source is not None
    assert raw_source.source_format.value == "MCAP"
    assert raw_source.manifest_key == event.manifest_key
    assert raw_source.content_hash == manifest.sha256
    assert raw_source.processing_status.value == "PENDING"
    raw_episodes = service.raw_sources.list_episodes(
        organization_id="org-a",
        project_id="p1",
        region_code="cn-hz",
        raw_source_id=raw_source_id,
    )
    assert [(item.episode_id, item.source_episode_index) for item in raw_episodes] == [
        (manifest.rollout_id, 0)
    ]
    raw_job = service.raw_sources.get_job(
        organization_id="org-a",
        project_id="p1",
        region_code="cn-hz",
        raw_source_id=raw_source_id,
    )
    assert raw_job is not None
    assert raw_job.job_type.value == "DIRECT_EPISODE_INGEST"
    assert raw_job.adapter_name == "mcap"


def test_raw_media_access_requires_a_committed_object_and_records_a_redacted_audit() -> None:
    body = b"valid-mcap-placeholder"
    manifest = manifest_for(body, rollout_id="raw-media")
    storage = InMemoryObjectStorage()
    persistence = InMemoryIngestPersistence()
    service = UploadSessionService(storage, persistence, raw_media_authorization_ttl_seconds=120)
    session, parts = upload_parts(service, storage, manifest, body)

    with pytest.raises(ProblemException) as before_commit:
        service.authorize_raw_media(
            session_id=session.session_id,
            actor_id="operator-1",
            request_id="request-raw-media",
        )
    assert_problem(before_commit, "RAW_MEDIA_NOT_COMMITTED")
    assert persistence.raw_media_audit_events == []

    service.complete_upload(session.session_id, parts)
    service.commit_manifest(
        organization_id="org-a", session_id=session.session_id, manifest=manifest
    )
    source = service.authorize_raw_media(
        session_id=session.session_id,
        actor_id="operator-1",
        request_id="request-raw-media",
    )

    assert source.format == "MCAP"
    assert source.media_type == "application/x-mcap"
    assert source.byte_length == len(body)
    assert source.sha256 == manifest.sha256
    assert source.download_url.startswith("memory://object/")
    assert source.expires_at > datetime.now(timezone.utc)

    assert len(persistence.raw_media_audit_events) == 1
    audit = persistence.raw_media_audit_events[0]
    assert audit.actor_id == "operator-1"
    assert audit.request_id == "request-raw-media"
    assert audit.byte_length == len(body)
    assert audit.session_id == session.session_id
    assert audit.rollout_id == manifest.rollout_id


def test_manifest_discovery_for_rollout_is_exactly_project_and_region_scoped() -> None:
    body = b"task-bound-manifest-discovery"
    manifest = manifest_for(body, rollout_id="task-bound-rollout")
    service = UploadSessionService(InMemoryObjectStorage(), InMemoryIngestPersistence())
    session = service.create_session(
        manifest=manifest,
        region_code="cn-hz",
        idempotency_key="task-bound-manifest",
    )

    discovery = service.get_manifest_discovery_for_rollout(
        project_id=manifest.project_id,
        region_code="cn-hz",
        rollout_id=manifest.rollout_id,
    )
    assert discovery.robot_id == manifest.robot_id
    assert (
        discovery.model_copy(update={"robot_id": None})
        == service.get_manifest_preflight(session.session_id).discovery
    )

    with pytest.raises(ProblemException) as wrong_region:
        service.get_manifest_discovery_for_rollout(
            project_id=manifest.project_id,
            region_code="us-west",
            rollout_id=manifest.rollout_id,
        )
    assert_problem(wrong_region, "UPLOAD_SESSION_NOT_FOUND")

    with pytest.raises(ProblemException) as wrong_rollout:
        service.get_manifest_discovery_for_rollout(
            project_id=manifest.project_id,
            region_code="cn-hz",
            rollout_id="another-rollout",
        )
    assert_problem(wrong_rollout, "UPLOAD_SESSION_NOT_FOUND")


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
        service.commit_manifest(
            organization_id="org-a", session_id=session.session_id, manifest=manifest
        )
    marker_key = manifest_object_key(session.object_key)
    assert storage.head(marker_key) is not None

    recovered = service.commit_manifest(
        organization_id="org-a", session_id=session.session_id, manifest=manifest
    )
    assert recovered.manifest_key == marker_key
    assert service.get_session(session.session_id).status is UploadStatus.RAW_COMMITTED


def test_retry_reconciles_committed_event_after_status_update_crash() -> None:
    class CrashAfterEventPersistence(InMemoryIngestPersistence):
        def __init__(self) -> None:
            super().__init__()
            self.fail_status_update = True

        def save_session(self, session: Any) -> None:
            if session.status is UploadStatus.RAW_COMMITTED and self.fail_status_update:
                self.fail_status_update = False
                raise RuntimeError("simulated worker crash after committed event")
            super().save_session(session)

    body = b"event-recovery"
    manifest = manifest_for(body)
    storage = InMemoryObjectStorage()
    persistence = CrashAfterEventPersistence()
    service = UploadSessionService(storage, persistence)
    session, parts = upload_parts(service, storage, manifest, body)
    service.complete_upload(session.session_id, parts)

    with pytest.raises(RuntimeError, match="after committed event"):
        service.commit_manifest(
            organization_id="org-a", session_id=session.session_id, manifest=manifest
        )
    assert persistence.get_committed(manifest.project_id, manifest.rollout_id) is None
    assert persistence.get_workflow_trigger(session.session_id) is None
    assert service.get_session(session.session_id).status is UploadStatus.MULTIPART_COMPLETED

    recovered = service.commit_manifest(
        organization_id="org-a", session_id=session.session_id, manifest=manifest
    )
    assert recovered.data_package_id == manifest.data_package_id
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
    assert restarted.commit_manifest(
        organization_id="org-a", session_id=session.session_id, manifest=manifest
    ).object_key


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


def test_authorized_object_reference_uses_canonical_key_and_is_idempotent() -> None:
    body = b"already-in-object-storage"
    manifest = manifest_for(body)
    storage = InMemoryObjectStorage()
    key = raw_object_key(manifest)
    storage.objects[key] = body
    service = UploadSessionService(storage)

    with pytest.raises(ProblemException) as rejected:
        service.create_upload(
            manifest=manifest,
            region_code="cn-hz",
            idempotency_key="bad-reference",
            object_storage_uri=f"memory://object/{key}?client-secret=forbidden",
        )
    assert_problem(rejected, "OBJECT_STORAGE_REFERENCE_INVALID")

    grant = service.create_upload(
        manifest=manifest,
        region_code="cn-hz",
        idempotency_key="object-reference",
        object_storage_uri=f"memory://object/{key}",
    )
    assert grant.idempotency_outcome is IdempotencyOutcome.CREATED
    assert grant.parts == []
    assert grant.session.source_type is UploadSourceType.OBJECT_STORAGE_REFERENCE
    assert grant.session.multipart_upload_id is None
    assert grant.session.status is UploadStatus.MULTIPART_COMPLETED
    discovery = service.get_manifest_preflight(grant.session.session_id)
    assert discovery.identifiers.data_package_id == manifest.data_package_id
    assert discovery.discovery.read_only is True

    service.commit_manifest(
        organization_id="org-a", session_id=grant.session.session_id, manifest=manifest
    )
    replay = service.create_upload(
        manifest=manifest,
        region_code="cn-hz",
        idempotency_key="object-reference-replay",
        object_storage_uri=f"memory://object/{key}",
    )
    assert replay.idempotency_outcome is IdempotencyOutcome.ALREADY_COMMITTED
    assert replay.session.session_id == grant.session.session_id
    with pytest.raises(ProblemException) as source_switch:
        service.create_upload(
            manifest=manifest,
            region_code="cn-hz",
            idempotency_key="forbidden-source-switch",
        )
    assert_problem(source_switch, "UPLOAD_SOURCE_CONFLICT")


def test_failed_part_retry_is_bounded_and_success_clears_failure() -> None:
    body = b"retry-body"
    manifest = manifest_for(body)
    storage = InMemoryObjectStorage()
    service = UploadSessionService(storage, max_part_retries=2)
    session = service.create_session(
        manifest=manifest,
        region_code="cn-hz",
        idempotency_key="retry-create",
    )
    service.renew_part_authorizations(session.session_id, [1])

    retry = service.retry_failed_parts(
        session.session_id,
        [FailedPartV1(part_number=1, failure_code="NETWORK_INTERRUPTED")],
    )
    assert retry[0].part_number == 1
    failed = service.list_parts(session.session_id)[0]
    assert failed.status is UploadPartStatus.AUTHORIZED
    assert failed.retry_count == 1
    assert failed.failure_code == "NETWORK_INTERRUPTED"

    storage.upload_part(session.multipart_upload_id, 1, body, key=session.object_key)
    uploaded = service.list_parts(session.session_id)[0]
    assert uploaded.status is UploadPartStatus.UPLOADED
    assert uploaded.retry_count == 1
    assert uploaded.failure_code is None


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
        service.commit_manifest(
            organization_id="org-a", session_id=session.session_id, manifest=manifest
        )
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
        service.commit_manifest(
            organization_id="org-a", session_id=session.session_id, manifest=manifest
        )
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
        manifest=first,
        region_code="cn-hz",
        idempotency_key="retry",
    )
    assert retry.session_id == original.session_id
    with pytest.raises(ProblemException) as changed:
        service.create_session(
            manifest=first.model_copy(update={"expected_topics": ["/camera/corrected"]}),
            region_code="cn-hz",
            idempotency_key="changed-manifest",
        )
    assert_problem(changed, "MANIFEST_CHANGED")
    assert retry.multipart_upload_id == original.multipart_upload_id

    with pytest.raises(ProblemException) as captured:
        service.create_session(
            manifest=manifest_for(b"second"),
            region_code="cn-hz",
            idempotency_key="different",
        )
    assert_problem(captured, "ROLLOUT_CONTENT_CONFLICT")


def test_same_sha_in_distinct_business_packages_is_retained_separately() -> None:
    body = b"shared-content"
    first_manifest = manifest_for(body, rollout_id="r1")
    second_manifest = first_manifest.model_copy(
        update={
            "rollout_id": "r2",
            "recording_request_id": "request-r2",
            "data_package_id": "package-r2",
            "sequence_no": 2,
        }
    )
    storage = InMemoryObjectStorage()
    service = UploadSessionService(storage)

    first = service.create_session(
        manifest=first_manifest,
        region_code="cn-hz",
        idempotency_key="package-1",
    )
    second = service.create_session(
        manifest=second_manifest,
        region_code="cn-hz",
        idempotency_key="package-2",
    )

    assert first.session_id != second.session_id
    assert first.expected_sha256 == second.expected_sha256
    assert first.object_key != second.object_key
    assert "package=package-r1" in first.object_key
    assert "package=package-r2" in second.object_key


def test_same_source_episode_with_new_converter_identity_is_rejected() -> None:
    source = HuggingFaceEpisodeSourceV1(
        repository="aractingi/droid_100",
        resolved_revision="e86f5657cac0cd48c509543e4c14c6a31352b0cc",
        episode_index=0,
    )
    first_manifest = manifest_for(b"first", rollout_id="r1").model_copy(
        update={"source_recording": source}
    )
    second_manifest = manifest_for(b"second", rollout_id="r2").model_copy(
        update={
            "collection_job_id": "j2",
            "collection_session_id": "session2",
            "data_package_id": "package-r2-converter-v2",
            "recorder_version": "2.0",
            "source_recording": source.model_copy(update={"resolved_revision": "f" * 40}),
        }
    )
    service = UploadSessionService(InMemoryObjectStorage())

    first = service.create_session(
        manifest=first_manifest,
        region_code="cn-hz",
        idempotency_key="source-v1",
    )
    assert service.preflight_upload_manifest(first_manifest).source_fingerprint is not None
    assert first.session_id

    with pytest.raises(ProblemException) as preflight_rejected:
        service.preflight_upload_manifest(second_manifest)
    assert_problem(preflight_rejected, "SOURCE_RECORDING_DUPLICATE")
    assert (
        preflight_rejected.value.problem.details["existing_data_package_id"]
        == first_manifest.data_package_id
    )

    with pytest.raises(ProblemException) as create_rejected:
        service.create_session(
            manifest=second_manifest,
            region_code="cn-hz",
            idempotency_key="source-v2",
        )
    assert_problem(create_rejected, "SOURCE_RECORDING_DUPLICATE")


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

    service.commit_manifest(
        organization_id="org-a", session_id=session.session_id, manifest=manifest
    )
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
