from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from datetime import datetime, timedelta, timezone
from itertools import chain
from urllib.parse import urlencode

from hc_data_platform.core.errors import ProblemException, problem

from .metrics import (
    PREVIEW_CACHE_HIT,
    PREVIEW_CACHE_MISS,
    PREVIEW_FRAMES,
    PREVIEW_GENERATION_SECONDS,
    PREVIEW_JOBS_FAILED,
    PREVIEW_JOBS_QUEUED,
    PREVIEW_JOBS_RUNNING,
    PREVIEW_OUTPUT_BYTES,
    PREVIEW_PEAK_IN_FLIGHT_FRAMES,
)
from .models import (
    PlaceholderDescriptorV1,
    PreviewArtifactStatus,
    PreviewArtifactV1,
    PreviewDescriptorV1,
    PreviewFrameV1,
    PreviewJobDescriptorV1,
    PreviewJobStatus,
    PreviewJobV1,
    PreviewPendingDescriptorV1,
    PreviewRequestV1,
    PreviewScopeV1,
    RenderFrameV1,
    StepRangeV1,
    TimelineMappingV1,
    TimelineSegmentV1,
    ViewMode,
)
from .ports import (
    EffectiveExclusionPort,
    MediaEncoderPort,
    PreviewArtifactStorePort,
    PreviewJobQueuePort,
    PreviewRepositoryPort,
    StepReaderPort,
    UrlSignerPort,
)
from .profiles import DEFAULT_PREVIEW_PROFILES, PreviewProfileCatalog

PREVIEW_PIPELINE_REVISION = "h264-image2pipe-v1"
_PLAYLIST_MEMBER = re.compile(r"^(?:init\.mp4|segment_[0-9]{5,}\.m4s)$")
_MAP_URI = re.compile(r'URI="(?P<asset>[^"]+)"')


def normalize_ranges(ranges: Sequence[StepRangeV1]) -> tuple[StepRangeV1, ...]:
    if not ranges:
        return ()
    ordered = sorted(ranges, key=lambda item: (item.start_step, item.end_step))
    merged: list[StepRangeV1] = []
    for item in ordered:
        if not merged or item.start_step > merged[-1].end_step:
            merged.append(item)
            continue
        previous = merged[-1]
        merged[-1] = StepRangeV1(
            start_step=previous.start_step,
            end_step=max(previous.end_step, item.end_step),
        )
    return tuple(merged)


