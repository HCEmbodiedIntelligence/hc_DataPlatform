from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.preview.gc import PreviewGarbageCollector
from hc_data_platform.preview.memory import (
    HmacUrlSigner,
    InMemoryExclusionReader,
    InMemoryMediaEncoder,
    InMemoryPreviewArtifactStore,
    InMemoryPreviewRepository,
    InMemoryStepReader,
)
from hc_data_platform.preview.models import (
    EncodedPreviewArtifactV1,
    PreviewDescriptorV1,
    PreviewFrameV1,
    PreviewPendingDescriptorV1,
    PreviewRequestV1,
    PreviewScopeV1,
    RenderFrameV1,
    ViewMode,
)
from hc_data_platform.preview.service import (
    PreviewControlPlaneService,
    PreviewGenerationService,
    preview_artifact_key,
)

NOW = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)
SCOPE = PreviewScopeV1(
    organization_id="organization-1",
    project_id="project-1",
    region_code="cn-test",
)


def frames(count: int = 6):  # type: ignore[no-untyped-def]
    for index in range(count):
        yield PreviewFrameV1(
            rollout_id="rollout-1",
            step_index=index,
            timestamp_ns=index * 33_333_333,
            source_timestamp_ns=index * 33_333_333,
            image_ref=b"\xff\xd8frame\xff\xd9",
        )


def request(*, revision: int = 1, profile_id: str = "annotation-h264-720p-v1"):
    return PreviewRequestV1(
        project_id="project-1",
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        lance_version="v7",
        annotation_revision=revision,
        camera_id="front",
        view_mode=ViewMode.ORIGINAL,
        profile_id=profile_id,
        frequency_hz=2,
        start_step=0,
        end_step=6,
    )


def services(*, source_count: int = 6):
    repository = InMemoryPreviewRepository()
    store = InMemoryPreviewArtifactStore()
    reader = InMemoryStepReader(list(frames(source_count)))
    encoder = InMemoryMediaEncoder()
    control = PreviewControlPlaneService(
        repository=repository,
        store=store,
        signer=HmacUrlSigner(b"test"),
        clock=lambda: NOW,
    )
    generation = PreviewGenerationService(
        step_reader=reader,
        exclusions=InMemoryExclusionReader(),
        encoder=encoder,
        repository=repository,
        store=store,
        clock=lambda: NOW,
    )
    return control, generation, repository, store, reader, encoder


def authorize(control: PreviewControlPlaneService, value: PreviewRequestV1):
    return asyncio.run(control.create_session(SCOPE, value))


def warm(control: PreviewControlPlaneService, generation: PreviewGenerationService):
    pending = authorize(control, request())
    assert isinstance(pending, PreviewPendingDescriptorV1)
    artifact = generation.generate(SCOPE, request(), job_id=pending.job_id)
    ready = authorize(control, request())
    assert isinstance(ready, PreviewDescriptorV1)
    return artifact, ready


def test_cold_request_is_202_then_media_worker_publishes_ready_artifact() -> None:
    control, generation, _, store, reader, encoder = services()
    pending = authorize(control, request())

    assert isinstance(pending, PreviewPendingDescriptorV1)
    assert pending.status == "QUEUED"
    assert reader.calls == 0
    assert encoder.calls == []

    artifact = generation.generate(SCOPE, request(), job_id=pending.job_id)
    descriptor = authorize(control, request())

    assert artifact.status.value == "READY"
    assert artifact.playlist_key in store.objects
    assert isinstance(descriptor, PreviewDescriptorV1)
    assert descriptor.artifact_key == pending.artifact_key
    assert reader.calls == 1
    assert len(encoder.calls) == 1


def test_ready_artifact_survives_control_plane_reconstruction_without_worker_files() -> None:
    control, generation, repository, store, reader, encoder = services()
    pending = authorize(control, request())
    assert isinstance(pending, PreviewPendingDescriptorV1)
    generation.generate(SCOPE, request(), job_id=pending.job_id)

    restarted_api = PreviewControlPlaneService(
        repository=repository,
        store=store,
        signer=HmacUrlSigner(b"test"),
        clock=lambda: NOW,
    )
    descriptor = authorize(restarted_api, request())

    assert isinstance(descriptor, PreviewDescriptorV1)
    assert descriptor.artifact_key == pending.artifact_key
    assert reader.calls == 1
    assert len(encoder.calls) == 1


