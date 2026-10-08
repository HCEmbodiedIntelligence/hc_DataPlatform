from datetime import datetime, timezone

import pytest

from hc_data_platform.quality.boundaries import conversion_window
from hc_data_platform.quality.engine import QualityEngine
from hc_data_platform.quality.models import (
    AutoQualityProblemV1,
    FindingSeverity,
    QualityCode,
    QualityInputV1,
    QualityProfileV1,
    QualityStatus,
    QualityStreamObservationV1,
)

SECOND = 1_000_000_000


def evaluate(topics, *, streamed=False, version="be06-qc/3"):
    data = QualityInputV1(
        rollout_id="boundary-test",
        source_sha256="a" * 64,
        start_ns=0,
        end_ns=20 * SECOND,
        topic_timestamps_ns=topics,
    )
    profile = QualityProfileV1(
        profile_id="boundary-test",
        engine_version=version,
        required_topics={"camera", "action"},
        default_timing={
            "target_frequency_hz": 10,
            "minimum_frequency_hz_risk": 9,
            "minimum_frequency_hz_reject": 0,
            "maximum_consecutive_missing_risk": 2,
        },
        action={"minimum_observation_count_risk": 0, "minimum_observation_count_reject": 0},
    )
    engine = QualityEngine()
    if not streamed:
        return engine.evaluate(data, profile)
    observations = sorted(
        (
            QualityStreamObservationV1(topic=topic, timestamp_ns=stamp)
            for topic, stamps in topics.items()
            for stamp in stamps
        ),
        key=lambda item: item.timestamp_ns,
    )
    return engine.evaluate_stream(
        data.model_copy(update={"topic_timestamps_ns": {}}), observations, profile
    )


def timestamps(start, end):
    return tuple(range(start * SECOND, end * SECOND, SECOND // 10))


@pytest.mark.parametrize("streamed", [False, True])
def test_recording_boundaries_are_information_and_define_one_synchronized_window(streamed):
    report = evaluate({"camera": timestamps(0, 20), "action": timestamps(3, 17)}, streamed=streamed)
    assert report.status == QualityStatus.PASS
    assert [(f.code, f.start_ns, f.end_ns) for f in report.findings] == [
        (QualityCode.LEADING_IDLE, 0, 3 * SECOND),
        (QualityCode.TRAILING_IDLE, 17 * SECOND, 20 * SECOND),
    ]
    assert all(f.severity == FindingSeverity.INFO for f in report.findings)
    assert conversion_window(report, 10) == (3 * SECOND, 17 * SECOND)
    assert report.has_valid_digest()


@pytest.mark.parametrize("streamed", [False, True])
def test_larger_boundaries_do_not_hide_multiple_interior_gaps(streamed):
    action = tuple(
        t
        for t in timestamps(3, 17)
        if not (6 * SECOND <= t < 7 * SECOND or 10 * SECOND <= t < 11 * SECOND)
    )
    report = evaluate({"camera": timestamps(0, 20), "action": action}, streamed=streamed)
    interior = [f for f in report.findings if f.code == QualityCode.CONSECUTIVE_FRAMES_MISSING]
    assert [(f.start_ns, f.end_ns) for f in interior] == [
        (59 * SECOND // 10, 7 * SECOND),
        (99 * SECOND // 10, 11 * SECOND),
    ]
    assert all(f.severity == FindingSeverity.WARNING for f in interior)
    assert report.status == QualityStatus.RISK
    problem = AutoQualityProblemV1.from_report(
        report, session_id=None, data_package_id=None, updated_at=datetime.now(timezone.utc)
    )
    assert QualityCode.LEADING_IDLE not in problem.finding_codes
    assert QualityCode.TRAILING_IDLE not in problem.finding_codes
    assert conversion_window(report, 10) == (3 * SECOND, 17 * SECOND)


def test_empty_common_window_is_not_silently_converted():
    report = evaluate({"camera": timestamps(0, 5), "action": timestamps(10, 17)})
    assert report.status == QualityStatus.RISK
    assert QualityCode.NO_COMMON_WINDOW in {f.code for f in report.findings}
    with pytest.raises(ValueError, match="no common"):
        conversion_window(report, 10)


def test_stream_and_bulk_boundary_evidence_have_identical_immutable_content():
    topics = {"camera": timestamps(0, 20), "action": timestamps(3, 17)}
    assert evaluate(topics) == evaluate(topics, streamed=True)


def test_old_policy_reports_and_digest_remain_unchanged():
    report = evaluate(
        {"camera": timestamps(0, 20), "action": timestamps(3, 17)}, version="be06-qc/2"
    )
    assert report.status == QualityStatus.RISK
    assert all(f.severity == FindingSeverity.WARNING for f in report.findings)
    assert conversion_window(report, 10) == (0, 20 * SECOND)
    assert report.has_valid_digest()
