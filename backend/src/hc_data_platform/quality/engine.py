"""Deterministic rule engine for timing, visual and robot-data quality."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable

from .models import (
    NANOSECONDS_PER_SECOND,
    FindingSeverity,
    JsonScalar,
    QcFinding,
    QcReportV1,
    QualityCode,
    QualityInputV1,
    QualityProfileV1,
    QualityStatus,
    QualitySummaryV1,
    TopicTimingMetricsV1,
    TopicTimingProfileV1,
)
from .ports import MetadataSink, QualityPersistenceError, ReportSink


class QualityEngine:
    """Evaluate a decoded rollout and optionally persist its report and summary."""

    def __init__(
        self,
        report_sink: ReportSink | None = None,
        metadata_sink: MetadataSink | None = None,
        *,
        engine_version: str | None = None,
    ) -> None:
        if (
            report_sink is not None
            and metadata_sink is None
            and isinstance(report_sink, MetadataSink)
        ):
            # Compatibility for the original in-memory fake, while the public ports remain split.
            metadata_sink = report_sink
        if (report_sink is None) != (metadata_sink is None):
            raise ValueError("report_sink and metadata_sink must be configured together")
        if engine_version is not None and not engine_version:
            raise ValueError("engine_version must not be empty")
        self._report_sink = report_sink
        self._metadata_sink = metadata_sink
        self._engine_version = engine_version

    def evaluate(self, data: QualityInputV1, profile: QualityProfileV1) -> QcReportV1:
        findings: list[QcFinding] = []
        metrics: list[TopicTimingMetricsV1] = []
        missing_required = profile.required_topics - data.topic_timestamps_ns.keys()
        for topic in sorted(missing_required):
            findings.append(
                self._finding(
                    QualityCode.REQUIRED_TOPIC_MISSING,
                    FindingSeverity.ERROR,
                    "required topic is absent",
                    topic,
                    data.start_ns,
                    data.end_ns,
                    observed=False,
                    threshold="required=true",
                )
            )

        evaluated_topics = set(data.topic_timestamps_ns) | set(profile.required_topics)
        for topic in sorted(evaluated_topics):
            topic_metrics, topic_findings = self._timing(
                topic,
                data.topic_timestamps_ns.get(topic, ()),
                data,
                profile.timing_for(topic),
            )
            metrics.append(topic_metrics)
            findings.extend(topic_findings)

        findings.extend(self._images(data, profile))
        findings.extend(self._joints(data, profile))
        findings.extend(self._actions(data, profile))
        findings.extend(self._point_clouds(data, profile))
        findings.extend(self._offsets(data, profile))
        findings.extend(self._complete_steps(data, profile))
        findings.sort(key=self._finding_key)

        report = QcReportV1.build(
            rollout_id=data.rollout_id,
            source_sha256=data.source_sha256,
            profile_id=profile.profile_id,
            profile_version=profile.profile_version,
            profile_sha256=profile.content_sha256(),
            engine_version=self._engine_version or profile.engine_version,
            start_ns=data.start_ns,
            end_ns=data.end_ns,
            status=self._status(findings),
            topic_metrics=tuple(metrics),
            findings=tuple(findings),
        )
        self._persist(report)
        return report

    def _persist(self, report: QcReportV1) -> None:
        if self._report_sink is None or self._metadata_sink is None:
            return
        try:
            self._report_sink.put_immutable(report)
        except Exception as error:
            raise QualityPersistenceError(
                "report", "failed to persist immutable QC report"
            ) from error
        try:
            self._metadata_sink.put_summary(QualitySummaryV1.from_report(report))
        except Exception as error:
            raise QualityPersistenceError(
                "metadata", "failed to update QC metadata summary"
            ) from error

    def _timing(
        self,
        topic: str,
        timestamps: tuple[int, ...],
        data: QualityInputV1,
        thresholds: TopicTimingProfileV1,
    ) -> tuple[TopicTimingMetricsV1, list[QcFinding]]:
        duration_ns = data.end_ns - data.start_ns
        unique = sorted(set(timestamps))
        counts = Counter(timestamps)
        duplicate_timestamps = sorted(timestamp for timestamp, count in counts.items() if count > 1)
        duplicates = len(timestamps) - len(unique)
        backward_pairs = [
            (left, right)
            for left, right in zip(timestamps, timestamps[1:], strict=False)
            if right < left
        ]
        intervals = [right - left for left, right in zip(unique, unique[1:], strict=False)]
        expected = max(
            1,
            self._ceil_div(
                duration_ns * thresholds.target_frequency_hz,
                NANOSECONDS_PER_SECOND,
            ),
        )
        frequency = len(unique) * NANOSECONDS_PER_SECOND / duration_ns
        coverage = min(1.0, len(unique) / expected)
        gap_start, gap_end, maximum_gap, missing_start, missing_end, missing = self._gap_metrics(
            unique,
            start_ns=data.start_ns,
            end_ns=data.end_ns,
            target_frequency_hz=thresholds.target_frequency_hz,
            expected_count=expected,
        )
        result: list[QcFinding] = []

        duplicate_severity = self._higher_is_worse(
            duplicates,
            thresholds.maximum_duplicate_timestamps_risk,
            thresholds.maximum_duplicate_timestamps_reject,
        )
        if duplicate_severity is not None:
            severity, threshold = duplicate_severity
            start_ns, end_ns = self._affected_range(
                duplicate_timestamps, data.start_ns, data.end_ns
            )
            result.append(
                self._finding(
                    QualityCode.TIMESTAMP_DUPLICATE,
                    severity,
                    "duplicate timestamps exceed the profile limit",
                    topic,
                    start_ns,
                    end_ns,
                    observed=duplicates,
                    threshold=threshold,
                )
            )

        if len(backward_pairs) > thresholds.maximum_backward_timestamps_reject:
            affected = [timestamp for pair in backward_pairs for timestamp in pair]
            start_ns, end_ns = self._affected_range(affected, data.start_ns, data.end_ns)
            result.append(
                self._finding(
                    QualityCode.TIMESTAMP_BACKWARD,
                    FindingSeverity.ERROR,
                    "timestamp order moves backwards",
                    topic,
                    start_ns,
                    end_ns,
                    observed=len(backward_pairs),
                    threshold=thresholds.maximum_backward_timestamps_reject,
                )
            )

        frequency_severity = self._lower_is_worse(
            frequency,
            thresholds.minimum_frequency_hz_risk,
            thresholds.minimum_frequency_hz_reject,
        )
        if frequency_severity is not None:
            severity, threshold = frequency_severity
            result.append(
                self._finding(
                    QualityCode.FREQUENCY_LOW,
                    severity,
                    "topic frequency is below the profile limit",
                    topic,
                    data.start_ns,
                    data.end_ns,
                    observed=self._rounded(frequency),
                    threshold=threshold,
                )
            )

        gap_severity = self._higher_is_worse(
            maximum_gap,
            thresholds.maximum_gap_ns_risk,
            thresholds.maximum_gap_ns_reject,
        )
        if gap_severity is not None:
            severity, threshold = gap_severity
            result.append(
                self._finding(
                    QualityCode.GAP_EXCESSIVE,
                    severity,
                    "maximum topic gap exceeds the profile limit",
                    topic,
                    gap_start,
                    gap_end,
                    observed=maximum_gap,
                    threshold=threshold,
                )
            )

        missing_severity = self._higher_is_worse(
            missing,
            thresholds.maximum_consecutive_missing_risk,
            thresholds.maximum_consecutive_missing_reject,
        )
        if missing_severity is not None:
            severity, threshold = missing_severity
            result.append(
                self._finding(
                    QualityCode.CONSECUTIVE_FRAMES_MISSING,
                    severity,
                    "consecutive missing frames exceed the profile limit",
                    topic,
                    missing_start,
                    missing_end,
                    observed=missing,
                    threshold=threshold,
                )
            )

        coverage_severity = self._lower_is_worse(
            coverage,
            thresholds.minimum_coverage_ratio_risk,
            thresholds.minimum_coverage_ratio_reject,
        )
        if coverage_severity is not None:
            severity, threshold = coverage_severity
            result.append(
                self._finding(
                    QualityCode.COVERAGE_LOW,
                    severity,
                    "topic coverage is below the profile limit",
                    topic,
                    data.start_ns,
                    data.end_ns,
                    observed=self._rounded(coverage),
                    threshold=threshold,
                )
            )

        return (
            TopicTimingMetricsV1(
                topic=topic,
                message_count=len(timestamps),
                unique_frame_count=len(unique),
                expected_frame_count=expected,
                duplicate_count=duplicates,
                backward_count=len(backward_pairs),
                actual_frequency_hz=self._rounded(frequency),
                interval_p50_ns=self._percentile(intervals, 0.50),
                interval_p95_ns=self._percentile(intervals, 0.95),
                interval_p99_ns=self._percentile(intervals, 0.99),
                maximum_gap_ns=maximum_gap,
                maximum_consecutive_missing=missing,
                coverage_ratio=self._rounded(coverage),
            ),
            result,
        )

    def _gap_metrics(
        self,
        unique: list[int],
        *,
        start_ns: int,
        end_ns: int,
        target_frequency_hz: int,
        expected_count: int,
    ) -> tuple[int, int, int, int, int, int]:
        if not unique:
            return start_ns, end_ns, end_ns - start_ns, start_ns, end_ns, expected_count

        def slot(timestamp_ns: int) -> int:
            offset = timestamp_ns - start_ns
            return (offset * target_frequency_hz + NANOSECONDS_PER_SECOND // 2) // (
                NANOSECONDS_PER_SECOND
            )

        gap_candidates: list[tuple[int, int, int]] = [
            (start_ns, unique[0], unique[0] - start_ns),
            *((left, right, right - left) for left, right in zip(unique, unique[1:], strict=False)),
            (unique[-1], end_ns, end_ns - unique[-1]),
        ]
        missing_candidates: list[tuple[int, int, int]] = [
            (start_ns, unique[0], max(0, slot(unique[0]))),
            *(
                (left, right, max(0, slot(right) - slot(left) - 1))
                for left, right in zip(unique, unique[1:], strict=False)
            ),
            (
                unique[-1],
                end_ns,
                max(0, expected_count - slot(unique[-1]) - 1),
            ),
        ]
        gap_start, gap_end, maximum_gap = max(
            gap_candidates, key=lambda item: (item[2], -item[0], -item[1])
        )
        missing_start, missing_end, maximum_missing = max(
            missing_candidates, key=lambda item: (item[2], -item[0], -item[1])
        )
        return (
            gap_start,
            gap_end,
            maximum_gap,
            missing_start,
            missing_end,
            maximum_missing,
        )

    def _images(self, data: QualityInputV1, profile: QualityProfileV1) -> list[QcFinding]:
        result: list[QcFinding] = []
        for topic, images in sorted(data.images.items()):
            if not images:
                continue
            thresholds = profile.image_for(topic)
            black_frames = [
                item
                for item in images
                if item.luma_mean is not None and item.luma_mean <= thresholds.black_luma_threshold
            ]
            corrupt_frames = [item for item in images if item.corrupt]
            repeated_pairs = [
                (left, right)
                for left, right in zip(images, images[1:], strict=False)
                if left.fingerprint is not None and left.fingerprint == right.fingerprint
            ]
            black_ratio = len(black_frames) / len(images)
            corrupt_ratio = len(corrupt_frames) / len(images)
            repeated_ratio = len(repeated_pairs) / max(1, len(images) - 1)
            black_severity = self._higher_is_worse(
                black_ratio,
                thresholds.maximum_black_frame_ratio_risk,
                thresholds.maximum_black_frame_ratio_reject,
            )
            if black_severity is not None:
                severity, threshold = black_severity
                start_ns, end_ns = self._affected_range(
                    [item.timestamp_ns for item in black_frames], data.start_ns, data.end_ns
                )
                result.append(
                    self._finding(
                        QualityCode.IMAGE_BLACK,
                        severity,
                        "black or underexposed frame ratio exceeds the profile limit",
                        topic,
                        start_ns,
                        end_ns,
                        observed=self._rounded(black_ratio),
                        threshold=threshold,
                    )
                )
            repeated_severity = self._higher_is_worse(
                repeated_ratio,
                thresholds.maximum_repeated_frame_ratio_risk,
                thresholds.maximum_repeated_frame_ratio_reject,
            )
            if repeated_severity is not None:
                severity, threshold = repeated_severity
                affected = [item.timestamp_ns for pair in repeated_pairs for item in pair]
                start_ns, end_ns = self._affected_range(affected, data.start_ns, data.end_ns)
                result.append(
                    self._finding(
                        QualityCode.IMAGE_REPEATED,
                        severity,
                        "consecutive repeated-frame ratio exceeds the profile limit",
                        topic,
                        start_ns,
                        end_ns,
                        observed=self._rounded(repeated_ratio),
                        threshold=threshold,
                    )
                )
            corrupt_severity = self._higher_is_worse(
                corrupt_ratio,
                thresholds.maximum_corrupt_frame_ratio_risk,
                thresholds.maximum_corrupt_frame_ratio_reject,
            )
            if corrupt_severity is not None:
                severity, threshold = corrupt_severity
                start_ns, end_ns = self._affected_range(
                    [item.timestamp_ns for item in corrupt_frames], data.start_ns, data.end_ns
                )
                result.append(
                    self._finding(
                        QualityCode.IMAGE_CORRUPT,
                        severity,
                        "corrupt image-frame ratio exceeds the profile limit",
                        topic,
                        start_ns,
                        end_ns,
                        observed=self._rounded(corrupt_ratio),
                        threshold=threshold,
                    )
                )
        return result

    def _joints(self, data: QualityInputV1, profile: QualityProfileV1) -> list[QcFinding]:
        result: list[QcFinding] = []
        severity = (
            FindingSeverity.ERROR
            if profile.joint_out_of_range_is_reject
            else FindingSeverity.WARNING
        )
        for observation in data.joints:
            for joint, value in sorted(observation.positions.items()):
                limits = profile.joint_limits.get(joint)
                if limits is not None and not limits[0] <= value <= limits[1]:
                    result.append(
                        self._finding(
                            QualityCode.JOINT_OUT_OF_RANGE,
                            severity,
                            f"joint {joint} is outside its profile limits",
                            profile.joint_topic,
                            observation.timestamp_ns,
                            observation.timestamp_ns,
                            observed=value,
                            threshold=f"{joint}:[{limits[0]},{limits[1]}]",
                        )
                    )
        return result

    def _actions(self, data: QualityInputV1, profile: QualityProfileV1) -> list[QcFinding]:
        result: list[QcFinding] = []
        thresholds = profile.action
        count_severity = self._lower_is_worse(
            len(data.actions),
            thresholds.minimum_observation_count_risk,
            thresholds.minimum_observation_count_reject,
        )
        if count_severity is not None:
            severity, threshold = count_severity
            result.append(
                self._finding(
                    QualityCode.ACTION_MISSING,
                    severity,
                    "action observation count is below the profile limit",
                    thresholds.topic,
                    data.start_ns,
                    data.end_ns,
                    observed=len(data.actions),
                    threshold=threshold,
                )
            )
        for left, right in zip(data.actions, data.actions[1:], strict=False):
            start_ns, end_ns = sorted((left.timestamp_ns, right.timestamp_ns))
            if len(left.values) != len(right.values):
                result.append(
                    self._finding(
                        QualityCode.ACTION_JUMP,
                        (
                            FindingSeverity.ERROR
                            if thresholds.dimension_mismatch_is_reject
                            else FindingSeverity.WARNING
                        ),
                        "adjacent action vectors have different dimensions",
                        thresholds.topic,
                        start_ns,
                        end_ns,
                        observed="dimension_mismatch",
                        threshold="equal_vector_dimensions",
                    )
                )
                continue
            jump = math.sqrt(
                sum((a - b) ** 2 for a, b in zip(left.values, right.values, strict=True))
            )
            jump_severity = self._higher_is_worse(
                jump, thresholds.maximum_jump_risk, thresholds.maximum_jump_reject
            )
            if jump_severity is not None:
                severity, threshold = jump_severity
                result.append(
                    self._finding(
                        QualityCode.ACTION_JUMP,
                        severity,
                        "action-vector jump exceeds the profile limit",
                        thresholds.topic,
                        start_ns,
                        end_ns,
                        observed=self._rounded(jump),
                        threshold=threshold,
                    )
                )
        return result

    def _point_clouds(self, data: QualityInputV1, profile: QualityProfileV1) -> list[QcFinding]:
        result: list[QcFinding] = []
        for topic, clouds in sorted(data.point_clouds.items()):
            if not clouds:
                continue
            thresholds = profile.point_cloud_for(topic)
            empty = [item for item in clouds if item.point_count == 0]
            empty_ratio = len(empty) / len(clouds)
            empty_severity = self._higher_is_worse(
                empty_ratio,
                thresholds.maximum_empty_ratio_risk,
                thresholds.maximum_empty_ratio_reject,
            )
            if empty_severity is not None:
                severity, threshold = empty_severity
                start_ns, end_ns = self._affected_range(
                    [item.timestamp_ns for item in empty], data.start_ns, data.end_ns
                )
                result.append(
                    self._finding(
                        QualityCode.POINT_CLOUD_EMPTY,
                        severity,
                        "empty point-cloud ratio exceeds the profile limit",
                        topic,
                        start_ns,
                        end_ns,
                        observed=self._rounded(empty_ratio),
                        threshold=threshold,
                    )
                )

            if (
                thresholds.minimum_point_count is not None
                or thresholds.maximum_point_count is not None
            ):
                abnormal = [
                    item
                    for item in clouds
                    if (
                        thresholds.minimum_point_count is not None
                        and item.point_count < thresholds.minimum_point_count
                    )
                    or (
                        thresholds.maximum_point_count is not None
                        and item.point_count > thresholds.maximum_point_count
                    )
                ]
                abnormal_ratio = len(abnormal) / len(clouds)
                abnormal_severity = self._higher_is_worse(
                    abnormal_ratio,
                    thresholds.maximum_abnormal_count_ratio_risk,
                    thresholds.maximum_abnormal_count_ratio_reject,
                )
                if abnormal_severity is not None:
                    severity, threshold = abnormal_severity
                    start_ns, end_ns = self._affected_range(
                        [item.timestamp_ns for item in abnormal],
                        data.start_ns,
                        data.end_ns,
                    )
                    point_range = (
                        f"[{thresholds.minimum_point_count},{thresholds.maximum_point_count}]"
                    )
                    result.append(
                        self._finding(
                            QualityCode.POINT_COUNT_ABNORMAL,
                            severity,
                            "abnormal point-count ratio exceeds the profile limit",
                            topic,
                            start_ns,
                            end_ns,
                            observed=self._rounded(abnormal_ratio),
                            threshold=f"ratio<={threshold};points={point_range}",
                        )
                    )
        return result

    def _offsets(self, data: QualityInputV1, profile: QualityProfileV1) -> list[QcFinding]:
        result: list[QcFinding] = []
        thresholds = profile.modality_offset
        for topic, values in sorted(data.modality_offsets_ns.items()):
            p95 = self._percentile([abs(value) for value in values], 0.95)
            if p95 is None:
                continue
            severity_result = self._higher_is_worse(
                p95,
                thresholds.maximum_p95_offset_ns_risk,
                thresholds.maximum_p95_offset_ns_reject,
            )
            if severity_result is not None:
                severity, threshold = severity_result
                result.append(
                    self._finding(
                        QualityCode.MODALITY_OFFSET,
                        severity,
                        "modality offset p95 exceeds the profile limit",
                        topic,
                        data.start_ns,
                        data.end_ns,
                        observed=p95,
                        threshold=threshold,
                    )
                )
        return result

    def _complete_steps(self, data: QualityInputV1, profile: QualityProfileV1) -> list[QcFinding]:
        thresholds = profile.complete_step
        severity_result = self._lower_is_worse(
            data.complete_step_ratio,
            thresholds.minimum_ratio_risk,
            thresholds.minimum_ratio_reject,
        )
        if severity_result is None:
            return []
        severity, threshold = severity_result
        return [
            self._finding(
                QualityCode.COMPLETE_STEP_RATIO_LOW,
                severity,
                "complete multimodal-step ratio is below the profile limit",
                thresholds.topic,
                data.start_ns,
                data.end_ns,
                observed=self._rounded(data.complete_step_ratio),
                threshold=threshold,
            )
        ]

    @staticmethod
    def _higher_is_worse(
        observed: int | float,
        risk_threshold: int | float | None,
        reject_threshold: int | float | None,
    ) -> tuple[FindingSeverity, int | float] | None:
        if reject_threshold is not None and observed > reject_threshold:
            return FindingSeverity.ERROR, reject_threshold
        if risk_threshold is not None and observed > risk_threshold:
            return FindingSeverity.WARNING, risk_threshold
        return None

    @staticmethod
    def _lower_is_worse(
        observed: int | float,
        risk_threshold: int | float | None,
        reject_threshold: int | float | None,
    ) -> tuple[FindingSeverity, int | float] | None:
        if reject_threshold is not None and observed < reject_threshold:
            return FindingSeverity.ERROR, reject_threshold
        if risk_threshold is not None and observed < risk_threshold:
            return FindingSeverity.WARNING, risk_threshold
        return None

    @staticmethod
    def _percentile(values: Iterable[int], quantile: float) -> int | None:
        ordered = sorted(values)
        if not ordered:
            return None
        index = max(0, math.ceil(quantile * len(ordered)) - 1)
        return ordered[index]

    @staticmethod
    def _status(findings: list[QcFinding]) -> QualityStatus:
        if any(item.severity == FindingSeverity.ERROR for item in findings):
            return QualityStatus.REJECT
        if findings:
            return QualityStatus.RISK
        return QualityStatus.PASS

    @staticmethod
    def _finding(
        code: QualityCode,
        severity: FindingSeverity,
        message: str,
        topic: str,
        start_ns: int,
        end_ns: int,
        *,
        observed: JsonScalar,
        threshold: JsonScalar,
    ) -> QcFinding:
        return QcFinding(
            code=code,
            severity=severity,
            message=message,
            topic=topic,
            start_ns=start_ns,
            end_ns=end_ns,
            observed=observed,
            threshold=threshold,
        )

    @staticmethod
    def _finding_key(item: QcFinding) -> tuple[str, str, int, int, str, str, str]:
        return (
            item.code.value,
            item.topic,
            item.start_ns,
            item.end_ns,
            item.severity.value,
            str(item.observed),
            str(item.threshold),
        )

    @staticmethod
    def _affected_range(
        timestamps: Iterable[int], default_start_ns: int, default_end_ns: int
    ) -> tuple[int, int]:
        values = tuple(timestamps)
        if not values:
            return default_start_ns, default_end_ns
        return min(values), max(values)

    @staticmethod
    def _rounded(value: float) -> float:
        return round(value, 6)

    @staticmethod
    def _ceil_div(dividend: int, divisor: int) -> int:
        return (dividend + divisor - 1) // divisor
