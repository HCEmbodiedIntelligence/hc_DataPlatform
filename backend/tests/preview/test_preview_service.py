from datetime import datetime, timedelta, timezone

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.preview.memory import (
    HmacUrlSigner,
    InMemoryExclusionReader,
    InMemoryMediaEncoder,
    InMemoryPreviewCache,
    InMemoryStepReader,
)
from hc_data_platform.preview.models import (
    EncodingProfileV1,
    PreviewFrameV1,
    PreviewRequestV1,
    StepRangeV1,
    ViewMode,
)
from hc_data_platform.preview.service import PreviewService, preview_cache_key

NOW = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)


def frames(count: int = 6) -> list[PreviewFrameV1]:
    return [
        PreviewFrameV1(
            rollout_id="rollout-1",
            step_index=index,
            timestamp_ns=index * 33_333_333,
            source_timestamp_ns=index * 33_333_333,
            image_ref=f"lance://camera/{index}",
        )
        for index in range(count)
    ]


def request(mode: ViewMode, *, revision: int = 1, camera: str = "front") -> PreviewRequestV1:
    return PreviewRequestV1(
        project_id="project-1",
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        lance_version="v7",
        annotation_revision=revision,
        camera_id=camera,
        view_mode=mode,
        frequency_hz=2,
    )


def build_service(
    mode_ranges: list[StepRangeV1] | None = None,
    source_frames: list[PreviewFrameV1] | None = None,
) -> tuple[PreviewService, InMemoryStepReader, InMemoryMediaEncoder]:
    reader = InMemoryStepReader(source_frames if source_frames is not None else frames())
    encoder = InMemoryMediaEncoder()
    service = PreviewService(
        step_reader=reader,
        exclusions=InMemoryExclusionReader(mode_ranges or []),
        encoder=encoder,
        cache=InMemoryPreviewCache(),
        signer=HmacUrlSigner(b"test"),
        clock=lambda: NOW,
    )
    return service, reader, encoder


def test_original_preserves_source_step_to_frame_mapping() -> None:
    service, _, encoder = build_service([StepRangeV1(start_step=2, end_step=4)])
    result = service.create(request(ViewMode.ORIGINAL))

    assert [item.step_index for item in encoder.calls[0]] == list(range(6))
    assert all(not item.excluded for item in encoder.calls[0])
    assert result.timeline.segments[0].source_start_step == 0
    assert result.timeline.segments[0].source_end_step == 6
    assert result.timeline.segments[0].playback_end_seconds == 3


def test_edited_removes_half_open_ranges_and_maps_compressed_time() -> None:
    service, _, encoder = build_service([StepRangeV1(start_step=2, end_step=4)])
    result = service.create(request(ViewMode.EDITED))

    assert [item.step_index for item in encoder.calls[0]] == [0, 1, 4, 5]
    assert [(s.source_start_step, s.source_end_step) for s in result.timeline.segments] == [
        (0, 2),
        (4, 6),
    ]
    assert result.timeline.segments[1].playback_start_seconds == 1
    assert result.timeline.segments[1].playback_end_seconds == 2


def test_compare_keeps_timeline_and_marks_excluded_segment() -> None:
    service, _, encoder = build_service([StepRangeV1(start_step=2, end_step=4)])
    result = service.create(request(ViewMode.COMPARE))

    assert [item.step_index for item in encoder.calls[0]] == list(range(6))
    assert [item.excluded for item in encoder.calls[0]] == [False, False, True, True, False, False]
    assert [segment.excluded for segment in result.timeline.segments] == [False, True, False]


def test_invalid_frame_becomes_explicit_placeholder() -> None:
    source = frames(2)
    source[1] = source[1].model_copy(
        update={"valid": False, "image_ref": None, "invalid_reason": "camera decode failed"}
    )
    service, _, encoder = build_service(source_frames=source)
    result = service.create(request(ViewMode.ORIGINAL))

    placeholder = encoder.calls[0][1].placeholder
    assert placeholder is not None
    assert placeholder.code == "INVALID_IMAGE_STEP"
    assert placeholder.invalid_reason == "camera decode failed"
    assert placeholder.playback_frame == 1
    assert result.placeholders == (placeholder,)
    assert result.placeholder_count == 1


