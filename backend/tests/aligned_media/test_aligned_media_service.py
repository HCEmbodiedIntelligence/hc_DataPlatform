from __future__ import annotations

import io
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from PIL import Image

from hc_data_platform.aligned_media.artifact_store import LocalAlignedMediaArtifactStore
from hc_data_platform.aligned_media.audit import InMemoryAlignedMediaAuditRecorder
from hc_data_platform.aligned_media.encoder import AlignedMediaEncodingError, FFmpegMp4Encoder
from hc_data_platform.aligned_media.maintenance import (
    PostgresAlignedMediaVersionRetirementCollector,
)
from hc_data_platform.aligned_media.memory import InMemoryAlignedMediaRepository
from hc_data_platform.aligned_media.models import (
    AlignedMediaArtifactStatus,
    AlignedMediaArtifactV1,
    AlignedMediaEncodingProfileV1,
    AlignedMediaGenerationRequestV1,
    AlignedMediaScopeV1,
    AlignedMediaSelectorV1,
    AlignmentStagingArtifactV1,
    EncodedAlignedMediaV1,
)
from hc_data_platform.aligned_media.router import configure_aligned_media
from hc_data_platform.aligned_media.router import router as aligned_media_router
from hc_data_platform.aligned_media.service import (
    AlignedMediaAuthorizationService,
    AlignedMediaGenerationService,
    AlignedMediaLifecycleService,
)
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security import AuthContext

NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)
SHA = "a" * 64


@dataclass(frozen=True)
class Frame:
    step_index: int
    timestamp_ns: int
    image: bytes | None
    valid: bool = True
    repeated: bool = False


class CountingFrameReader:
    def __init__(self) -> None:
        self.calls = 0

    def read_camera_frames(self, request: AlignedMediaGenerationRequestV1):  # type: ignore[no-untyped-def]
        self.calls += 1
        return (
            Frame(index, request.alignment.created_at.microsecond + index, b"jpeg")
            for index in range(request.alignment.row_count)
        )


class CountingEncoder:
    def __init__(self, root: Path) -> None:
        self._root = root
        self.calls = 0
        self.cleanup_calls = 0

    def encode(self, *, artifact_key: str, request, frames, cancelled=None):  # type: ignore[no-untyped-def]
        del cancelled
        self.calls += 1
        frame_count = sum(1 for _frame in frames)
        directory = self._root / artifact_key
        directory.mkdir(parents=True, exist_ok=True)
        media = directory / "media.mp4"
        media.write_bytes(b"canonical-mp4")
        return EncodedAlignedMediaV1(
            file_uri=media.resolve().as_uri(),
            frame_count=frame_count,
            duration_seconds=frame_count / 30,
            width=1280,
            height=720,
            placeholder_count=0,
            first_timestamp_ns=0,
        )

    def cleanup(self, encoded: EncodedAlignedMediaV1) -> None:
        del encoded
        self.cleanup_calls += 1


class CountingRepository(InMemoryAlignedMediaRepository):
    def __init__(self) -> None:
        super().__init__()
        self.ensure_calls = 0
        self.find_calls = 0

    def ensure_generation(self, **kwargs):  # type: ignore[no-untyped-def]
        self.ensure_calls += 1
        return super().ensure_generation(**kwargs)

    def find_by_selector(
        self,
        scope: AlignedMediaScopeV1,
        selector: AlignedMediaSelectorV1,
        *,
        profile_id: str,
    ) -> AlignedMediaArtifactV1 | None:
        self.find_calls += 1
        return super().find_by_selector(scope, selector, profile_id=profile_id)


class RetirementCandidateCursor:
    def __init__(self) -> None:
        self.query = ""
        self.parameters: tuple[object, ...] = ()
        self.rowcount = 1

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def execute(self, query: str, parameters: tuple[object, ...]) -> None:
        self.query = query
        self.parameters = parameters

    def fetchall(self) -> tuple[tuple[str, str, int], ...]:
        return (("dataset-1", "version_lance_7", 7),)


class RetirementCandidateConnection:
    def __init__(self) -> None:
        self.cursor_value = RetirementCandidateCursor()
        self.closed = False

    def cursor(self) -> RetirementCandidateCursor:
        return self.cursor_value

    def close(self) -> None:
        self.closed = True

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


