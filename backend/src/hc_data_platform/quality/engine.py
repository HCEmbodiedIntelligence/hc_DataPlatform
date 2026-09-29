"""Deterministic rule engine for timing, visual and robot-data quality."""

from __future__ import annotations

import math
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .boundaries import BOUNDARY_QUALITY_ENGINE_VERSION, active_topic_timing, common_window
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
    QualityStreamObservationV1,
    QualitySummaryV1,
    TopicTimingMetricsV1,
    TopicTimingProfileV1,
)
from .policy import LEGACY_QUALITY_ENGINE_VERSION, approved_risk_findings
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
            if (self._engine_version or profile.engine_version) == BOUNDARY_QUALITY_ENGINE_VERSION:
                topic_metrics, topic_findings = active_topic_timing(
                    topic=topic,
                    timestamps=sorted(set(data.topic_timestamps_ns.get(topic, ()))),
                    data=data,
                    thresholds=profile.timing_for(topic),
                    metrics=topic_metrics,
                    findings=topic_findings,
                    required=topic in profile.required_topics,
                )
            metrics.append(topic_metrics)
            findings.extend(topic_findings)

        findings.extend(self._images(data, profile))
        findings.extend(self._joints(data, profile))
        findings.extend(self._actions(data, profile))
        findings.extend(self._point_clouds(data, profile))
        findings.extend(self._offsets(data, profile))
        findings.extend(self._complete_steps(data, profile))
        self._check_common_window(findings, data, profile)
        findings = self._apply_policy(findings, profile)
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

    def evaluate_stream(
        self,
        data: QualityInputV1,
        observations: Iterable[QualityStreamObservationV1],
        profile: QualityProfileV1,
    ) -> QcReportV1:
        """Evaluate ingest facts online with exact timestamp state spilled to SQLite."""

        findings: list[QcFinding] = []
        metrics: list[TopicTimingMetricsV1] = []
        order: dict[str, dict[str, int | None]] = {}
        images: dict[str, dict[str, object]] = {}
        with tempfile.TemporaryDirectory(prefix="hc-quality-online-") as directory:
            database = sqlite3.connect(str(Path(directory) / "quality.sqlite3"))
            try:
                database.execute(
                    "CREATE TABLE timestamps (topic TEXT NOT NULL, ts INTEGER NOT NULL, "
                    "PRIMARY KEY (topic, ts)) WITHOUT ROWID"
                )
                database.execute(
                    "CREATE TABLE intervals (topic TEXT NOT NULL, value INTEGER NOT NULL)"
                )
                for observation in observations:
                    if not data.start_ns <= observation.timestamp_ns < data.end_ns:
                        raise ValueError("quality stream timestamp is outside the rollout window")
                    state = order.setdefault(
                        observation.topic,
                        {
                            "count": 0,
                            "last": None,
                            "backward": 0,
                            "backward_min": None,
                            "backward_max": None,
                            "duplicates": 0,
                            "duplicate_min": None,
                            "duplicate_max": None,
                        },
                    )
                    state["count"] = int(state["count"] or 0) + 1
                    last = state["last"]
                    if isinstance(last, int) and observation.timestamp_ns < last:
                        state["backward"] = int(state["backward"] or 0) + 1
                        _expand_range(state, "backward", last)
                        _expand_range(state, "backward", observation.timestamp_ns)
                    state["last"] = observation.timestamp_ns
                    cursor = database.execute(
                        "INSERT OR IGNORE INTO timestamps (topic, ts) VALUES (?, ?)",
                        (observation.topic, observation.timestamp_ns),
                    )
                    if cursor.rowcount == 0:
                        state["duplicates"] = int(state["duplicates"] or 0) + 1
                        _expand_range(state, "duplicate", observation.timestamp_ns)
                    if observation.is_camera:
                        image = images.setdefault(
                            observation.topic,
                            {
                                "count": 0,
                                "black": 0,
                                "black_min": None,
                                "black_max": None,
                                "corrupt": 0,
                                "corrupt_min": None,
                                "corrupt_max": None,
                                "repeated": 0,
                                "repeated_min": None,
                                "repeated_max": None,
                                "last_fingerprint": None,
                                "last_timestamp": None,
                            },
                        )
                        image["count"] = _required_state_int(image["count"], "count") + 1
                        threshold = profile.image_for(observation.topic).black_luma_threshold
                        if observation.luma_mean is not None and observation.luma_mean <= threshold:
                            image["black"] = _required_state_int(image["black"], "black") + 1
                            _expand_range(image, "black", observation.timestamp_ns)
                        if observation.corrupt:
                            image["corrupt"] = _required_state_int(image["corrupt"], "corrupt") + 1
                            _expand_range(image, "corrupt", observation.timestamp_ns)
                        prior_fingerprint = image["last_fingerprint"]
                        prior_timestamp = image["last_timestamp"]
                        if (
                            observation.fingerprint is not None
                            and observation.fingerprint == prior_fingerprint
                        ):
                            image["repeated"] = (
                                _required_state_int(image["repeated"], "repeated") + 1
                            )
                            if isinstance(prior_timestamp, int):
                                _expand_range(image, "repeated", prior_timestamp)
                            _expand_range(image, "repeated", observation.timestamp_ns)
                        image["last_fingerprint"] = observation.fingerprint
                        image["last_timestamp"] = observation.timestamp_ns
                database.commit()
                topics = set(order) | set(profile.required_topics)
                for topic in sorted(topics):
                    if topic not in order:
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
                    topic_metrics, topic_findings = self._online_timing(
                        database,
                        topic,
                        order.get(topic, {}),
                        data,
                        profile.timing_for(topic),
                    )
                    if (
                        self._engine_version or profile.engine_version
                    ) == BOUNDARY_QUALITY_ENGINE_VERSION:
                        topic_metrics, topic_findings = active_topic_timing(
                            topic=topic,
                            timestamps=(
                                int(row[0])
                                for row in database.execute(
                                    "SELECT ts FROM timestamps WHERE topic=? ORDER BY ts", (topic,)
                                )
                            ),
                            data=data,
                            thresholds=profile.timing_for(topic),
                            metrics=topic_metrics,
                            findings=topic_findings,
                            required=topic in profile.required_topics,
                        )
                    metrics.append(topic_metrics)
                    findings.extend(topic_findings)
                findings.extend(self._online_images(images, data, profile))
            finally:
                database.close()

        # These ingest projections do not currently populate richer decoded facts.
        findings.extend(self._joints(data, profile))
        findings.extend(self._actions(data, profile))
        findings.extend(self._point_clouds(data, profile))
        findings.extend(self._offsets(data, profile))
        findings.extend(self._complete_steps(data, profile))
        self._check_common_window(findings, data, profile)
        findings = self._apply_policy(findings, profile)
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

    def _check_common_window(
        self,
        findings: list[QcFinding],
        data: QualityInputV1,
        profile: QualityProfileV1,
    ) -> None:
        if (self._engine_version or profile.engine_version) != BOUNDARY_QUALITY_ENGINE_VERSION:
            return
        start, end = common_window(data.start_ns, data.end_ns, findings)
        hz = profile.default_timing.target_frequency_hz
        step = ((start - data.start_ns) * hz + NANOSECONDS_PER_SECOND - 1) // NANOSECONDS_PER_SECOND
        if data.start_ns + step * NANOSECONDS_PER_SECOND // hz >= end:
            findings.append(
                self._finding(
                    QualityCode.NO_COMMON_WINDOW,
                    FindingSeverity.WARNING,
                    "required modalities have no common active frame; human review required",
                    "__multimodal_step__",
                    data.start_ns,
                    data.end_ns,
                    observed=False,
                    threshold="non-empty common active window",
                )
            )

    def _apply_policy(
        self,
        findings: list[QcFinding],
        profile: QualityProfileV1,
    ) -> list[QcFinding]:
        # Archived v1 profiles remain reproducible for historical workflow replay.
        if (self._engine_version or profile.engine_version) == LEGACY_QUALITY_ENGINE_VERSION:
            return findings
        return approved_risk_findings(findings, profile)

    def _online_timing(
        self,
        database: sqlite3.Connection,
        topic: str,
        state: dict[str, int | None],
        data: QualityInputV1,
        thresholds: TopicTimingProfileV1,
    ) -> tuple[TopicTimingMetricsV1, list[QcFinding]]:
        duration_ns = data.end_ns - data.start_ns
        expected = max(
            1,
            self._ceil_div(
                duration_ns * thresholds.target_frequency_hz,
                NANOSECONDS_PER_SECOND,
            ),
        )
        timestamps = database.execute(
            "SELECT ts FROM timestamps WHERE topic = ? ORDER BY ts", (topic,)
        )
        first: int | None = None
        previous: int | None = None
        unique_count = 0
        maximum_gap = duration_ns
        gap_start, gap_end = data.start_ns, data.end_ns
        maximum_missing = expected
        missing_start, missing_end = data.start_ns, data.end_ns

        def slot(timestamp_ns: int) -> int:
            return (
                (timestamp_ns - data.start_ns) * thresholds.target_frequency_hz
                + NANOSECONDS_PER_SECOND // 2
            ) // NANOSECONDS_PER_SECOND

        for raw in timestamps:
            timestamp = int(raw[0])
            unique_count += 1
            if first is None:
                first = timestamp
                maximum_gap = timestamp - data.start_ns
                gap_start, gap_end = data.start_ns, timestamp
                maximum_missing = max(0, slot(timestamp))
                missing_start, missing_end = data.start_ns, timestamp
            if previous is not None:
                interval = timestamp - previous
                database.execute(
                    "INSERT INTO intervals (topic, value) VALUES (?, ?)", (topic, interval)
                )
                if interval > maximum_gap:
                    maximum_gap, gap_start, gap_end = interval, previous, timestamp
                missing = max(0, slot(timestamp) - slot(previous) - 1)
                if missing > maximum_missing:
                    maximum_missing = missing
                    missing_start, missing_end = previous, timestamp
            previous = timestamp
        if previous is not None:
            final_gap = data.end_ns - previous
            if final_gap > maximum_gap:
                maximum_gap, gap_start, gap_end = final_gap, previous, data.end_ns
            final_missing = max(0, expected - slot(previous) - 1)
            if final_missing > maximum_missing:
                maximum_missing = final_missing
                missing_start, missing_end = previous, data.end_ns
        database.commit()
        message_count = int(state.get("count") or 0)
        duplicates = int(state.get("duplicates") or 0)
        backward = int(state.get("backward") or 0)
        frequency = unique_count * NANOSECONDS_PER_SECOND / duration_ns
        coverage = min(1.0, unique_count / expected)
        result: list[QcFinding] = []

        checks = (
            (
                QualityCode.TIMESTAMP_DUPLICATE,
                self._higher_is_worse(
                    duplicates,
                    thresholds.maximum_duplicate_timestamps_risk,
                    thresholds.maximum_duplicate_timestamps_reject,
                ),
                "duplicate timestamps exceed the profile limit",
                int(state.get("duplicate_min") or data.start_ns),
                int(state.get("duplicate_max") or data.end_ns),
                duplicates,
            ),
            (
                QualityCode.FREQUENCY_LOW,
                self._lower_is_worse(
                    frequency,
                    thresholds.minimum_frequency_hz_risk,
                    thresholds.minimum_frequency_hz_reject,
                ),
                "topic frequency is below the profile limit",
                data.start_ns,
                data.end_ns,
                self._rounded(frequency),
            ),
            (
                QualityCode.GAP_EXCESSIVE,
                self._higher_is_worse(
                    maximum_gap,
                    thresholds.maximum_gap_ns_risk,
                    thresholds.maximum_gap_ns_reject,
                ),
                "maximum topic gap exceeds the profile limit",
                gap_start,
                gap_end,
                maximum_gap,
            ),
            (
                QualityCode.CONSECUTIVE_FRAMES_MISSING,
                self._higher_is_worse(
                    maximum_missing,
                    thresholds.maximum_consecutive_missing_risk,
                    thresholds.maximum_consecutive_missing_reject,
                ),
                "consecutive missing frames exceed the profile limit",
                missing_start,
                missing_end,
                maximum_missing,
            ),
            (
                QualityCode.COVERAGE_LOW,
                self._lower_is_worse(
                    coverage,
                    thresholds.minimum_coverage_ratio_risk,
                    thresholds.minimum_coverage_ratio_reject,
                ),
                "topic coverage is below the profile limit",
                data.start_ns,
                data.end_ns,
                self._rounded(coverage),
            ),
        )
        for code, severity, message, start, end, observed in checks:
            if severity is not None:
                level, threshold = severity
                result.append(
                    self._finding(
                        code,
                        level,
                        message,
                        topic,
                        start,
                        end,
                        observed=observed,
                        threshold=threshold,
                    )
                )
        if backward > thresholds.maximum_backward_timestamps_reject:
            result.append(
                self._finding(
                    QualityCode.TIMESTAMP_BACKWARD,
                    FindingSeverity.ERROR,
                    "timestamp order moves backwards",
                    topic,
                    int(state.get("backward_min") or data.start_ns),
                    int(state.get("backward_max") or data.end_ns),
                    observed=backward,
                    threshold=thresholds.maximum_backward_timestamps_reject,
                )
            )
        return (
            TopicTimingMetricsV1(
                topic=topic,
                message_count=message_count,
                unique_frame_count=unique_count,
                expected_frame_count=expected,
                duplicate_count=duplicates,
                backward_count=backward,
                actual_frequency_hz=self._rounded(frequency),
                interval_p50_ns=_sqlite_percentile(database, topic, 0.50),
                interval_p95_ns=_sqlite_percentile(database, topic, 0.95),
                interval_p99_ns=_sqlite_percentile(database, topic, 0.99),
                maximum_gap_ns=maximum_gap,
                maximum_consecutive_missing=maximum_missing,
                coverage_ratio=self._rounded(coverage),
            ),
            result,
        )

    def _online_images(
        self,
        images: dict[str, dict[str, object]],
        data: QualityInputV1,
        profile: QualityProfileV1,
    ) -> list[QcFinding]:
        result: list[QcFinding] = []
        for topic, state in sorted(images.items()):
            count = _required_state_int(state["count"], "count")
            thresholds = profile.image_for(topic)
            definitions = (
                (
                    "black",
                    QualityCode.IMAGE_BLACK,
                    "black or underexposed frame ratio exceeds the profile limit",
                    thresholds.maximum_black_frame_ratio_risk,
                    thresholds.maximum_black_frame_ratio_reject,
                    count,
                ),
                (
                    "repeated",
                    QualityCode.IMAGE_REPEATED,
                    "consecutive repeated-frame ratio exceeds the profile limit",
                    thresholds.maximum_repeated_frame_ratio_risk,
                    thresholds.maximum_repeated_frame_ratio_reject,
                    max(1, count - 1),
                ),
                (
                    "corrupt",
                    QualityCode.IMAGE_CORRUPT,
                    "corrupt image-frame ratio exceeds the profile limit",
                    thresholds.maximum_corrupt_frame_ratio_risk,
                    thresholds.maximum_corrupt_frame_ratio_reject,
                    count,
                ),
            )
            for name, code, message, risk, reject, denominator in definitions:
                ratio = _required_state_int(state[name], name) / denominator
                severity = self._higher_is_worse(ratio, risk, reject)
                if severity is None:
                    continue
                level, threshold = severity
                result.append(
                    self._finding(
                        code,
                        level,
                        message,
                        topic,
                        _optional_state_int(state.get(f"{name}_min"), data.start_ns, name),
                        _optional_state_int(state.get(f"{name}_max"), data.end_ns, name),
                        observed=self._rounded(ratio),
                        threshold=threshold,
                    )
                )
        return result

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
        if any(item.severity == FindingSeverity.WARNING for item in findings):
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


def _required_state_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"quality stream state {name!r} must be an integer")
    return value


def _optional_state_int(value: object, default: int, name: str) -> int:
    if value is None:
        return default
    return _required_state_int(value, name)


def _expand_range(state: dict[str, Any], name: str, timestamp: int) -> None:
    minimum = state.get(f"{name}_min")
    maximum = state.get(f"{name}_max")
    state[f"{name}_min"] = timestamp if minimum is None else min(int(minimum), timestamp)
    state[f"{name}_max"] = timestamp if maximum is None else max(int(maximum), timestamp)


def _sqlite_percentile(database: sqlite3.Connection, topic: str, quantile: float) -> int | None:
    count = int(
        database.execute("SELECT count(*) FROM intervals WHERE topic = ?", (topic,)).fetchone()[0]
    )
    if count == 0:
        return None
    offset = max(0, math.ceil(quantile * count) - 1)
    row = database.execute(
        "SELECT value FROM intervals WHERE topic = ? ORDER BY value LIMIT 1 OFFSET ?",
        (topic, offset),
    ).fetchone()
    return None if row is None else int(row[0])