def test_original_artifact_identity_ignores_annotation_revision() -> None:
    assert preview_artifact_key(request(revision=1)) == preview_artifact_key(
        request(revision=999)
    )


def test_non_allowlisted_profile_is_rejected_before_a_job_is_created() -> None:
    control, _, repository, _, _, _ = services()

    with pytest.raises(ProblemException) as captured:
        authorize(control, request(profile_id="attacker-8k-lossless-v1"))

    assert captured.value.problem.code == "PREVIEW_PROFILE_NOT_ALLOWED"
    assert repository.storage_totals() == (0, 0)


def test_preview_state_isolated_by_organization_project_and_region() -> None:
    control, _, repository, _, _, _ = services()
    pending = authorize(control, request())
    assert isinstance(pending, PreviewPendingDescriptorV1)
    foreign_scope = PreviewScopeV1(
        organization_id="organization-2",
        project_id=SCOPE.project_id,
        region_code=SCOPE.region_code,
    )

    assert repository.find_artifact(
        foreign_scope, pending.artifact_key, now=NOW
    ) is None
    assert repository.get_job(foreign_scope, pending.job_id) is None


def test_one_thousand_ready_authorizations_never_invoke_an_encoder() -> None:
    control, generation, _, _, reader, encoder = services()
    _, first = warm(control, generation)
    original_encoder_calls = len(encoder.calls)

    with ThreadPoolExecutor(max_workers=64) as executor:
        descriptors = list(
            executor.map(lambda _: authorize(control, request()), range(1_000))
        )

    assert all(isinstance(item, PreviewDescriptorV1) for item in descriptors)
    assert {item.artifact_key for item in descriptors} == {first.artifact_key}
    assert reader.calls == 1
    assert len(encoder.calls) == original_encoder_calls == 1


def test_one_thousand_cold_authorizations_share_one_durable_job() -> None:
    control, _, _, _, reader, encoder = services()

    with ThreadPoolExecutor(max_workers=64) as executor:
        pending = list(executor.map(lambda _: authorize(control, request()), range(1_000)))

    assert all(isinstance(item, PreviewPendingDescriptorV1) for item in pending)
    assert len({item.job_id for item in pending}) == 1
    assert len({item.artifact_key for item in pending}) == 1
    assert reader.calls == 0
    assert encoder.calls == []


def test_session_expiry_releases_active_reference_for_gc() -> None:
    current = [NOW]
    repository = InMemoryPreviewRepository()
    store = InMemoryPreviewArtifactStore()
    control = PreviewControlPlaneService(
        repository=repository,
        store=store,
        signer=HmacUrlSigner(b"test"),
        session_ttl=timedelta(seconds=1),
        clock=lambda: current[0],
    )
    generation = PreviewGenerationService(
        step_reader=InMemoryStepReader(list(frames())),
        exclusions=InMemoryExclusionReader(),
        encoder=InMemoryMediaEncoder(),
        repository=repository,
        store=store,
        clock=lambda: current[0],
    )
    artifact, descriptor = warm(control, generation)
    assert control.get_session(SCOPE, descriptor.session_id).session_id == descriptor.session_id

    current[0] += timedelta(seconds=2)
    assert repository.cleanup_expired_metadata(now=current[0]) == 1
    refreshed = repository.find_artifact(SCOPE, artifact.artifact_key, now=current[0])
    assert refreshed is not None
    assert refreshed.active_reference_count == 0


