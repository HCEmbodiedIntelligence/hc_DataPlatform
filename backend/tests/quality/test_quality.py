from __future__ import annotations

from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from hc_data_platform.quality import (
    ActionObservation,
    ActionQualityProfileV1,
    CompleteStepProfileV1,
    FakeMetadataSink,
    FakeQualityProfileStore,
    FakeQualityReportSink,
    FakeReportSink,
    FindingSeverity,
    ImageObservation,
    JointObservation,
    ModalityOffsetProfileV1,
    PointCloudObservation,
    PointCloudQualityProfileV1,
    QcReportV1,
    QualityCode,
    QualityCompletedV1,
    QualityEngine,
    QualityInputV1,
    QualityPersistenceError,
    QualityProfileV1,
    QualityStatus,
    TopicTimingProfileV1,
    VisualAnomalyProbe,
)
from hc_data_platform.quality.models import (
    ImageQualityProfileV1,
    QualityStreamObservationV1,
)

SHA = "b" * 64
DURATION_NS = 60_000_000_000


def _timestamps(hz: int) -> tuple[int, ...]:
    return tuple((index * 1_000_000_000) // hz for index in range(60 * hz))


def _profile(**updates: object) -> QualityProfileV1:
    values: dict[str, object] = {
        "profile_id": "production",
        "profile_version": 7,
        "required_topics": {"/camera/front"},
    }
    values.update(updates)
    return QualityProfileV1(**values)


def _input(timestamps: tuple[int, ...] | None = None, **updates: object) -> QualityInputV1:
    values: dict[str, object] = {
        "rollout_id": "r1",
        "source_sha256": SHA,
        "start_ns": 0,
        "end_ns": DURATION_NS,
        "topic_timestamps_ns": {"/camera/front": timestamps or _timestamps(30)},
        "actions": (ActionObservation(timestamp_ns=0, values=(0.0,)),),
    }
    values.update(updates)
    return QualityInputV1(**values)


def _find(report: QcReportV1, code: QualityCode, topic: str | None = None):  # type: ignore[no-untyped-def]
    return next(
        finding
        for finding in report.findings
        if finding.code == code and (topic is None or finding.topic == topic)
    )


def test_nominal_30hz_is_pass_with_complete_timing_metrics() -> None:
    report = QualityEngine().evaluate(_input(), _profile())

    assert report.status == QualityStatus.PASS
    assert report.findings == ()
    assert report.has_valid_digest()
    assert report.profile_version == 7
    metrics = report.topic_metrics[0]
    assert metrics.message_count == 1800
    assert metrics.unique_frame_count == 1800
    assert metrics.unique_timestamp_count == 1800
    assert metrics.expected_frame_count == 1800
    assert metrics.actual_frequency_hz == 30
    assert metrics.interval_p50_ns == 33_333_333
    assert metrics.interval_p95_ns == 33_333_334
    assert metrics.interval_p99_ns == 33_333_334
    assert metrics.maximum_gap_ns == 33_333_334
    assert metrics.maximum_consecutive_missing == 0
    assert metrics.coverage_ratio == 1


def test_unique_duplicate_and_backward_timestamp_metrics_are_independent() -> None:
    timestamps = list(_timestamps(30))
    timestamps.insert(20, timestamps[19])
    timestamps[50], timestamps[51] = timestamps[51], timestamps[50]

    report = QualityEngine().evaluate(_input(tuple(timestamps)), _profile())

    metrics = report.topic_metrics[0]
    assert metrics.message_count == 1801
    assert metrics.unique_frame_count == 1800
    assert metrics.duplicate_count == 1
    assert metrics.backward_count == 1
    assert report.status == QualityStatus.REJECT
    duplicate = _find(report, QualityCode.TIMESTAMP_DUPLICATE)
    backward = _find(report, QualityCode.TIMESTAMP_BACKWARD)
    assert duplicate.severity == FindingSeverity.WARNING
    assert duplicate.start_ns == duplicate.end_ns == timestamps[19]
    assert backward.severity == FindingSeverity.ERROR
    assert backward.start_ns < backward.end_ns


def test_online_timing_matches_materialized_rules_without_retaining_rollout_lists() -> None:
    timestamps = list(_timestamps(30))
    timestamps.insert(20, timestamps[19])
    timestamps[50], timestamps[51] = timestamps[51], timestamps[50]
    offline_data = _input(tuple(timestamps))
    stream_data = offline_data.model_copy(update={"topic_timestamps_ns": {}})
    observations = (
        QualityStreamObservationV1(
            topic="/camera/front",
            timestamp_ns=timestamp_ns,
            is_camera=False,
        )
        for timestamp_ns in timestamps
    )

    offline = QualityEngine().evaluate(offline_data, _profile())
    online = QualityEngine().evaluate_stream(stream_data, observations, _profile())

    assert online.status == offline.status
    assert online.topic_metrics == offline.topic_metrics
    assert online.findings == offline.findings


def test_online_image_aggregation_matches_black_corrupt_and_repeated_evidence() -> None:
    timestamps = tuple(index * 2_000_000_000 for index in range(30))
    images = tuple(
        ImageObservation(
            timestamp_ns=timestamp_ns,
            luma_mean=0 if index == 3 else 100,
            fingerprint="repeat" if index in (5, 6) else f"frame-{index}",
            corrupt=index == 9,
        )
        for index, timestamp_ns in enumerate(timestamps)
    )
    offline_data = _input(timestamps, images={"/camera/front": images})
    stream_data = offline_data.model_copy(update={"topic_timestamps_ns": {}, "images": {}})
    observations = (
        QualityStreamObservationV1(
            topic="/camera/front",
            timestamp_ns=image.timestamp_ns,
            is_camera=True,
            luma_mean=image.luma_mean,
            fingerprint=image.fingerprint,
            corrupt=image.corrupt,
        )
        for image in images
    )

    offline = QualityEngine().evaluate(offline_data, _profile())
    online = QualityEngine().evaluate_stream(stream_data, observations, _profile())

    assert online.status == offline.status
    assert online.topic_metrics == offline.topic_metrics
    assert online.findings == offline.findings


def test_28hz_is_risk_and_warnings_never_accumulate_into_reject() -> None:
    report = QualityEngine().evaluate(_input(_timestamps(28)), _profile())

    assert report.status == QualityStatus.RISK
    assert {finding.code for finding in report.findings} == {
        QualityCode.FREQUENCY_LOW,
        QualityCode.COVERAGE_LOW,
    }
    assert all(finding.severity == FindingSeverity.WARNING for finding in report.findings)
    assert _find(report, QualityCode.FREQUENCY_LOW).observed == 28


def test_profile_can_make_28hz_nominal_without_engine_changes() -> None:
    thresholds = TopicTimingProfileV1(
        target_frequency_hz=30,
        minimum_frequency_hz_risk=27,
        minimum_frequency_hz_reject=20,
        minimum_coverage_ratio_risk=0.90,
        minimum_coverage_ratio_reject=0.70,
    )
    report = QualityEngine().evaluate(_input(_timestamps(28)), _profile(default_timing=thresholds))

    assert report.status == QualityStatus.PASS


def test_backward_timestamp_and_large_missing_range_reject() -> None:
    timestamps = list(_timestamps(30))
    timestamps[50], timestamps[51] = timestamps[51], timestamps[50]
    del timestamps[100:300]

    report = QualityEngine().evaluate(_input(tuple(timestamps)), _profile())

    assert report.status == QualityStatus.REJECT
    assert _find(report, QualityCode.TIMESTAMP_BACKWARD).severity == FindingSeverity.ERROR
    gap = _find(report, QualityCode.GAP_EXCESSIVE)
    missing = _find(report, QualityCode.CONSECUTIVE_FRAMES_MISSING)
    assert gap.severity == FindingSeverity.ERROR
    assert gap.start_ns == timestamps[99]
    assert gap.end_ns == timestamps[100]
    assert missing.severity == FindingSeverity.ERROR
    assert missing.observed == 200
    assert report.topic_metrics[0].maximum_consecutive_missing == 200


def test_absent_required_topic_is_reject_with_full_window_evidence() -> None:
    data = _input(topic_timestamps_ns={"/optional": _timestamps(30)})

    report = QualityEngine().evaluate(data, _profile())

    finding = _find(report, QualityCode.REQUIRED_TOPIC_MISSING, "/camera/front")
    assert report.status == QualityStatus.REJECT
    assert finding.observed is False
    assert finding.threshold == "required=true"
    assert (finding.start_ns, finding.end_ns) == (0, DURATION_NS)
    missing_metrics = next(
        metrics for metrics in report.topic_metrics if metrics.topic == "/camera/front"
    )
    assert missing_metrics.unique_frame_count == 0
    assert missing_metrics.maximum_consecutive_missing == 1800


def test_multi_camera_coverage_difference_is_topic_scoped_risk() -> None:
    data = _input(
        topic_timestamps_ns={
            "/camera/front": _timestamps(30),
            "/camera/wrist": _timestamps(29),
        }
    )
    profile = _profile(required_topics={"/camera/front", "/camera/wrist"})

    report = QualityEngine().evaluate(data, profile)

    assert report.status == QualityStatus.RISK
    coverage = _find(report, QualityCode.COVERAGE_LOW, "/camera/wrist")
    assert coverage.observed == 0.966667
    assert coverage.threshold == 0.98
    assert not any(
        finding.code == QualityCode.COVERAGE_LOW and finding.topic == "/camera/front"
        for finding in report.findings
    )


def test_topic_specific_frequency_profile_supports_mixed_rate_modalities() -> None:
    lidar_timing = TopicTimingProfileV1(
        target_frequency_hz=10,
        minimum_frequency_hz_risk=9.5,
        minimum_frequency_hz_reject=7.5,
        maximum_gap_ns_risk=300_000_000,
        maximum_gap_ns_reject=3_000_000_000,
    )
    report = QualityEngine().evaluate(
        _input(
            topic_timestamps_ns={
                "/camera/front": _timestamps(30),
                "/lidar": _timestamps(10),
            }
        ),
        _profile(
            required_topics={"/camera/front", "/lidar"},
            topic_timing={"/lidar": lidar_timing},
        ),
    )

    assert report.status == QualityStatus.PASS
    lidar = next(metrics for metrics in report.topic_metrics if metrics.topic == "/lidar")
    assert lidar.actual_frequency_hz == 10
    assert lidar.expected_frame_count == 600


def test_black_and_repeated_frames_are_soft_profile_deviations() -> None:
    images = tuple(
        ImageObservation(
            timestamp_ns=index * 1_000_000,
            luma_mean=0 if index == 3 else 100,
            fingerprint="repeat" if index in (5, 6) else f"frame-{index}",
        )
        for index in range(10)
    )

    report = QualityEngine().evaluate(_input(images={"/camera/front": images}), _profile())

    assert report.status == QualityStatus.RISK
    black = _find(report, QualityCode.IMAGE_BLACK)
    repeated = _find(report, QualityCode.IMAGE_REPEATED)
    assert black.severity == FindingSeverity.WARNING
    assert black.observed == 0.1
    assert (black.start_ns, black.end_ns) == (3_000_000, 3_000_000)
    assert repeated.severity == FindingSeverity.WARNING
    assert repeated.observed == 0.111111
    assert (repeated.start_ns, repeated.end_ns) == (5_000_000, 6_000_000)


def test_corrupt_image_probe_contract_can_report_undecodable_frame() -> None:
    class CorruptProbe:
        def inspect(self, *, timestamp_ns: int, encoded_image: bytes) -> ImageObservation:
            assert encoded_image == b"not-an-image"
            return ImageObservation(timestamp_ns=timestamp_ns, corrupt=True)

    probe = CorruptProbe()
    assert isinstance(probe, VisualAnomalyProbe)
    observation = probe.inspect(timestamp_ns=10, encoded_image=b"not-an-image")

    report = QualityEngine().evaluate(_input(images={"/camera/front": (observation,)}), _profile())

    finding = _find(report, QualityCode.IMAGE_CORRUPT)
    assert report.status == QualityStatus.REJECT
    assert finding.severity == FindingSeverity.ERROR
    assert (finding.start_ns, finding.end_ns) == (10, 10)


def test_joint_out_of_range_is_hard_failure_with_joint_limit_evidence() -> None:
    report = QualityEngine().evaluate(
        _input(joints=(JointObservation(timestamp_ns=2, positions={"shoulder": 2.0}),)),
        _profile(joint_limits={"shoulder": (-1.0, 1.0)}),
    )

    finding = _find(report, QualityCode.JOINT_OUT_OF_RANGE)
    assert report.status == QualityStatus.REJECT
    assert finding.topic == "/joint_states"
    assert finding.observed == 2.0
    assert finding.threshold == "shoulder:[-1.0,1.0]"
    assert finding.start_ns == finding.end_ns == 2


def test_action_missing_and_jump_are_profile_controlled_soft_deviations() -> None:
    missing = QualityEngine().evaluate(_input(actions=()), _profile())
    jump = QualityEngine().evaluate(
        _input(
            actions=(
                ActionObservation(timestamp_ns=0, values=(0.0, 0.0)),
                ActionObservation(timestamp_ns=1, values=(10.0, 0.0)),
            )
        ),
        _profile(action=ActionQualityProfileV1(maximum_jump_risk=2.0, maximum_jump_reject=20.0)),
    )

    assert missing.status == QualityStatus.RISK
    assert _find(missing, QualityCode.ACTION_MISSING).observed == 0
    assert jump.status == QualityStatus.RISK
    finding = _find(jump, QualityCode.ACTION_JUMP)
    assert finding.observed == 10
    assert finding.threshold == 2
    assert (finding.start_ns, finding.end_ns) == (0, 1)


def test_empty_and_abnormal_point_clouds_have_independent_ratio_rules() -> None:
    clouds = tuple(
        PointCloudObservation(
            timestamp_ns=index * 1_000_000,
            point_count=0 if index == 0 else (500 if index == 1 else 100),
        )
        for index in range(10)
    )
    cloud_profile = PointCloudQualityProfileV1(
        minimum_point_count=50,
        maximum_point_count=200,
        maximum_empty_ratio_risk=0,
        maximum_empty_ratio_reject=0.20,
        maximum_abnormal_count_ratio_risk=0,
        maximum_abnormal_count_ratio_reject=0.50,
    )

    report = QualityEngine().evaluate(
        _input(point_clouds={"/lidar": clouds}),
        _profile(default_point_cloud=cloud_profile),
    )

    assert report.status == QualityStatus.RISK
    empty = _find(report, QualityCode.POINT_CLOUD_EMPTY)
    abnormal = _find(report, QualityCode.POINT_COUNT_ABNORMAL)
    assert empty.observed == 0.1
    assert abnormal.observed == 0.2
    assert abnormal.topic == "/lidar"


def test_modality_offset_and_complete_step_ratio_use_profile_hard_limits() -> None:
    report = QualityEngine().evaluate(
        _input(modality_offsets_ns={"/lidar": (150_000_000,)}, complete_step_ratio=0.5),
        _profile(
            modality_offset=ModalityOffsetProfileV1(
                maximum_p95_offset_ns_risk=20_000_000,
                maximum_p95_offset_ns_reject=100_000_000,
            ),
            complete_step=CompleteStepProfileV1(minimum_ratio_risk=0.98, minimum_ratio_reject=0.80),
        ),
    )

    assert report.status == QualityStatus.REJECT
    assert _find(report, QualityCode.MODALITY_OFFSET).severity == FindingSeverity.ERROR
    complete = _find(report, QualityCode.COMPLETE_STEP_RATIO_LOW)
    assert complete.severity == FindingSeverity.ERROR
    assert complete.topic == "__multimodal_step__"
    assert (complete.start_ns, complete.end_ns) == (0, DURATION_NS)


def test_every_finding_contains_rule_threshold_observation_topic_and_interval() -> None:
    report = QualityEngine().evaluate(
        _input(
            _timestamps(28),
            images={
                "/camera/front": (ImageObservation(timestamp_ns=0, luma_mean=0, fingerprint="a"),)
            },
            actions=(),
        ),
        _profile(),
    )

    assert report.findings
    for finding in report.findings:
        assert finding.rule == finding.code
        assert finding.topic
        assert finding.start_ns <= finding.end_ns
        assert finding.observed is not None
        assert finding.threshold is not None


def test_report_and_profile_hashes_are_deterministic_across_mapping_order() -> None:
    profile_a = _profile(
        required_topics={"/camera/front", "/camera/wrist"},
        joint_limits={"wrist": (-2.0, 2.0), "shoulder": (-1.0, 1.0)},
    )
    profile_b = _profile(
        required_topics={"/camera/wrist", "/camera/front"},
        joint_limits={"shoulder": (-1.0, 1.0), "wrist": (-2.0, 2.0)},
    )
    topics_a: Mapping[str, tuple[int, ...]] = {
        "/camera/wrist": _timestamps(30),
        "/camera/front": _timestamps(30),
    }
    topics_b = dict(reversed(tuple(topics_a.items())))

    first = QualityEngine().evaluate(_input(topic_timestamps_ns=dict(topics_a)), profile_a)
    second = QualityEngine().evaluate(_input(topic_timestamps_ns=topics_b), profile_b)

    assert profile_a.content_sha256() == profile_b.content_sha256()
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert first.content_sha256 == second.content_sha256


def test_engine_version_is_part_of_report_content_hash() -> None:
    first = QualityEngine(engine_version="be06-qc/1").evaluate(_input(), _profile())
    second = QualityEngine(engine_version="be06-qc/2").evaluate(_input(), _profile())

    assert first.status == second.status == QualityStatus.PASS
    assert first.engine_version != second.engine_version
    assert first.content_sha256 != second.content_sha256


def test_report_and_metadata_sinks_are_separate_and_ordered() -> None:
    reports = FakeReportSink()
    metadata = FakeMetadataSink()

    report = QualityEngine(reports, metadata).evaluate(_input(), _profile())

    assert reports.reports == {report.content_sha256: report}
    summary = metadata.summaries[report.rollout_id]
    assert summary.report_sha256 == report.content_sha256
    assert summary.status == QualityStatus.PASS
    assert summary.profile_version == 7
    completed = QualityCompletedV1.from_report(report)
    assert completed.report_sha256 == report.content_sha256
    assert completed.status == summary.status


def test_report_sink_failure_is_technical_and_never_updates_success_summary() -> None:
    class FailingReportSink:
        def put_immutable(self, report: QcReportV1) -> None:
            raise OSError(f"storage unavailable for {report.rollout_id}")

    metadata = FakeMetadataSink()
    engine = QualityEngine(FailingReportSink(), metadata)

    with pytest.raises(QualityPersistenceError, match="immutable QC report") as caught:
        engine.evaluate(_input(), _profile())

    assert caught.value.stage == "report"
    assert metadata.summaries == {}


def test_metadata_failure_propagates_after_idempotent_report_write() -> None:
    class FailingMetadataSink:
        def put_summary(self, summary: object) -> None:
            raise OSError(f"database unavailable: {summary!r}")

    reports = FakeReportSink()
    engine = QualityEngine(reports, FailingMetadataSink())

    with pytest.raises(QualityPersistenceError, match="metadata summary") as caught:
        engine.evaluate(_input(), _profile())

    assert caught.value.stage == "metadata"
    assert len(reports.reports) == 1


def test_combined_legacy_fake_stays_idempotent() -> None:
    sink = FakeQualityReportSink()
    engine = QualityEngine(sink)

    first = engine.evaluate(_input(), _profile())
    second = engine.evaluate(_input(), _profile())

    assert first == second
    assert len(sink.objects) == 1
    assert sink.summaries["r1"].report_sha256 == first.content_sha256


def test_quality_profile_versions_are_immutable_in_store() -> None:
    store = FakeQualityProfileStore()
    original = _profile()
    changed_same_version = _profile(
        default_image=ImageQualityProfileV1(maximum_black_frame_ratio_risk=0.10)
    )
    next_version = QualityProfileV1(**{**changed_same_version.model_dump(), "profile_version": 8})

    store.put_immutable("project-a", original)
    store.put_immutable("project-a", original)
    with pytest.raises(ValueError, match="immutable"):
        store.put_immutable("project-a", changed_same_version)
    store.put_immutable("project-a", next_version)

    assert store.get("project-a", "production", 7) == original
    assert store.get("project-a", "production", 8) == next_version


def test_profile_rejects_inverted_thresholds_and_invalid_joint_limits() -> None:
    with pytest.raises(ValidationError, match="frequency reject threshold"):
        TopicTimingProfileV1(minimum_frequency_hz_risk=20, minimum_frequency_hz_reject=25)
    with pytest.raises(ValidationError, match="invalid limits"):
        _profile(joint_limits={"joint": (2.0, -2.0)})


def test_initial_flat_profile_fields_are_upgraded_without_changing_behavior() -> None:
    legacy_values: dict[str, object] = {
        "profile_id": "legacy",
        "required_topics": {"/camera/front"},
        "target_frequency_hz": 30,
        "frequency_risk_ratio": 0.95,
        "frequency_reject_ratio": 0.75,
        "gap_risk_periods": 3,
        "gap_reject_periods": 30,
        "max_action_jump": 2.0,
    }
    profile = QualityProfileV1(**legacy_values)

    report = QualityEngine().evaluate(
        _input(
            _timestamps(28),
            actions=(
                ActionObservation(timestamp_ns=0, values=(0.0,)),
                ActionObservation(timestamp_ns=1, values=(3.0,)),
            ),
        ),
        profile,
    )

    assert profile.default_timing.minimum_frequency_hz_risk == 28.5
    assert profile.default_timing.maximum_gap_ns_reject == 1_000_000_000
    assert report.status == QualityStatus.RISK
    assert {QualityCode.FREQUENCY_LOW, QualityCode.ACTION_JUMP} <= {
        finding.code for finding in report.findings
    }

    sixty_hz_values: dict[str, object] = {
        "profile_id": "legacy-60",
        "required_topics": {"/camera/front"},
        "target_frequency_hz": 60,
    }
    sixty_hz = QualityProfileV1(**sixty_hz_values)
    assert sixty_hz.default_timing.minimum_frequency_hz_risk == 57
    assert sixty_hz.default_timing.minimum_frequency_hz_reject == 45
    assert sixty_hz.default_timing.maximum_gap_ns_risk == 50_000_000