def exclusion_content_hash(ranges: Sequence[StepRangeV1]) -> str:
    normalized = normalize_ranges(ranges)
    canonical = json.dumps(
        [[item.start_step, item.end_step] for item in normalized],
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def canonical_source_request(request: PreviewRequestV1) -> PreviewRequestV1:
    """Reuse immutable ORIGINAL media for edited/compare UI overlays."""

    if request.view_mode is ViewMode.ORIGINAL and request.annotation_revision == 0:
        return request
    return request.model_copy(
        update={"view_mode": ViewMode.ORIGINAL, "annotation_revision": 0}
    )


def preview_artifact_key(
    request: PreviewRequestV1,
    *,
    effective_exclusion_hash: str | None = None,
) -> str:
    """Stable identity; sessions and annotation revision never enter ORIGINAL keys."""

    payload: dict[str, object] = {
        "pipeline_revision": PREVIEW_PIPELINE_REVISION,
        "dataset_id": request.dataset_id,
        "rollout_id": request.rollout_id,
        "lance_version": request.lance_version,
        "camera_id": request.camera_id,
        "profile_id": request.profile_id,
        "frequency_hz": request.frequency_hz,
        "source_start_step": request.start_step,
        "source_end_step": request.end_step,
    }
    if request.view_mode is not ViewMode.ORIGINAL:
        if effective_exclusion_hash is None:
            raise ValueError("edited preview identity requires an effective exclusion hash")
        payload.update(
            {
                "view_mode": request.view_mode.value,
                "effective_exclusion_hash": effective_exclusion_hash,
            }
        )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# Compatibility import name; its semantics are now the durable artifact identity.
preview_cache_key = preview_artifact_key


class RenderFrameStream(Iterable[RenderFrameV1]):
    """One-shot bounded transform that retains only compact timeline metadata."""

    def __init__(
        self,
        frames: Iterable[PreviewFrameV1],
        *,
        ranges: Sequence[StepRangeV1],
        view_mode: ViewMode,
        frequency_hz: float,
        placeholder_detail_limit: int = 100,
    ) -> None:
        self._frames = iter(frames)
        self._ranges = normalize_ranges(ranges)
        self._view_mode = view_mode
        self._frequency_hz = frequency_hz
        self._placeholder_detail_limit = placeholder_detail_limit
        self._consumed = False
        self.frame_count = 0
        self.placeholder_count = 0
        self.placeholders: list[PlaceholderDescriptorV1] = []
        self._segments: list[TimelineSegmentV1] = []
        self._segment_first: RenderFrameV1 | None = None
        self._segment_previous: RenderFrameV1 | None = None

    def __iter__(self) -> Iterator[RenderFrameV1]:
        if self._consumed:
            raise RuntimeError("preview frame stream is one-shot")
        self._consumed = True
        previous_step: int | None = None
        range_index = 0
        for source in self._frames:
            if previous_step is not None and source.step_index <= previous_step:
                code = (
                    "DUPLICATE_PREVIEW_STEP"
                    if source.step_index == previous_step
                    else "UNORDERED_PREVIEW_STEP"
                )
                raise problem(
                    status=409,
                    code=code,
                    title="Invalid preview step order",
                    detail="The step reader must stream unique source steps in ascending order.",
                )
            previous_step = source.step_index
            while (
                range_index < len(self._ranges)
                and self._ranges[range_index].end_step <= source.step_index
            ):
                range_index += 1
            excluded = (
                range_index < len(self._ranges)
                and self._ranges[range_index].start_step <= source.step_index
                < self._ranges[range_index].end_step
            )
            if self._view_mode is ViewMode.EDITED and excluded:
                continue
            placeholder: PlaceholderDescriptorV1 | None = None
            if not source.valid or source.image_ref is None:
                placeholder = PlaceholderDescriptorV1(
                    invalid_reason=source.invalid_reason or "image data is missing or invalid",
                    playback_frame=self.frame_count,
                    step_index=source.step_index,
                )
                self.placeholder_count += 1
                if len(self.placeholders) < self._placeholder_detail_limit:
                    self.placeholders.append(placeholder)
            rendered = RenderFrameV1(
                playback_frame=self.frame_count,
                step_index=source.step_index,
                timestamp_ns=source.timestamp_ns,
                source_timestamp_ns=source.source_timestamp_ns,
                image_ref=source.image_ref if placeholder is None else None,
                excluded=excluded if self._view_mode is ViewMode.COMPARE else False,
                placeholder=placeholder,
            )
            self.frame_count += 1
            self._observe_timeline(rendered)
            yield rendered
        self._finish_timeline()

    def _observe_timeline(self, frame: RenderFrameV1) -> None:
        previous = self._segment_previous
        if previous is None:
            self._segment_first = self._segment_previous = frame
            return
        if not (
            frame.playback_frame == previous.playback_frame + 1
            and frame.step_index == previous.step_index + 1
            and frame.excluded == previous.excluded
        ):
            assert self._segment_first is not None
            self._segments.append(
                _timeline_segment(self._segment_first, previous, self._frequency_hz)
            )
            self._segment_first = frame
        self._segment_previous = frame

    def _finish_timeline(self) -> None:
        if self._segment_first is not None and self._segment_previous is not None:
            self._segments.append(
                _timeline_segment(
                    self._segment_first,
                    self._segment_previous,
                    self._frequency_hz,
                )
            )
            self._segment_first = self._segment_previous = None

    @property
    def timeline(self) -> TimelineMappingV1:
        if not self._consumed:
            raise RuntimeError("timeline is unavailable before encoding consumes the stream")
        return TimelineMappingV1(
            frequency_hz=self._frequency_hz,
            segments=tuple(self._segments),
        )


def build_render_frames(
    *,
    frames: Sequence[PreviewFrameV1],
    ranges: Sequence[StepRangeV1],
    view_mode: ViewMode,
) -> tuple[RenderFrameV1, ...]:
    """Compatibility helper for small fixtures; production uses RenderFrameStream."""

    return tuple(
        RenderFrameStream(
            frames,
            ranges=ranges,
            view_mode=view_mode,
            frequency_hz=1,
        )
    )


def build_timeline(
    frames: Sequence[RenderFrameV1], *, frequency_hz: float
) -> TimelineMappingV1:
    if not frames:
        return TimelineMappingV1(frequency_hz=frequency_hz)
    segments: list[TimelineSegmentV1] = []
    group_start = frames[0]
    previous = frames[0]
    for current in frames[1:]:
        if not (
            current.playback_frame == previous.playback_frame + 1
            and current.step_index == previous.step_index + 1
            and current.excluded == previous.excluded
        ):
            segments.append(_timeline_segment(group_start, previous, frequency_hz))
            group_start = current
        previous = current
    segments.append(_timeline_segment(group_start, previous, frequency_hz))
    return TimelineMappingV1(frequency_hz=frequency_hz, segments=tuple(segments))


def _timeline_segment(
    start: RenderFrameV1, end: RenderFrameV1, frequency_hz: float
) -> TimelineSegmentV1:
    return TimelineSegmentV1(
        playback_start_seconds=start.playback_frame / frequency_hz,
        playback_end_seconds=(end.playback_frame + 1) / frequency_hz,
        source_start_step=start.step_index,
        source_end_step=end.step_index + 1,
        excluded=start.excluded,
    )


class NoopPreviewJobQueue:
    """Test/local control-plane queue that leaves durable work in QUEUED state."""

    async def enqueue(self, _job: PreviewJobV1) -> None:
        return None


class PreviewControlPlaneService:
    """API-safe preview authorization. This class has no encoder dependency."""

    def __init__(
        self,
        *,
        repository: PreviewRepositoryPort,
        store: PreviewArtifactStorePort,
        signer: UrlSignerPort,
        queue: PreviewJobQueuePort | None = None,
        profiles: PreviewProfileCatalog = DEFAULT_PREVIEW_PROFILES,
        artifact_ttl: timedelta = timedelta(days=30),
        session_ttl: timedelta = timedelta(minutes=30),
        signed_url_ttl: timedelta = timedelta(minutes=15),
        retry_after_seconds: int = 2,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._store = store
        self._signer = signer
        self._queue = queue or NoopPreviewJobQueue()
        self._profiles = profiles
        self._artifact_ttl = artifact_ttl
        self._session_ttl = session_ttl
        self._signed_url_ttl = signed_url_ttl
        self._retry_after_seconds = retry_after_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def create_session(
        self, scope: PreviewScopeV1, request: PreviewRequestV1
    ) -> PreviewDescriptorV1 | PreviewPendingDescriptorV1:
        self._validate_scope(scope, request)
        self._profiles.get(request.profile_id)
        source_request = canonical_source_request(request)
        artifact_key = preview_artifact_key(source_request)
        now = self._now()
        artifact = self._repository.find_artifact(scope, artifact_key, now=now)
        if artifact is not None and artifact.status is PreviewArtifactStatus.READY:
            PREVIEW_CACHE_HIT.inc()
            session = self._repository.create_session(
                scope=scope,
                artifact=artifact,
                request=request,
                session_ttl=self._session_ttl,
                now=now,
            )
            return self._descriptor(session.session_id, request, artifact, now=now)

        artifact, job = self._repository.ensure_artifact_job(
            scope=scope,
            artifact_key=artifact_key,
            request=source_request,
            pipeline_revision=PREVIEW_PIPELINE_REVISION,
            rebuild_source_id=(
                f"lance:{request.dataset_id}:{request.lance_version}:{request.rollout_id}"
            ),
            artifact_ttl=self._artifact_ttl,
            now=now,
        )
        if artifact.status is PreviewArtifactStatus.READY:
            PREVIEW_CACHE_HIT.inc()
            session = self._repository.create_session(
                scope=scope,
                artifact=artifact,
                request=request,
                session_ttl=self._session_ttl,
                now=now,
            )
            return self._descriptor(session.session_id, request, artifact, now=now)
        if job is None:
            raise RuntimeError("a non-ready preview artifact must have an active job")
        PREVIEW_CACHE_MISS.inc()
        if job.status is not PreviewJobStatus.FAILED:
            PREVIEW_JOBS_QUEUED.set(1)
            await self._queue.enqueue(job)
        return self._pending(job)

    def get_job(self, scope: PreviewScopeV1, job_id: str) -> PreviewJobDescriptorV1:
        job = self._repository.get_job(scope, job_id)
        if job is None:
            raise problem(
                status=404,
                code="PREVIEW_JOB_NOT_FOUND",
                title="Preview job not found",
                detail="The preview job does not exist in the selected scope.",
            )
        return PreviewJobDescriptorV1(
            job_id=job.job_id,
            artifact_key=job.artifact_key,
            status=job.status,
            progress=job.progress,
            retry_after_seconds=(
                self._retry_after_seconds
                if job.status in {PreviewJobStatus.QUEUED, PreviewJobStatus.RUNNING}
                else None
            ),
            error_code=job.error_code,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )

    def get_session(
        self, scope: PreviewScopeV1, session_id: str
    ) -> PreviewDescriptorV1:
        now = self._now()
        result = self._repository.get_session(scope, session_id, now=now)
        if result is None:
            raise problem(
                status=404,
                code="PREVIEW_SESSION_NOT_FOUND",
                title="Preview session not found",
                detail="The preview session does not exist or has expired.",
            )
        session, artifact = result
        return self._descriptor(session.session_id, session.request, artifact, now=now)

    def signed_playlist(
        self,
        scope: PreviewScopeV1,
        *,
        session_id: str,
        expires: int,
        signature: str,
    ) -> str:
        now = self._now()
        result = self._repository.get_session(scope, session_id, now=now)
        if result is None:
            raise problem(
                status=404,
                code="PREVIEW_SESSION_NOT_FOUND",
                title="Preview session not found",
                detail="The preview session does not exist or has expired.",
            )
        _, artifact = result
        if artifact.playlist_key is None or not self._signer.verify(
            session_id=session_id,
            artifact_uri=artifact.playlist_key,
            asset_name="index.m3u8",
            expires=expires,
            signature=signature,
            now=now,
        ):
            raise problem(
                status=403,
                code="PREVIEW_MEDIA_FORBIDDEN",
                title="Preview media access denied",
                detail="The media capability is invalid or expired.",
            )
        return self._rewrite_playlist(artifact)

    def _rewrite_playlist(self, artifact: PreviewArtifactV1) -> str:
        assert artifact.playlist_key is not None
        raw = self._store.read_playlist(artifact.playlist_key)
        by_name = {
            object_record.key.rsplit("/", maxsplit=1)[-1]: object_record.key
            for object_record in artifact.objects
        }

        def signed(asset: str) -> str:
            if _PLAYLIST_MEMBER.fullmatch(asset) is None or asset not in by_name:
                raise problem(
                    status=409,
                    code="PREVIEW_MANIFEST_INVALID",
                    title="Preview manifest is invalid",
                    detail="The committed playlist references an undeclared object.",
                )
            return self._store.authorize_object(
                by_name[asset], expires_in=self._signed_url_ttl
            )

        output: list[str] = []
        for line in raw.splitlines():
            if line.startswith("#EXT-X-MAP:"):
                match = _MAP_URI.search(line)
                if match is None:
                    raise problem(
                        status=409,
                        code="PREVIEW_MANIFEST_INVALID",
                        title="Preview manifest is invalid",
                        detail="The HLS initialization object is malformed.",
                    )
                line = _MAP_URI.sub(
                    f'URI="{signed(match.group("asset"))}"', line, count=1
                )
            elif line and not line.startswith("#"):
                line = signed(line)
            output.append(line)
        return "\n".join(output) + "\n"

    def _descriptor(
        self,
        session_id: str,
        request: PreviewRequestV1,
        artifact: PreviewArtifactV1,
        *,
        now: datetime,
    ) -> PreviewDescriptorV1:
        if artifact.status is not PreviewArtifactStatus.READY or artifact.playlist_key is None:
            raise RuntimeError("only READY artifacts can be authorized")
        signed_expires_at = min(now + self._signed_url_ttl, artifact.expires_at)
        playlist_url = self._signer.sign(
            session_id=session_id,
            artifact_uri=artifact.playlist_key,
            asset_name="index.m3u8",
            expires_at=signed_expires_at,
        )
        playlist_url = (
            f"{playlist_url}&"
            + urlencode(
                {
                    "organization_id": artifact.scope.organization_id,
                    "project_id": artifact.scope.project_id,
                    "region_code": artifact.scope.region_code,
                }
            )
        )
        source_start = artifact.source_start_step or 0
        source_end = artifact.source_end_step or source_start + artifact.frame_count
        segments = (
            ()
            if artifact.frame_count == 0
            else (
                TimelineSegmentV1(
                    playback_start_seconds=0,
                    playback_end_seconds=artifact.duration_seconds,
                    source_start_step=source_start,
                    source_end_step=source_end,
                ),
            )
        )
        return PreviewDescriptorV1(
            session_id=session_id,
            artifact_key=artifact.artifact_key,
            project_id=request.project_id,
            dataset_id=request.dataset_id,
            rollout_id=request.rollout_id,
            lance_version=request.lance_version,
            annotation_revision=request.annotation_revision,
            camera_id=request.camera_id,
            view_mode=request.view_mode,
            profile_id=request.profile_id,
            encoding_profile=self._profiles.get(request.profile_id),
            playlist_url=playlist_url,
            media_type="application/vnd.apple.mpegurl",
            frame_count=artifact.frame_count,
            placeholder_count=0,
            duration_seconds=artifact.duration_seconds,
            timeline=TimelineMappingV1(
                frequency_hz=request.frequency_hz,
                segments=segments,
            ),
            artifact_expires_at=artifact.expires_at,
            signed_url_expires_at=signed_expires_at,
        )

    def _pending(self, job: PreviewJobV1) -> PreviewPendingDescriptorV1:
        status = (
            job.status.value
            if job.status
            in {PreviewJobStatus.QUEUED, PreviewJobStatus.RUNNING, PreviewJobStatus.FAILED}
            else "QUEUED"
        )
        return PreviewPendingDescriptorV1(
            status=status,
            artifact_key=job.artifact_key,
            job_id=job.job_id,
            status_url=(
                f"/api/v1/previews/jobs/{job.job_id}?"
                + urlencode({"project_id": job.scope.project_id})
            ),
            retry_after_seconds=self._retry_after_seconds,
            error_code=job.error_code,
        )

    @staticmethod
    def _validate_scope(scope: PreviewScopeV1, request: PreviewRequestV1) -> None:
        if scope.project_id != request.project_id:
            raise problem(
                status=403,
                code="PREVIEW_SCOPE_MISMATCH",
                title="Preview scope mismatch",
                detail="The request project does not match the authorized scope.",
            )

    def _now(self) -> datetime:
        now = self._clock()
        return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)


class PreviewGenerationService:
    """Media-worker data plane: bounded read -> one encoder -> immutable publication."""

    def __init__(
        self,
        *,
        step_reader: StepReaderPort,
        exclusions: EffectiveExclusionPort,
        encoder: MediaEncoderPort,
        repository: PreviewRepositoryPort,
        store: PreviewArtifactStorePort,
        profiles: PreviewProfileCatalog = DEFAULT_PREVIEW_PROFILES,
        artifact_ttl: timedelta = timedelta(days=30),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._step_reader = step_reader
        self._exclusions = exclusions
        self._encoder = encoder
        self._repository = repository
        self._store = store
        self._profiles = profiles
        self._artifact_ttl = artifact_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def generate(
        self,
        scope: PreviewScopeV1,
        request: PreviewRequestV1,
        *,
        job_id: str | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> PreviewArtifactV1:
        if scope.project_id != request.project_id:
            raise ValueError("preview generation request does not match its scope")
        self._profiles.get(request.profile_id)
        ranges: tuple[StepRangeV1, ...] = ()
        effective_hash: str | None = None
        if request.view_mode is not ViewMode.ORIGINAL:
            ranges = normalize_ranges(
                self._exclusions.effective_ranges(
                    project_id=request.project_id,
                    rollout_id=request.rollout_id,
                    annotation_revision=request.annotation_revision,
                )
            )
            effective_hash = exclusion_content_hash(ranges)
        artifact_key = preview_artifact_key(
            request, effective_exclusion_hash=effective_hash
        )
        now = self._now()
        # Temporal activity retries reuse the durable job. Re-enter RUNNING before
        # artifact reconciliation so ensure_artifact_job observes that active row
        # instead of creating a competing retry job.
        supplied_job = None
        if job_id is not None:
            supplied_job = self._repository.get_job(scope, job_id)
            if supplied_job is None or supplied_job.artifact_key != artifact_key:
                raise RuntimeError("preview generation job does not match the artifact")
            self._repository.mark_job_running(scope, job_id, now=now)
        artifact, ensured_job = self._repository.ensure_artifact_job(
            scope=scope,
            artifact_key=artifact_key,
            request=request,
            pipeline_revision=PREVIEW_PIPELINE_REVISION,
            rebuild_source_id=(
                f"lance:{request.dataset_id}:{request.lance_version}:{request.rollout_id}"
            ),
            artifact_ttl=self._artifact_ttl,
            now=now,
        )
        if artifact.status is PreviewArtifactStatus.READY:
            return artifact
        active_job = supplied_job or ensured_job
        if active_job is None or active_job.artifact_key != artifact_key:
            raise RuntimeError("preview generation job does not match the artifact")
        if supplied_job is None:
            self._repository.mark_job_running(scope, active_job.job_id, now=now)
        encoded = None
        started = time.perf_counter()
        PREVIEW_JOBS_RUNNING.inc()
        try:
            source = iter(
                self._step_reader.read_steps(
                    project_id=request.project_id,
                    dataset_id=request.dataset_id,
                    rollout_id=request.rollout_id,
                    lance_version=request.lance_version,
                    camera_id=request.camera_id,
                    start_step=request.start_step,
                    end_step=request.end_step,
                )
            )
            try:
                first = next(source)
            except StopIteration:
                raise problem(
                    status=404,
                    code="PREVIEW_STEPS_NOT_FOUND",
                    title="Preview source not found",
                    detail="No source steps matched the requested preview window.",
                ) from None
            stream = RenderFrameStream(
                chain((first,), source),
                ranges=ranges,
                view_mode=request.view_mode,
                frequency_hz=request.frequency_hz,
            )
            encoded = self._encoder.encode(
                cache_key=artifact_key,
                request=request,
                frames=stream,
                cancelled=cancelled,
            )
            self._repository.update_job_progress(scope, active_job.job_id, progress=80)
            publication = self._store.publish(
                project_id=request.project_id,
                artifact_key=artifact_key,
                encoded=encoded,
            )
            # Persist the exact object receipt before the READY transition. A
            # subsequent database failure therefore leaves a FAILED artifact that
            # the manifest-driven orphan collector can safely retry.
            self._repository.record_publication(
                scope=scope,
                artifact_key=artifact_key,
                publication=publication,
            )
            PREVIEW_FRAMES.labels(profile_id=request.profile_id).inc(encoded.frame_count)
            PREVIEW_OUTPUT_BYTES.inc(publication.total_bytes)
            PREVIEW_PEAK_IN_FLIGHT_FRAMES.set(1 if encoded.frame_count else 0)
            return self._repository.mark_ready(
                scope=scope,
                job_id=active_job.job_id,
                artifact_key=artifact_key,
                publication=publication,
                frame_count=encoded.frame_count,
                duration_seconds=encoded.duration_seconds,
                now=self._now(),
            )
        except BaseException as exc:
            PREVIEW_JOBS_FAILED.inc()
            error_code = (
                exc.problem.code
                if isinstance(exc, ProblemException)
                else getattr(exc, "code", type(exc).__name__.upper())
            )
            self._repository.mark_failed(
                scope=scope,
                job_id=active_job.job_id,
                artifact_key=artifact_key,
                error_code=str(error_code)[:128],
                now=self._now(),
            )
            raise
        finally:
            PREVIEW_JOBS_RUNNING.dec()
            PREVIEW_JOBS_QUEUED.set(0)
            PREVIEW_GENERATION_SECONDS.labels(profile_id=request.profile_id).observe(
                time.perf_counter() - started
            )
            if encoded is not None:
                self._encoder.cleanup(encoded)

    def _now(self) -> datetime:
        now = self._clock()
        return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