def scope() -> AlignedMediaScopeV1:
    return AlignedMediaScopeV1(
        organization_id="organization-1",
        project_id="project-1",
        region_code="cn-test",
    )


def generation_request() -> AlignedMediaGenerationRequestV1:
    return AlignedMediaGenerationRequestV1(
        project_id="project-1",
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        expected_dataset_version=7,
        camera_id="front",
        source_sha256=SHA,
        alignment=AlignmentStagingArtifactV1(
            object_key="staging/alignment/test.arrow",
            content_sha256="b" * 64,
            size_bytes=100,
            row_count=1_800,
            alignment_version="causal-30hz-v1",
            created_at=NOW,
            expires_at=NOW + timedelta(hours=1),
        ),
    )


def selector() -> AlignedMediaSelectorV1:
    return AlignedMediaSelectorV1(
        project_id="project-1",
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        dataset_version=7,
        camera_id="front",
    )


def test_generation_is_idempotent_and_authorization_is_read_only(tmp_path: Path) -> None:
    repository = CountingRepository()
    reader = CountingFrameReader()
    encoder = CountingEncoder(tmp_path / "encode")
    store = LocalAlignedMediaArtifactStore(tmp_path / "objects")
    generation = AlignedMediaGenerationService(
        frame_reader=reader,
        encoder=encoder,
        repository=repository,
        store=store,
        heartbeat_interval=timedelta(hours=1),
        clock=lambda: NOW,
    )

    first = generation.generate(scope(), generation_request())
    second = generation.generate(scope(), generation_request())

    assert first == second
    assert first.status is AlignedMediaArtifactStatus.READY
    assert first.frame_count == 1_800
    assert first.duration_seconds == 60
    assert first.timeline is not None
    assert first.timeline.frame_count == 1_800
    assert encoder.calls == reader.calls == 1
    assert repository.ensure_calls == 2
    assert len(tuple((tmp_path / "objects").rglob("media.mp4"))) == 1

    authorization = AlignedMediaAuthorizationService(
        repository=repository,
        store=store,
        clock=lambda: NOW,
    )
    with pytest.raises(ProblemException) as uncommitted:
        authorization.authorize(scope(), selector())
    assert uncommitted.value.problem.status == 425

    repository.mark_dataset_committed(
        scope=scope(),
        artifact_ids=(first.artifact_id,),
        now=NOW,
    )

    before = (repository.ensure_calls, reader.calls, encoder.calls)
    descriptor = authorization.authorize(scope(), selector())

    assert descriptor.content_type == "video/mp4"
    assert descriptor.media_url.startswith("/api/v1/aligned-media/local/")
    assert descriptor.timeline.pts_time_base_denominator == 30
    assert descriptor.model_dump(mode="json")["timeline"]["start_timestamp_ns"] == "0"
    assert (repository.ensure_calls, reader.calls, encoder.calls) == before
    assert repository.find_calls == 2


def test_missing_media_returns_not_ready_without_creating_work(tmp_path: Path) -> None:
    repository = CountingRepository()
    service = AlignedMediaAuthorizationService(
        repository=repository,
        store=LocalAlignedMediaArtifactStore(tmp_path),
        clock=lambda: NOW,
    )

    with pytest.raises(ProblemException) as caught:
        service.authorize(scope(), selector())

    assert caught.value.problem.status == 425
    assert caught.value.problem.code == "ALIGNED_MEDIA_NOT_READY"
    assert repository.ensure_calls == 0
    assert repository.find_calls == 1


