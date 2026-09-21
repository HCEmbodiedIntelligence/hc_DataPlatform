from __future__ import annotations

import pytest

from hc_data_platform.quality import (
    ActionObservation,
    FindingSeverity,
    ImageObservation,
    JointObservation,
    PointCloudObservation,
    QualityCode,
    QualityEngine,
    QualityProfileV1,
    QualityStatus,
)
from hc_data_platform.quality.models import QualityStreamObservationV1
from hc_data_platform.quality.policy import reclassify_report

from .test_quality import _input, _timestamps


def profile(**updates: object) -> QualityProfileV1:
    return QualityProfileV1(
        profile_id="approved-risks", required_topics={"/camera/front"}, **updates
    )


@pytest.mark.parametrize(
    "case, expected",
    [
        ("corrupt", QualityCode.IMAGE_CORRUPT),
        ("slow", QualityCode.FREQUENCY_LOW),
        ("no_action", QualityCode.ACTION_MISSING),
        ("action_dimension", QualityCode.ACTION_JUMP),
        ("missing_frames", QualityCode.CONSECUTIVE_FRAMES_MISSING),
        ("duplicate", QualityCode.TIMESTAMP_DUPLICATE),
        ("backward", QualityCode.TIMESTAMP_BACKWARD),
        ("missing_topic", QualityCode.REQUIRED_TOPIC_MISSING),
    ],
)
def test_only_approved_categories_are_risks_even_at_former_reject_levels(
    case: str,
    expected: QualityCode,
) -> None:
    data = _input()
    timestamps = list(_timestamps(30))
    if case == "corrupt":
        data = _input(images={"/camera/front": (ImageObservation(timestamp_ns=0, corrupt=True),)})
    elif case == "slow":
        data = _input(_timestamps(10))
    elif case == "no_action":
        data = _input(actions=())
    elif case == "action_dimension":
        data = _input(
            actions=(
                ActionObservation(timestamp_ns=0, values=(0.0,)),
                ActionObservation(timestamp_ns=1, values=(0.0, 1.0)),
            )
        )
    elif case == "missing_frames":
        del timestamps[100:300]
        data = _input(tuple(timestamps))
    elif case == "duplicate":
        timestamps.insert(20, timestamps[19])
        data = _input(tuple(timestamps))
    elif case == "backward":
        timestamps[20], timestamps[21] = timestamps[21], timestamps[20]
        data = _input(tuple(timestamps))
    elif case == "missing_topic":
        data = _input(topic_timestamps_ns={})

    report = QualityEngine().evaluate(data, profile())
    assert report.engine_version == "be06-qc/2"
    assert report.status is QualityStatus.RISK
    assert expected in {finding.code for finding in report.findings}
    assert all(finding.severity is FindingSeverity.WARNING for finding in report.findings)
    assert report.has_valid_digest()


def test_unapproved_rules_cannot_flag_data_even_with_extreme_observations() -> None:
    data = _input(
        images={
            "/camera/front": tuple(
                ImageObservation(timestamp_ns=index, luma_mean=0, fingerprint="identical")
                for index in range(10)
            )
        },
        joints=(JointObservation(timestamp_ns=0, positions={"joint": 999}),),
        point_clouds={"/lidar": (PointCloudObservation(timestamp_ns=0, point_count=0),)},
        modality_offsets_ns={"/lidar": (1_000_000_000,)},
        complete_step_ratio=0,
    )
    report = QualityEngine().evaluate(
        data,
        profile(
            joint_limits={"joint": (-1, 1)},
            default_point_cloud={"minimum_point_count": 100},
            default_timing={"maximum_gap_ns_risk": 0},
        ),
    )
    assert report.status is QualityStatus.PASS
    assert report.findings == ()


def test_coverage_alone_is_not_a_risk() -> None:
    report = QualityEngine().evaluate(_input(_timestamps(29)), profile())
    assert report.topic_metrics[0].coverage_ratio < 0.98
    assert report.status is QualityStatus.PASS
    assert report.findings == ()


def test_streaming_and_materialized_evaluations_apply_the_same_policy() -> None:
    images = tuple(
        ImageObservation(
            timestamp_ns=timestamp,
            luma_mean=0,
            fingerprint="identical",
            corrupt=index == 0,
        )
        for index, timestamp in enumerate(_timestamps(30))
    )
    data = _input(images={"/camera/front": images})
    offline = QualityEngine().evaluate(data, profile())
    online = QualityEngine().evaluate_stream(
        data.model_copy(update={"images": {}, "topic_timestamps_ns": {}}),
        (
            QualityStreamObservationV1(
                topic="/camera/front",
                timestamp_ns=item.timestamp_ns,
                is_camera=True,
                luma_mean=item.luma_mean,
                fingerprint=item.fingerprint,
                corrupt=item.corrupt,
            )
            for item in images
        ),
        profile(),
    )
    assert offline == online
    assert offline.status is QualityStatus.RISK
    assert [finding.code for finding in offline.findings] == [QualityCode.IMAGE_CORRUPT]


@pytest.mark.parametrize("approved_risk", [False, True])
def test_historical_reclassification_matches_new_evaluation_and_preserves_original(
    approved_risk: bool,
) -> None:
    data = _input(
        _timestamps(10) if approved_risk else _timestamps(30),
        images={"/camera/front": (ImageObservation(timestamp_ns=0, luma_mean=0, fingerprint="a"),)},
    )
    old_profile = profile(engine_version="be06-qc/1")
    original = QualityEngine().evaluate(data, old_profile)
    previous_json = original.model_dump_json()
    current = profile(profile_version=2)
    revised = reclassify_report(original, old_profile, current)
    assert revised == QualityEngine().evaluate(data, current)
    assert revised.status is (QualityStatus.RISK if approved_risk else QualityStatus.PASS)
    assert revised.content_sha256 != original.content_sha256
    assert original.model_dump_json() == previous_json
    with pytest.raises(ValueError, match="same evidence and thresholds"):
        reclassify_report(
            original,
            old_profile,
            profile(
                profile_version=2,
                default_timing={
                    "minimum_frequency_hz_risk": 29,
                },
            ),
        )
