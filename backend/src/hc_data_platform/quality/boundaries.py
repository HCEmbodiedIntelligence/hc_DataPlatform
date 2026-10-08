"""Separate normal recording boundaries from gaps inside an active topic."""

from collections.abc import Iterable

from .models import (
    FindingSeverity,
    QcFinding,
    QcReportV1,
    QualityCode,
    QualityInputV1,
    TopicTimingMetricsV1,
    TopicTimingProfileV1,
)

BOUNDARY_QUALITY_ENGINE_VERSION = "be06-qc/3"
BOUNDARY_CODES = frozenset({QualityCode.LEADING_IDLE, QualityCode.TRAILING_IDLE})
_SECOND = 1_000_000_000


def active_topic_timing(
    *,
    topic: str,
    timestamps: Iterable[int],
    data: QualityInputV1,
    thresholds: TopicTimingProfileV1,
    metrics: TopicTimingMetricsV1,
    findings: list[QcFinding],
    required: bool,
) -> tuple[TopicTimingMetricsV1, list[QcFinding]]:
    """Consume sorted unique timestamps once; retain every significant interior gap."""
    first = previous = None
    maximum_gap = maximum_missing = 0
    result = [
        f
        for f in findings
        if f.code
        not in {
            QualityCode.CONSECUTIVE_FRAMES_MISSING,
            QualityCode.GAP_EXCESSIVE,
            QualityCode.FREQUENCY_LOW,
            QualityCode.COVERAGE_LOW,
        }
    ]
    hz = thresholds.target_frequency_hz
    period = (_SECOND + hz - 1) // hz

    def slot(stamp: int) -> int:
        return ((stamp - data.start_ns) * hz + _SECOND // 2) // _SECOND

    for stamp in timestamps:
        if first is None:
            first = stamp
        if previous is not None:
            maximum_gap = max(maximum_gap, stamp - previous)
            missing = max(0, slot(stamp) - slot(previous) - 1)
            maximum_missing = max(maximum_missing, missing)
            if missing > thresholds.maximum_consecutive_missing_risk:
                result.append(
                    QcFinding(
                        code=QualityCode.CONSECUTIVE_FRAMES_MISSING,
                        severity=FindingSeverity.WARNING,
                        message=(
                            "interior missing samples require human review; no automatic removal"
                        ),
                        topic=topic,
                        start_ns=previous,
                        end_ns=stamp,
                        observed=missing,
                        threshold=thresholds.maximum_consecutive_missing_risk,
                    )
                )
        previous = stamp
    if first is None or previous is None:
        return metrics, result
    coverage_end = min(data.end_ns, previous + period)
    if required:
        for code, start, end in (
            (QualityCode.LEADING_IDLE, data.start_ns, first),
            (QualityCode.TRAILING_IDLE, coverage_end, data.end_ns),
        ):
            if end > start:
                result.append(
                    QcFinding(
                        code=code,
                        severity=FindingSeverity.INFO,
                        message=(
                            "normal recording boundary; "
                            "trim all modalities to the common active window"
                        ),
                        topic=topic,
                        start_ns=start,
                        end_ns=end,
                        observed=end - start,
                        threshold="duration_ns; synchronized boundary trim",
                    )
                )
    duration = coverage_end - first
    frequency = metrics.unique_frame_count * _SECOND / duration
    expected = max(1, (duration * hz + _SECOND - 1) // _SECOND)
    if frequency < thresholds.minimum_frequency_hz_risk:
        result.append(
            QcFinding(
                code=QualityCode.FREQUENCY_LOW,
                severity=FindingSeverity.WARNING,
                message="topic frequency inside its active window is below the profile limit",
                topic=topic,
                start_ns=first,
                end_ns=coverage_end,
                observed=round(frequency, 9),
                threshold=thresholds.minimum_frequency_hz_risk,
            )
        )
    return metrics.model_copy(
        update={
            "actual_frequency_hz": round(frequency, 9),
            "expected_frame_count": expected,
            "coverage_ratio": round(min(1.0, metrics.unique_frame_count / expected), 9),
            "maximum_gap_ns": maximum_gap,
            "maximum_consecutive_missing": maximum_missing,
        }
    ), result


def common_window(start_ns: int, end_ns: int, findings: Iterable[QcFinding]) -> tuple[int, int]:
    for finding in findings:
        if finding.code == QualityCode.LEADING_IDLE:
            start_ns = max(start_ns, finding.end_ns)
        elif finding.code == QualityCode.TRAILING_IDLE:
            end_ns = min(end_ns, finding.start_ns)
    return start_ns, end_ns


def conversion_window(report: QcReportV1, frequency_hz: int) -> tuple[int, int]:
    """Keep the original grid phase while starting derived step/frame indices at zero."""
    if report.engine_version != BOUNDARY_QUALITY_ENGINE_VERSION:
        return report.start_ns, report.end_ns
    start, end = common_window(report.start_ns, report.end_ns, report.findings)
    first_step = ((start - report.start_ns) * frequency_hz + _SECOND - 1) // _SECOND
    start = report.start_ns + (first_step * _SECOND) // frequency_hz
    if start >= end:
        raise ValueError("required modalities have no common conversion frame")
    return start, end
