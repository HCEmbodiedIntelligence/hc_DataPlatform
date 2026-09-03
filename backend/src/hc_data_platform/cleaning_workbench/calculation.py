"""Deterministic, side-effect-free P11 EDL calculations.

The service persists the user supplied EDL as an immutable revision, then uses
these helpers to calculate the only server-authoritative impact projection. No
asset locator or media payload is accepted or produced here.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from hc_data_platform.security.audit import canonical_hash

from .models import (
    CleaningOperation,
    CleaningStream,
    CleaningSummary,
    CleaningValidation,
    CleaningValidationIssue,
    DisableChannelOperation,
    ExcludeRangeOperation,
    InvalidateEpisodeOperation,
    InvalidMaskOperation,
    SourceToOutputMap,
    SourceToOutputMapSegment,
    SplitOperation,
    TimeOffsetOperation,
    TrimOperation,
)

EMPTY_EDL_HASH = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def operation_hash(operations: Iterable[CleaningOperation]) -> str:
    """Return the canonical EDL identity, including disabled operations."""

    serialized = tuple(operation.model_dump(mode="json") for operation in operations)
    if not serialized:
        return EMPTY_EDL_HASH
    return f"sha256:{canonical_hash(serialized)}"


def draft_etag(*, draft_id: str, workbench_version: int) -> str:
    return f'"{draft_id}:workbench:{workbench_version}"'


def calculate_edl(
    *,
    operations: tuple[CleaningOperation, ...],
    streams: tuple[CleaningStream, ...],
    edl_revision: int,
    operation_signature: str,
    calculated_at: datetime,
) -> tuple[CleaningValidation, CleaningSummary, SourceToOutputMap | None]:
    """Validate half-open EDL operations and calculate a safe logical overlay.

    Stream durations form a single, origin-zero time domain. This matches the
    P11 wire contract: operations are not raw storage positions, and the output
    map describes only retained half-open intervals. A semantic error is
    representable as a persisted failed validation; save itself never silently
    normalizes an out-of-domain user interval.
    """

    duration = min((int(stream.duration_ns) for stream in streams), default=0)
    known_streams = {stream.stream_id for stream in streams}
    issues: list[CleaningValidationIssue] = []

    seen_ids: set[str] = set()
    for index, operation in enumerate(operations):
        if operation.sequence_no != index:
            issues.append(
                _issue(
                    code="EDL_SEQUENCE_INVALID",
                    operation_id=operation.id,
                    pointer=f"/operations/{index}/sequence_no",
                    message="Operation sequence_no must be contiguous from zero.",
                )
            )
        if operation.id in seen_ids:
            issues.append(
                _issue(
                    code="EDL_OPERATION_ID_DUPLICATE",
                    operation_id=operation.id,
                    pointer=f"/operations/{index}/id",
                    message="Operation identifiers must be unique within an EDL revision.",
                )
            )
        seen_ids.add(operation.id)

    enabled_trims = tuple(
        operation
        for operation in operations
        if operation.enabled and isinstance(operation, TrimOperation)
    )
    if len(enabled_trims) > 1:
        for operation in enabled_trims[1:]:
            issues.append(
                _issue(
                    code="EDL_TRIM_MULTIPLE",
                    operation_id=operation.id,
                    pointer=None,
                    message="V1 permits at most one enabled TRIM operation.",
                )
            )

    domain_start, domain_end = 0, duration
    if enabled_trims:
        trim = enabled_trims[0]
        if _range_in_domain(trim.start_ns, trim.end_ns, duration):
            domain_start, domain_end = int(trim.start_ns), int(trim.end_ns)
        else:
            issues.append(_range_issue(trim.id, "TRIM", duration))

    excluded: list[tuple[int, int]] = []
    invalid_masks: list[tuple[int, int]] = []
    splits: list[int] = []
    disabled_count = 0
    invalidated = False
    for index, operation in enumerate(operations):
        if not operation.enabled:
            continue
        if isinstance(operation, (ExcludeRangeOperation, InvalidMaskOperation)):
            start, end = int(operation.start_ns), int(operation.end_ns)
            if not _range_in_domain(operation.start_ns, operation.end_ns, duration):
                issues.append(_range_issue(operation.id, operation.type, duration))
                continue
            if start < domain_start or end > domain_end:
                issues.append(
                    _issue(
                        code="EDL_RANGE_OUTSIDE_TRIM",
                        operation_id=operation.id,
                        pointer=f"/operations/{index}",
                        message=(
                            "A range operation must be fully contained by the enabled TRIM domain."
                        ),
                    )
                )
                continue
            if isinstance(operation, ExcludeRangeOperation):
                excluded.append((start, end))
            else:
                if (
                    operation.episode_stream_id is not None
                    and operation.episode_stream_id not in known_streams
                ):
                    issues.append(_unknown_stream_issue(operation.id, operation.episode_stream_id))
                invalid_masks.append((start, end))
        elif isinstance(operation, SplitOperation):
            point = int(operation.at_ns)
            if not domain_start < point < domain_end:
                issues.append(
                    _issue(
                        code="EDL_SPLIT_OUTSIDE_DOMAIN",
                        operation_id=operation.id,
                        pointer=f"/operations/{index}/at_ns",
                        message=(
                            "A split point must be strictly inside the retained half-open domain."
                        ),
                    )
                )
            else:
                splits.append(point)
        elif isinstance(operation, TimeOffsetOperation):
            if operation.episode_stream_id not in known_streams:
                issues.append(_unknown_stream_issue(operation.id, operation.episode_stream_id))
            if (
                operation.reference_stream_id is not None
                and operation.reference_stream_id not in known_streams
            ):
                issues.append(_unknown_stream_issue(operation.id, operation.reference_stream_id))
        elif isinstance(operation, DisableChannelOperation):
            if operation.episode_stream_id not in known_streams:
                issues.append(_unknown_stream_issue(operation.id, operation.episode_stream_id))
            else:
                disabled_count += 1
        elif isinstance(operation, InvalidateEpisodeOperation):
            invalidated = True

    excluded_union = _union(excluded)
    invalid_union = _union(invalid_masks)
    removed_union = _union((*excluded_union, *invalid_union))
    retained = _subtract((domain_start, domain_end), removed_union)
    if invalidated:
        retained = ()
    if not retained:
        issues.append(
            _issue(
                code="EDL_OUTPUT_EMPTY",
                operation_id=None,
                pointer=None,
                message="The enabled operations leave no retained output interval.",
            )
        )

    output_duration = sum(end - start for start, end in retained)
    segments = _segments(retained=retained, split_points=splits)
    summary = CleaningSummary(
        source_duration_ns=str(duration),
        trimmed_domain_duration_ns=str(max(0, domain_end - domain_start)),
        excluded_union_duration_ns=str(_length(excluded_union)),
        invalid_mask_union_duration_ns=str(_length(invalid_union)),
        output_duration_ns=str(output_duration),
        output_segment_count=str(len(segments)),
        disabled_stream_count=str(disabled_count),
        # P11 creates a logical, non-destructive overlay. Physical materialization
        # is explicitly a later job and therefore no byte count is invented here.
        reused_source_bytes="0",
        new_derived_bytes="0",
        reuse_rate="1" if output_duration == duration else "0",
        requires_materialization=bool(operations),
        estimate_status="ESTIMATED",
        calculated_at=calculated_at,
    )
    validation = CleaningValidation(
        status="FAILED" if issues else "PASSED",
        issues=tuple(issues),
        validated_edl_revision=str(edl_revision),
        validated_operation_hash=operation_signature,
    )
    mapping = None if issues or not segments else _map(segments, operation_signature)
    return validation, summary, mapping


def _issue(
    *, code: str, operation_id: str | None, pointer: str | None, message: str
) -> CleaningValidationIssue:
    return CleaningValidationIssue(
        code=code,
        severity="BLOCKER",
        operation_id=operation_id,
        json_pointer=pointer,
        message=message,
    )


def _range_issue(operation_id: str, operation_type: str, duration: int) -> CleaningValidationIssue:
    return _issue(
        code="EDL_RANGE_OUT_OF_BOUNDS",
        operation_id=operation_id,
        pointer=None,
        message=f"{operation_type} must remain within [0,{duration}).",
    )


def _unknown_stream_issue(operation_id: str, stream_id: str) -> CleaningValidationIssue:
    return _issue(
        code="EDL_STREAM_UNKNOWN",
        operation_id=operation_id,
        pointer=None,
        message=f"The operation names unknown stream {stream_id!r}.",
    )


def _range_in_domain(start: str, end: str, duration: int) -> bool:
    return 0 <= int(start) < int(end) <= duration


def _union(intervals: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return tuple(merged)


def _subtract(
    domain: tuple[int, int], removed: tuple[tuple[int, int], ...]
) -> tuple[tuple[int, int], ...]:
    cursor = domain[0]
    retained: list[tuple[int, int]] = []
    for start, end in removed:
        if end <= cursor or start >= domain[1]:
            continue
        clipped_start, clipped_end = max(start, cursor), min(end, domain[1])
        if cursor < clipped_start:
            retained.append((cursor, clipped_start))
        cursor = max(cursor, clipped_end)
    if cursor < domain[1]:
        retained.append((cursor, domain[1]))
    return tuple(retained)


def _segments(
    *, retained: tuple[tuple[int, int], ...], split_points: Iterable[int]
) -> tuple[tuple[int, int], ...]:
    points = tuple(sorted(set(split_points)))
    result: list[tuple[int, int]] = []
    for start, end in retained:
        cursor = start
        for point in points:
            if cursor < point < end:
                result.append((cursor, point))
                cursor = point
        if cursor < end:
            result.append((cursor, end))
    return tuple(result)


def _map(segments: tuple[tuple[int, int], ...], operation_signature: str) -> SourceToOutputMap:
    output_cursor = 0
    result: list[SourceToOutputMapSegment] = []
    # The preview is an overlay, so its virtual output revision is stable and
    # safely typed without claiming a physical dataset revision exists yet.
    preview_revision_id = f"revision_preview_{operation_signature.removeprefix('sha256:')[:32]}"
    for start, end in segments:
        width = end - start
        result.append(
            SourceToOutputMapSegment(
                source_start_ns=str(start),
                source_end_ns=str(end),
                output_revision_id=preview_revision_id,
                output_start_ns=str(output_cursor),
                output_end_ns=str(output_cursor + width),
            )
        )
        output_cursor += width
    return SourceToOutputMap(segments=tuple(result), mapping_version="1")


def _length(intervals: Iterable[tuple[int, int]]) -> int:
    return sum(end - start for start, end in intervals)