def test_gc_deleting_artifact_cannot_race_a_new_media_publication() -> None:
    class RecordingQueue:
        def __init__(self) -> None:
            self.jobs = []

        async def enqueue(self, job):  # type: ignore[no-untyped-def]
            self.jobs.append(job)

    repository = InMemoryPreviewRepository()
    store = InMemoryPreviewArtifactStore()
    queue = RecordingQueue()
    control = PreviewControlPlaneService(
        repository=repository,
        store=store,
        signer=HmacUrlSigner(b"test"),
        queue=queue,
        clock=lambda: NOW,
    )
    generation = PreviewGenerationService(
        step_reader=InMemoryStepReader(list(frames())),
        exclusions=InMemoryExclusionReader(),
        encoder=InMemoryMediaEncoder(),
        repository=repository,
        store=store,
        clock=lambda: NOW,
    )
    first = authorize(control, request())
    assert isinstance(first, PreviewPendingDescriptorV1)
    generation.generate(SCOPE, request(), job_id=first.job_id)
    artifact = repository.find_artifact(SCOPE, first.artifact_key, now=NOW)
    assert artifact is not None
    assert repository.mark_deleting(
        SCOPE, artifact.artifact_id, expected_version=artifact.version
    )
    queue.jobs.clear()

    deleting = authorize(control, request())

    assert isinstance(deleting, PreviewPendingDescriptorV1)
    assert deleting.status == "FAILED"
    assert deleting.error_code == "PREVIEW_ARTIFACT_DELETING"
    assert queue.jobs == []
    still_deleting = repository.find_artifact(SCOPE, first.artifact_key, now=NOW)
    assert still_deleting is not None
    assert still_deleting.status.value == "DELETING"


def test_failed_ready_transition_leaves_an_exact_manifest_for_orphan_gc() -> None:
    class FailingReadyRepository(InMemoryPreviewRepository):
        def mark_ready(self, **_: object):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated database transition failure")

    repository = FailingReadyRepository()
    store = InMemoryPreviewArtifactStore()
    control = PreviewControlPlaneService(
        repository=repository,
        store=store,
        signer=HmacUrlSigner(b"test"),
        clock=lambda: NOW,
    )
    generation = PreviewGenerationService(
        step_reader=InMemoryStepReader(list(frames())),
        exclusions=InMemoryExclusionReader(),
        encoder=InMemoryMediaEncoder(),
        repository=repository,
        store=store,
        clock=lambda: NOW,
    )
    pending = authorize(control, request())
    assert isinstance(pending, PreviewPendingDescriptorV1)

    with pytest.raises(RuntimeError, match="transition failure"):
        generation.generate(SCOPE, request(), job_id=pending.job_id)

    assert store.objects
    result = PreviewGarbageCollector(
        repository,
        store,
        project_quota_bytes=10_000,
        global_quota_bytes=10_000,
        clock=lambda: NOW,
    ).run_once()
    assert result.deleted_artifacts == 1
    assert result.failed_artifacts == 0
    assert store.objects == {}


def test_ten_thousand_frames_are_consumed_as_a_stream() -> None:
    class StreamingReader:
        def __init__(self) -> None:
            self.in_flight = 0
            self.peak_in_flight = 0

        def read_steps(self, **_: object):  # type: ignore[no-untyped-def]
            for frame in frames(10_000):
                self.in_flight += 1
                self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
                yield frame
                self.in_flight -= 1

    class StreamingEncoder:
        def __init__(self, reader: StreamingReader) -> None:
            self.reader = reader
            self.count = 0

        def encode(
            self,
            *,
            cache_key: str,
            request: PreviewRequestV1,
            frames: object,
            cancelled=None,  # type: ignore[no-untyped-def]
        ) -> EncodedPreviewArtifactV1:
            del cache_key, request, cancelled
            for frame in frames:  # type: ignore[union-attr]
                assert isinstance(frame, RenderFrameV1)
                self.count += 1
                assert self.reader.in_flight <= 1
            return EncodedPreviewArtifactV1(
                artifact_uri="memory://stream/index.m3u8",
                duration_seconds=5_000,
                frame_count=self.count,
            )

        def cleanup(self, encoded: EncodedPreviewArtifactV1) -> None:
            del encoded

    repository = InMemoryPreviewRepository()
    store = InMemoryPreviewArtifactStore()
    reader = StreamingReader()
    encoder = StreamingEncoder(reader)
    control = PreviewControlPlaneService(
        repository=repository,
        store=store,
        signer=HmacUrlSigner(),
        clock=lambda: NOW,
    )
    generation = PreviewGenerationService(
        step_reader=reader,
        exclusions=InMemoryExclusionReader(),
        encoder=encoder,
        repository=repository,
        store=store,
        clock=lambda: NOW,
    )
    long_request = request().model_copy(update={"end_step": 10_000})
    pending = authorize(control, long_request)
    assert isinstance(pending, PreviewPendingDescriptorV1)

    artifact = generation.generate(SCOPE, long_request, job_id=pending.job_id)

    assert artifact.frame_count == 10_000
    assert encoder.count == 10_000
    assert reader.peak_in_flight == 1
