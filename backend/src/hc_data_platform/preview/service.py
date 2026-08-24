from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from hc_data_platform.core.errors import problem

from .models import (
    PlaceholderDescriptorV1,
    PreviewCacheRecordV1,
    PreviewDescriptorV1,
    PreviewFrameV1,
    PreviewRequestV1,
    RenderFrameV1,
    StepRangeV1,
    TimelineMappingV1,
    TimelineSegmentV1,
    ViewMode,
)
from .ports import (
    EffectiveExclusionPort,
    MediaEncoderPort,
    PreviewCachePort,
    PreviewMediaReaderPort,
    StepReaderPort,
    UrlSignerPort,
)


def preview_cache_key(request: PreviewRequestV1) -> str:
    payload = request.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


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


def _is_excluded(step_index: int, ranges: Sequence[StepRangeV1]) -> bool:
    return any(item.start_step <= step_index < item.end_step for item in ranges)


def build_render_frames(
    *,
    frames: Sequence[PreviewFrameV1],
    ranges: Sequence[StepRangeV1],
    view_mode: ViewMode,
) -> tuple[RenderFrameV1, ...]:
    ordered = sorted(frames, key=lambda item: item.step_index)
    if len({item.step_index for item in ordered}) != len(ordered):
        raise problem(
            status=409,
            code="DUPLICATE_PREVIEW_STEP",
            title="Duplicate preview step",
            detail="The step reader returned more than one frame for a source step.",
        )

    rendered: list[RenderFrameV1] = []
    for source in ordered:
        excluded = _is_excluded(source.step_index, ranges)
        if view_mode is ViewMode.EDITED and excluded:
            continue
        placeholder = None
        if not source.valid or source.image_ref is None:
            reason = source.invalid_reason or "image data is missing or invalid"
            placeholder = PlaceholderDescriptorV1(
                invalid_reason=reason,
                playback_frame=len(rendered),
                step_index=source.step_index,
            )
        rendered.append(
            RenderFrameV1(
                playback_frame=len(rendered),
                step_index=source.step_index,
                timestamp_ns=source.timestamp_ns,
                source_timestamp_ns=source.source_timestamp_ns,
                image_ref=source.image_ref if placeholder is None else None,
                excluded=excluded if view_mode is ViewMode.COMPARE else False,
                placeholder=placeholder,
            )
        )
    return tuple(rendered)


def build_timeline(frames: Sequence[RenderFrameV1], *, frequency_hz: float) -> TimelineMappingV1:
    if not frames:
        return TimelineMappingV1(frequency_hz=frequency_hz)

    segments: list[TimelineSegmentV1] = []
    group_start = frames[0]
    previous = frames[0]
    for current in frames[1:]:
        contiguous = (
            current.playback_frame == previous.playback_frame + 1
            and current.step_index == previous.step_index + 1
            and current.excluded == previous.excluded
        )
        if not contiguous:
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