def test_authorize_endpoint_serves_exact_opaque_key_with_http_range(
    tmp_path: Path,
) -> None:
    repository = CountingRepository()
    reader = CountingFrameReader()
    encoder = CountingEncoder(tmp_path / "encode")
    store = LocalAlignedMediaArtifactStore(tmp_path / "objects")
    artifact = AlignedMediaGenerationService(
        frame_reader=reader,
        encoder=encoder,
        repository=repository,
        store=store,
        heartbeat_interval=timedelta(hours=1),
        clock=lambda: NOW,
    ).generate(scope(), generation_request())
    repository.mark_dataset_committed(scope=scope(), artifact_ids=(artifact.artifact_id,), now=NOW)
    audit = InMemoryAlignedMediaAuditRecorder()
    configure_aligned_media(
        AlignedMediaAuthorizationService(repository=repository, store=store, clock=lambda: NOW),
        store,
        audit,
    )
    app = FastAPI()
    app.include_router(aligned_media_router)
    auth = AuthContext(
        subject_id="annotator-1",
        project_ids=frozenset({"project-1"}),
        region_codes=frozenset({"cn-test"}),
        roles=frozenset({"annotator"}),
        organization_ids=frozenset({"organization-1"}),
        organization_scope_triples=frozenset({("organization-1", "project-1", "cn-test")}),
    )

    @app.middleware("http")
    async def install_auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.auth_context = auth
        return await call_next(request)

    before = (repository.ensure_calls, reader.calls, encoder.calls)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/aligned-media/authorize",
            headers={
                "X-Organization-Id": "organization-1",
                "X-Region-Code": "cn-test",
            },
            json=selector().model_dump(mode="json"),
        )
        assert response.status_code == 200
        media = client.get(response.json()["media_url"], headers={"Range": "bytes=0-3"})

    assert media.status_code == 206
    assert media.content == b"cano"
    assert media.headers["content-range"] == "bytes 0-3/13"
    assert (repository.ensure_calls, reader.calls, encoder.calls) == before
    assert len(audit.events) == 1


def test_uncommitted_bundle_cleanup_deletes_exact_receipt_and_can_retry(
    tmp_path: Path,
) -> None:
    repository = CountingRepository()
    reader = CountingFrameReader()
    encoder = CountingEncoder(tmp_path / "encode")
    store = LocalAlignedMediaArtifactStore(tmp_path / "objects")
    generation = AlignedMediaGenerationService(
        frame_reader=reader,
        encoder=encoder,
        repository=repository,
        store=store,
        heartbeat_interval=timedelta(hours=1),
        clock=lambda: NOW,
    )
    artifact = generation.generate(scope(), generation_request())

    objects = repository.begin_abandon_uncommitted(
        scope=scope(),
        artifact_ids=(artifact.artifact_id,),
        error_code="ALIGNED_MEDIA_BUNDLE_ABORTED",
        now=NOW,
    )
    assert {item.media_type for item in objects} == {"video/mp4", "application/json"}
    assert store.delete_exact(objects) == sum(item.size for item in objects)
    repository.complete_abandon_uncommitted(
        scope=scope(), artifact_ids=(artifact.artifact_id,), now=NOW
    )
    assert not tuple((tmp_path / "objects").rglob("*.*"))

    retried = generation.generate(scope(), generation_request())
    assert retried.status is AlignedMediaArtifactStatus.READY
    assert encoder.calls == reader.calls == 2
    assert len(tuple((tmp_path / "objects").rglob("media.mp4"))) == 1


def test_active_dataset_commit_fence_blocks_uncommitted_cleanup(tmp_path: Path) -> None:
    repository = CountingRepository()
    store = LocalAlignedMediaArtifactStore(tmp_path / "objects")
    artifact = AlignedMediaGenerationService(
        frame_reader=CountingFrameReader(),
        encoder=CountingEncoder(tmp_path / "encode"),
        repository=repository,
        store=store,
        heartbeat_interval=timedelta(hours=1),
        clock=lambda: NOW,
    ).generate(scope(), generation_request())
    repository.begin_dataset_commit(
        scope=scope(),
        artifact_ids=(artifact.artifact_id,),
        lease_expires_at=NOW + timedelta(hours=1),
        now=NOW,
    )

    assert (
        repository.begin_abandon_uncommitted(
            scope=scope(),
            artifact_ids=(artifact.artifact_id,),
            error_code="ALIGNED_MEDIA_BUNDLE_ABORTED",
            now=NOW + timedelta(minutes=1),
        )
        == ()
    )
    protected = repository.find_by_selector(
        scope(), selector(), profile_id="canonical-h264-crf20-v1"
    )
    assert protected is not None
    assert protected.status is AlignedMediaArtifactStatus.READY

    expired = repository.begin_abandon_uncommitted(
        scope=scope(),
        artifact_ids=(artifact.artifact_id,),
        error_code="ALIGNED_MEDIA_BUNDLE_ABORTED",
        now=NOW + timedelta(hours=2),
    )
    assert {item.key for item in expired} == {item.key for item in artifact.objects}