def test_cache_key_changes_for_every_source_identity_dimension() -> None:
    base = request(ViewMode.ORIGINAL)
    assert preview_cache_key(base) != preview_cache_key(request(ViewMode.ORIGINAL, revision=2))
    assert preview_cache_key(base) != preview_cache_key(request(ViewMode.ORIGINAL, camera="wrist"))
    assert preview_cache_key(base) != preview_cache_key(request(ViewMode.EDITED))
    assert preview_cache_key(base) != preview_cache_key(
        base.model_copy(update={"rollout_id": "rollout-2"})
    )
    assert preview_cache_key(base) != preview_cache_key(
        base.model_copy(update={"lance_version": "v8"})
    )
    assert preview_cache_key(base) != preview_cache_key(
        base.model_copy(update={"start_step": 1, "end_step": 4})
    )
    assert preview_cache_key(base) != preview_cache_key(
        base.model_copy(
            update={
                "encoding_profile": EncodingProfileV1(
                    name="mobile-preview-v1",
                    width=640,
                    height=360,
                )
            }
        )
    )


def test_cache_hit_does_not_read_or_encode_again_and_query_refreshes_signature() -> None:
    reader = InMemoryStepReader(frames())
    encoder = InMemoryMediaEncoder()
    cache = InMemoryPreviewCache()
    current = [NOW]
    exclusions = InMemoryExclusionReader()
    service = PreviewService(
        step_reader=reader,
        exclusions=exclusions,
        encoder=encoder,
        cache=cache,
        signer=HmacUrlSigner(b"test"),
        clock=lambda: current[0],
    )
    first = service.create(request(ViewMode.ORIGINAL))
    second = service.create(request(ViewMode.ORIGINAL))
    current[0] += timedelta(minutes=5)
    queried = service.get(first.session_id)

    assert first.session_id == second.session_id == queried.session_id
    assert reader.calls == 1
    assert exclusions.calls == 1
    assert len(encoder.calls) == 1
    assert queried.signed_url_expires_at == NOW + timedelta(minutes=20)
    assert queried.cache_expires_at == NOW + timedelta(hours=24)


def test_expired_cache_is_not_queryable() -> None:
    current = [NOW]
    service = PreviewService(
        step_reader=InMemoryStepReader(frames()),
        exclusions=InMemoryExclusionReader(),
        encoder=InMemoryMediaEncoder(),
        cache=InMemoryPreviewCache(),
        signer=HmacUrlSigner(),
        cache_ttl=timedelta(seconds=1),
        clock=lambda: current[0],
    )
    created = service.create(request(ViewMode.ORIGINAL))
    current[0] += timedelta(seconds=2)

    with pytest.raises(ProblemException) as captured:
        service.get(created.session_id)
    assert captured.value.problem.code == "PREVIEW_SESSION_NOT_FOUND"


def test_expired_cache_is_regenerated_and_revision_change_never_hits_old_cache() -> None:
    current = [NOW]
    reader = InMemoryStepReader(frames())
    encoder = InMemoryMediaEncoder()
    service = PreviewService(
        step_reader=reader,
        exclusions=InMemoryExclusionReader(),
        encoder=encoder,
        cache=InMemoryPreviewCache(),
        signer=HmacUrlSigner(),
        cache_ttl=timedelta(seconds=1),
        clock=lambda: current[0],
    )

    revision_one = service.create(request(ViewMode.ORIGINAL, revision=1))
    revision_two = service.create(request(ViewMode.ORIGINAL, revision=2))
    assert revision_one.cache_key != revision_two.cache_key
    assert reader.calls == 2
    assert len(encoder.calls) == 2

    current[0] += timedelta(seconds=2)
    regenerated = service.create(request(ViewMode.ORIGINAL, revision=1))
    assert regenerated.session_id != revision_one.session_id
    assert reader.calls == 3
    assert len(encoder.calls) == 3


def test_duplicate_source_step_is_rejected() -> None:
    source = frames(2)
    source.append(source[1])
    service, _, _ = build_service(source_frames=source)

    with pytest.raises(ProblemException) as captured:
        service.create(request(ViewMode.ORIGINAL))
    assert captured.value.problem.code == "DUPLICATE_PREVIEW_STEP"