class PreviewService:
    def __init__(
        self,
        *,
        step_reader: StepReaderPort,
        exclusions: EffectiveExclusionPort,
        encoder: MediaEncoderPort,
        cache: PreviewCachePort,
        signer: UrlSignerPort,
        media_reader: PreviewMediaReaderPort | None = None,
        cache_ttl: timedelta = timedelta(hours=24),
        signed_url_ttl: timedelta = timedelta(minutes=15),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._step_reader = step_reader
        self._exclusions = exclusions
        self._encoder = encoder
        self._cache = cache
        self._signer = signer
        self._media_reader = media_reader
        self._cache_ttl = cache_ttl
        self._signed_url_ttl = signed_url_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create(self, request: PreviewRequestV1) -> PreviewDescriptorV1:
        started = time.monotonic()
        outcome = "success"
        try:
            return self._create(request)
        except Exception:
            outcome = "failure"
            raise
        finally:
            from hc_data_platform.core.observability import (
                TRANSCODE_DURATION,
                locator_workflow_id,
            )

            TRANSCODE_DURATION.labels(
                project_id=request.project_id,
                resource_id=request.rollout_id,
                workflow_id=locator_workflow_id("preview", request.project_id, request.rollout_id),
                outcome=outcome,
                view_mode=request.view_mode.value,
            ).observe(time.monotonic() - started)

    def _create(self, request: PreviewRequestV1) -> PreviewDescriptorV1:
        now = self._now()
        cache_key = preview_cache_key(request)
        record = self._cache.get(cache_key, now=now)
        if record is None:
            source_frames = self._step_reader.read_steps(
                project_id=request.project_id,
                dataset_id=request.dataset_id,
                rollout_id=request.rollout_id,
                lance_version=request.lance_version,
                camera_id=request.camera_id,
                start_step=request.start_step,
                end_step=request.end_step,
            )
            if not source_frames:
                raise problem(
                    status=404,
                    code="PREVIEW_STEPS_NOT_FOUND",
                    title="Preview source not found",
                    detail="No source steps matched the requested preview window.",
                )
            ranges = normalize_ranges(
                self._exclusions.effective_ranges(
                    project_id=request.project_id,
                    rollout_id=request.rollout_id,
                    annotation_revision=request.annotation_revision,
                )
            )
            render_frames = build_render_frames(
                frames=source_frames,
                ranges=ranges,
                view_mode=request.view_mode,
            )
            timeline = build_timeline(render_frames, frequency_hz=request.frequency_hz)
            artifact = self._encoder.encode(
                cache_key=cache_key,
                request=request,
                frames=render_frames,
            )
            record = PreviewCacheRecordV1(
                cache_key=cache_key,
                session_id=str(uuid4()),
                request=request,
                artifact=artifact,
                timeline=timeline,
                placeholders=tuple(
                    frame.placeholder for frame in render_frames if frame.placeholder is not None
                ),
                placeholder_count=sum(frame.placeholder is not None for frame in render_frames),
                created_at=now,
                cache_expires_at=now + self._cache_ttl,
            )
            self._cache.put(record)
        return self._descriptor(record, now=now)

    def get(self, session_id: str) -> PreviewDescriptorV1:
        now = self._now()
        record = self._cache.get_by_session(session_id, now=now)
        if record is None:
            raise problem(
                status=404,
                code="PREVIEW_SESSION_NOT_FOUND",
                title="Preview session not found",
                detail="The preview session does not exist or its cache has expired.",
            )
        return self._descriptor(record, now=now)

    def resolve_media(
        self,
        *,
        session_id: str,
        asset_name: str,
        expires: int,
        signature: str,
    ) -> tuple[PreviewCacheRecordV1, Path]:
        """Validate a short-lived media capability and resolve its local cache asset."""

        now = self._now()
        record = self._cache.get_by_session(session_id, now=now)
        if record is None:
            raise problem(
                status=404,
                code="PREVIEW_SESSION_NOT_FOUND",
                title="Preview session not found",
                detail="The preview session does not exist or its cache has expired.",
            )
        if not self._signer.verify(
            session_id=session_id,
            artifact_uri=record.artifact.artifact_uri,
            asset_name=asset_name,
            expires=expires,
            signature=signature,
            now=now,
        ):
            raise problem(
                status=403,
                code="PREVIEW_MEDIA_FORBIDDEN",
                title="Preview media is unavailable",
                detail="The preview media capability is invalid or has expired.",
            )
        if self._media_reader is None:
            raise problem(
                status=404,
                code="PREVIEW_MEDIA_NOT_FOUND",
                title="Preview media not found",
                detail="The requested preview asset is not available.",
            )
        try:
            return record, self._media_reader.resolve(record, asset_name=asset_name)
        except FileNotFoundError:
            raise problem(
                status=404,
                code="PREVIEW_MEDIA_NOT_FOUND",
                title="Preview media not found",
                detail="The requested preview asset is not available.",
            ) from None

    def sign_media_asset(
        self,
        record: PreviewCacheRecordV1,
        *,
        asset_name: str,
        expires: int,
    ) -> str:
        return self._signer.sign(
            session_id=record.session_id,
            artifact_uri=record.artifact.artifact_uri,
            asset_name=asset_name,
            expires_at=datetime.fromtimestamp(expires, tz=timezone.utc),
        )

    def _descriptor(self, record: PreviewCacheRecordV1, *, now: datetime) -> PreviewDescriptorV1:
        signed_url_expires_at = min(now + self._signed_url_ttl, record.cache_expires_at)
        request = record.request
        return PreviewDescriptorV1(
            session_id=record.session_id,
            cache_key=record.cache_key,
            project_id=request.project_id,
            dataset_id=request.dataset_id,
            rollout_id=request.rollout_id,
            lance_version=request.lance_version,
            annotation_revision=request.annotation_revision,
            camera_id=request.camera_id,
            view_mode=request.view_mode,
            encoding_profile=request.encoding_profile,
            playlist_url=self._signer.sign(
                session_id=record.session_id,
                artifact_uri=record.artifact.artifact_uri,
                asset_name="index.m3u8",
                expires_at=signed_url_expires_at,
            ),
            media_type=record.artifact.media_type,
            frame_count=record.artifact.frame_count,
            placeholder_count=record.placeholder_count,
            placeholders=record.placeholders,
            duration_seconds=record.artifact.duration_seconds,
            timeline=record.timeline,
            cache_expires_at=record.cache_expires_at,
            signed_url_expires_at=signed_url_expires_at,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("preview clock must return a timezone-aware datetime")
        return value