def test_committed_dataset_version_retirement_is_exact_and_idempotent(tmp_path: Path) -> None:
    repository = CountingRepository()
    store = LocalAlignedMediaArtifactStore(tmp_path / "objects")
    artifact = AlignedMediaGenerationService(
        frame_reader=CountingFrameReader(),
        encoder=CountingEncoder(tmp_path / "encode"),
        repository=repository,
        store=store,
        heartbeat_interval=timedelta(hours=1),
        clock=lambda: NOW,
    ).generate(scope(), generation_request())
    repository.mark_dataset_committed(scope=scope(), artifact_ids=(artifact.artifact_id,), now=NOW)
    lifecycle = AlignedMediaLifecycleService(
        repository=repository,
        store=store,
        clock=lambda: NOW + timedelta(days=1),
    )
    connections: list[RetirementCandidateConnection] = []

    def connection_factory() -> RetirementCandidateConnection:
        connection = RetirementCandidateConnection()
        connections.append(connection)
        return connection

    collector = PostgresAlignedMediaVersionRetirementCollector(
        connection_factory,
        lifecycle,
        clock=lambda: NOW + timedelta(days=1),
    )
    context = bind_request_context(
        RequestContext(
            organization_id="organization-1",
            project_id="project-1",
            region_code="cn-test",
            service_identity=True,
        )
    )
    try:
        assert collector.run_once() == artifact.total_bytes
        assert collector.run_once() == 0
    finally:
        reset_request_context(context)

    assert all(connection.closed for connection in connections)
    assert "FOR UPDATE SKIP LOCKED" in connections[0].cursor_value.query
    assert "dataset_version_retirement_requests" in connections[0].cursor_value.query
    assert connections[0].cursor_value.parameters[:3] == (
        "organization-1",
        "project-1",
        "cn-test",
    )
    assert not tuple((tmp_path / "objects").rglob("*.*"))
    with pytest.raises(ProblemException) as retired:
        AlignedMediaAuthorizationService(
            repository=repository,
            store=store,
            clock=lambda: NOW,
        ).authorize(scope(), selector())
    assert retired.value.problem.status == 410
    assert retired.value.problem.code == "ALIGNED_MEDIA_RETIRED"


