"""Actual fractional rates must not round to an integer or lose epoch-ns precision."""

from fractions import Fraction

import pytest
from pydantic import ValidationError

from hc_data_platform.quality import QualityEngine, QualityInputV1, QualityProfileV1
from hc_data_platform.quality.models import QualityStreamObservationV1, TopicTimingProfileV1
from tests.quality.test_quality import _input, _profile


def test_prechange_integer_profile_report_and_evidence_hashes_are_preserved():
    profile = _profile()
    current = QualityEngine().evaluate_with_evidence(_input(), profile)
    # Captured on 26b02165 before changing the frequency contract or evaluator.
    assert (
        profile.content_sha256()
        == "db98e7b40441a6185528cc51cc1238cc3bbbe986d84cf8660f27e503887f0f72"
    )
    assert (
        current.report.content_sha256
        == "3acb478ccb739e33b438bc980ebc4526b271b6ccaf817a8ba05d3f9caa45fa35"
    )
    assert (
        current.evidence.content_sha256
        == "6621bcd6138d3abe7d13688c8323706577704c131dfc87a3618081c757152d2c"
    )
    equivalent = _profile(default_timing={"target_frequency_hz": 30.0})
    assert type(equivalent.default_timing.target_frequency_hz) is int
    assert equivalent.content_bytes() == profile.content_bytes()


@pytest.mark.parametrize("fps", [29.97, 30000 / 1001, 59.94, 0.5])
@pytest.mark.parametrize("missing", [False, True])
def test_fractional_window_batch_stream_agree_at_large_epoch(fps, missing):
    count, start = 37, 1_790_000_000_123_456_789
    rate = Fraction(str(fps))
    stamps = tuple(start + i * 10**9 * rate.denominator // rate.numerator for i in range(count))
    end = start + count * 10**9 * rate.denominator // rate.numerator
    if missing:
        stamps = stamps[:10] + stamps[14:]
    profile = QualityProfileV1(
        profile_id="fractional-check",
        required_topics={"rgb"},
        action={"minimum_observation_count_risk": 0, "minimum_observation_count_reject": 0},
        default_timing={
            "target_frequency_hz": fps,
            "minimum_frequency_hz_risk": fps * 0.95,
            "minimum_frequency_hz_reject": fps * 0.75,
        },
    )
    assert QualityProfileV1.model_validate_json(profile.model_dump_json()) == profile
    data = QualityInputV1(
        rollout_id="fractional",
        source_sha256="a" * 64,
        start_ns=start,
        end_ns=end,
        topic_timestamps_ns={"rgb": stamps},
    )
    engine = QualityEngine()
    batch = engine.evaluate_with_evidence(data, profile)
    stream = engine.evaluate_with_evidence(
        data,
        profile,
        observations=(QualityStreamObservationV1(topic="rgb", timestamp_ns=t) for t in stamps),
    )
    assert stream.report == batch.report
    assert stream.evidence.rules == batch.evidence.rules
    metric = batch.report.topic_metrics[0]
    assert metric.expected_frame_count == count
    assert metric.unique_frame_count == count - (4 if missing else 0)
    assert metric.maximum_consecutive_missing == (4 if missing else 0)
    assert batch.report.status.value == ("RISK" if missing else "PASS")


@pytest.mark.parametrize("fps", [0, -0.1, float("nan"), float("inf"), 1000.1])
def test_invalid_rates_are_rejected(fps):
    with pytest.raises(ValidationError):
        TopicTimingProfileV1(target_frequency_hz=fps)


def test_frozen_two_frame_tail_uses_actual_count_without_changing_raw_ingest():
    data = QualityInputV1(
        rollout_id="tail",
        source_sha256="a" * 64,
        start_ns=66_666_666,
        end_ns=133_333_333,
        topic_timestamps_ns={"rgb": (66_666_666, 100_000_000)},
    )
    profile = QualityProfileV1(profile_id="tail", required_topics={"rgb"})
    engine = QualityEngine()
    original = engine.evaluate(data, profile)
    assert original.topic_metrics[0].expected_frame_count == 3
    fixed = engine.evaluate_with_evidence(data, profile, expected_frames={"rgb": 2})
    streamed = engine.evaluate_with_evidence(
        data,
        profile,
        expected_frames={"rgb": 2},
        observations=(
            QualityStreamObservationV1(topic="rgb", timestamp_ns=t)
            for t in (66_666_666, 100_000_000)
        ),
    )
    assert fixed.report == streamed.report
    assert fixed.report.topic_metrics[0].expected_frame_count == 2
    assert fixed.report.topic_metrics[0].maximum_consecutive_missing == 0
    assert fixed.report.topic_metrics[0].coverage_ratio == 1
    assert engine.evaluate(data, profile) == original


@pytest.mark.parametrize("counts", [{"rgb": 0}, {"rgb": -1}, {"rgb": True}, {"rgb": 2.0}, {"": 2}])
def test_frozen_grid_rejects_invalid_counts_before_consuming_stream(counts):
    def unconsumed():
        pytest.fail("invalid grid consumed input")
        yield

    with pytest.raises(ValueError, match="frozen expected frame"):
        QualityEngine().evaluate_with_evidence(
            _input(), _profile(), expected_frames=counts, observations=unconsumed()
        )