def test_preserve_resolution_uses_first_valid_frame_after_leading_placeholder(
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []

    class Sink:
        def __init__(self) -> None:
            self.data = bytearray()

        def write(self, value: bytes) -> int:
            self.data.extend(value)
            return len(value)

        def close(self) -> None:
            return None

    class Process:
        def __init__(self) -> None:
            self.stdin: Sink | None = Sink()
            self.stderr = io.BytesIO()
            self.returncode = 0

        def communicate(self, timeout: float):  # type: ignore[no-untyped-def]
            del timeout
            return b"", b""

        def kill(self) -> None:
            self.returncode = -9

    def process_factory(command: list[str], **kwargs: object) -> Process:
        del kwargs
        commands.append(command)
        return Process()

    jpeg_buffer = io.BytesIO()
    Image.new("RGB", (96, 54), color=(12, 34, 56)).save(jpeg_buffer, format="JPEG")
    encoder = FFmpegMp4Encoder(tmp_path, process_factory=process_factory)
    output = tmp_path / "attempt" / "media.mp4"
    output.parent.mkdir()

    encoder._encode_file(  # noqa: SLF001
        output,
        profile=AlignedMediaEncodingProfileV1(),
        frames=(
            Frame(0, 10, None, valid=False),
            Frame(1, 20, jpeg_buffer.getvalue()),
        ),
        cancelled=None,
    )

    video_filter = commands[0][commands[0].index("-vf") + 1]
    assert video_filter.startswith("scale=96:54:")
    facts = json.loads((output.parent / "encode-facts.json").read_text())
    assert facts == {"frame_count": 2, "first_timestamp_ns": 10, "placeholder_count": 1}


def test_retry_reencodes_when_persisted_publication_receipt_was_orphaned(
    tmp_path: Path,
) -> None:
    class FailOnceReadyRepository(CountingRepository):
        fail_ready = True

        def mark_ready(self, **kwargs):  # type: ignore[no-untyped-def]
            if self.fail_ready:
                self.fail_ready = False
                raise ConnectionError("injected READY transition failure")
            return super().mark_ready(**kwargs)

    repository = FailOnceReadyRepository()
    reader = CountingFrameReader()
    encoder = CountingEncoder(tmp_path / "encode")
    store = LocalAlignedMediaArtifactStore(tmp_path / "objects")
    generation = AlignedMediaGenerationService(
        frame_reader=reader,
        encoder=encoder,
        repository=repository,
        store=store,
        heartbeat_interval=timedelta(hours=1),
        clock=lambda: NOW,
    )

    with pytest.raises(ConnectionError, match="READY transition"):
        generation.generate(scope(), generation_request())
    generating = repository.find_by_selector(
        scope(), selector(), profile_id="canonical-h264-crf20-v1"
    )
    assert generating is not None
    assert generating.status is AlignedMediaArtifactStatus.GENERATING
    assert store.delete_exact(generating.objects) > 0

    recovered = generation.generate(scope(), generation_request())
    assert recovered.status is AlignedMediaArtifactStatus.READY
    assert encoder.calls == reader.calls == 2
    assert all(store.resolve_local_object(item.key) is not None for item in recovered.objects)


def test_encoder_failure_is_retryable_without_duplicate_artifact(tmp_path: Path) -> None:
    class FailOnceEncoder(CountingEncoder):
        def encode(self, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                raise AlignedMediaEncodingError("injected FFmpeg failure")
            self.calls -= 1
            return super().encode(**kwargs)

    repository = CountingRepository()
    reader = CountingFrameReader()
    encoder = FailOnceEncoder(tmp_path / "encode")
    store = LocalAlignedMediaArtifactStore(tmp_path / "objects")
    generation = AlignedMediaGenerationService(
        frame_reader=reader,
        encoder=encoder,
        repository=repository,
        store=store,
        heartbeat_interval=timedelta(hours=1),
        clock=lambda: NOW,
    )

    with pytest.raises(AlignedMediaEncodingError, match="FFmpeg failure"):
        generation.generate(scope(), generation_request())
    recovered = generation.generate(scope(), generation_request())

    assert recovered.status is AlignedMediaArtifactStatus.READY
    assert encoder.calls == 2
    assert reader.calls == 2
    assert len(tuple((tmp_path / "objects").rglob("media.mp4"))) == 1


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg runtime is unavailable",
)
def test_real_ffmpeg_encodes_exact_sixty_second_canonical_mp4(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), color=(12, 34, 56)).save(buffer, format="JPEG")
    jpeg = buffer.getvalue()

    class SixtySecondReader:
        def read_camera_frames(self, request: AlignedMediaGenerationRequestV1):  # type: ignore[no-untyped-def]
            return (
                Frame(index, index * 33_333_333, jpeg)
                for index in range(request.alignment.row_count)
            )

    repository = InMemoryAlignedMediaRepository()
    store = LocalAlignedMediaArtifactStore(tmp_path / "objects")
    artifact = AlignedMediaGenerationService(
        frame_reader=SixtySecondReader(),
        encoder=FFmpegMp4Encoder(tmp_path / "encode", ffmpeg_threads=1),
        repository=repository,
        store=store,
        heartbeat_interval=timedelta(hours=1),
        clock=lambda: NOW,
    ).generate(scope(), generation_request())
    repository.mark_dataset_committed(scope=scope(), artifact_ids=(artifact.artifact_id,), now=NOW)

    assert artifact.media_object_key is not None
    media = store.resolve_local_object(artifact.media_object_key)
    assert media is not None
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            "stream=codec_name,pix_fmt,avg_frame_rate,nb_read_frames:format=duration",
            "-of",
            "json",
            str(media),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    probe = json.loads(result.stdout)
    stream = probe["streams"][0]
    assert stream == {
        "codec_name": "h264",
        "pix_fmt": "yuv420p",
        "avg_frame_rate": "30/1",
        "nb_read_frames": "1800",
    }
    assert float(probe["format"]["duration"]) == pytest.approx(60.0, abs=1 / 30)
