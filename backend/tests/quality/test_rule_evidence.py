"""Execution evidence must distinguish actual checks, missing inputs and policy exclusions."""

import json
import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from hc_data_platform.quality import QualityCode, QualityEngine
from hc_data_platform.quality.evidence import QcEvaluation, QcRuleEvidence
from hc_data_platform.quality.models import ImageObservation, QualityStreamObservationV1
from tests.quality.test_quality import _input, _profile


def rule(evaluation, code, topic=None):
    return next(
        r
        for r in evaluation.evidence.rules
        if r.code == code and (topic is None or r.topic == topic)
    )


def test_new_evidence_preserves_archived_report_bytes_and_records_every_rule():
    data, profile = _input(), _profile()
    engine = QualityEngine()
    archived = engine.evaluate(data, profile)
    current = engine.evaluate_with_evidence(data, profile)
    assert current.report.model_dump_json() == archived.model_dump_json()
    assert current.report.content_sha256 == archived.content_sha256
    assert {item.code for item in current.evidence.rules} == set(QualityCode) - {
        QualityCode.LEADING_IDLE,
        QualityCode.TRAILING_IDLE,
        QualityCode.NO_COMMON_WINDOW,
    }
    assert rule(current, QualityCode.FREQUENCY_LOW).status == "pass"
    assert rule(current, QualityCode.IMAGE_CORRUPT).status == "skip"
    assert rule(current, QualityCode.JOINT_OUT_OF_RANGE).reason == "NO_JOINT_LIMITS"
    ratio = rule(current, QualityCode.COMPLETE_STEP_RATIO_LOW)
    assert ratio.reason == "COMPLETE_STEP_RATIO_NOT_MEASURED" and ratio.observed["ratio"] is None
    assert QcEvaluation.model_validate_json(current.model_dump_json()) == current
    assert engine.evaluate_with_evidence(data, profile).evidence == current.evidence


def test_policy_exclusion_retains_detector_findings_without_changing_verdict():
    data = _input(
        images={
            "/camera/front": (ImageObservation(timestamp_ns=0, luma_mean=0, fingerprint="black"),)
        }
    )
    profile = _profile(engine_version="be06-qc/2")
    current = QualityEngine().evaluate_with_evidence(data, profile)
    assert current.report.status.value == "PASS"
    black = rule(current, QualityCode.IMAGE_BLACK)
    assert black.status == "info" and black.reason == "EXCLUDED_BY_VERDICT_POLICY"
    assert black.participates_in_verdict is False
    assert black.detector_findings[0].severity.value == "error"
    assert not black.report_finding_indexes
    legacy = QualityEngine().evaluate_with_evidence(data, _profile())
    assert legacy.report.status.value == "REJECT"
    assert rule(legacy, QualityCode.IMAGE_BLACK).status == "fail"


def test_stream_depth_decode_does_not_claim_rgb_appearance_pass():
    data, profile = _input(), _profile(engine_version="be06-qc/2")
    stamps = data.topic_timestamps_ns["/camera/front"]
    consumed = []

    def stream():
        for index, timestamp in enumerate(stamps):
            consumed.append(index)
            yield QualityStreamObservationV1(
                topic="/camera/front", timestamp_ns=timestamp, is_camera=True, corrupt=False
            )

    current = QualityEngine().evaluate_with_evidence(data, profile, observations=stream())
    assert len(consumed) == len(stamps)
    assert rule(current, QualityCode.IMAGE_CORRUPT).status == "pass"
    for code in (QualityCode.IMAGE_BLACK, QualityCode.IMAGE_REPEATED):
        result = rule(current, code)
        assert result.status == "skip" and result.evaluated_count == 0
    assert current.evidence.input_mode == "stream"


def test_timestamp_only_camera_cannot_prove_decode_integrity():
    observation = QualityStreamObservationV1(topic="/camera/front", timestamp_ns=0, is_camera=True)
    current = QualityEngine().evaluate_with_evidence(
        _input(), _profile(), observations=iter([observation])
    )
    assert rule(current, QualityCode.IMAGE_CORRUPT).status == "skip"
    assert rule(current, QualityCode.TIMESTAMP_BACKWARD).reason == "INSUFFICIENT_OBSERVATIONS"


def test_partial_luma_and_missing_required_topics_remain_explicit():
    observations = [
        QualityStreamObservationV1(
            topic="/camera/front",
            timestamp_ns=0,
            is_camera=True,
            luma_mean=128,
            fingerprint="one",
            corrupt=False,
        ),
        QualityStreamObservationV1(
            topic="/camera/front", timestamp_ns=33_333_333, is_camera=True, corrupt=False
        ),
    ]
    current = QualityEngine().evaluate_with_evidence(
        _input(),
        _profile(required_topics={"/camera/front", "/absent"}),
        observations=iter(observations),
    )
    assert rule(current, QualityCode.IMAGE_BLACK).reason == "PARTIAL_OBSERVATIONS"
    missing = rule(current, QualityCode.REQUIRED_TOPIC_MISSING, "/absent")
    assert missing.status == "fail" and missing.observed["topic_present"] is False
    policy_v2 = QualityEngine().evaluate_with_evidence(
        _input(), _profile(engine_version="be06-qc/2", required_topics={"/absent"})
    )
    assert rule(policy_v2, QualityCode.REQUIRED_TOPIC_MISSING).status == "warn"


def test_evidence_digest_report_and_profile_cannot_be_substituted():
    current = QualityEngine().evaluate_with_evidence(_input(), _profile())
    document = current.evidence.model_dump(mode="json")
    document["rules"][0]["reason"] = "forged"
    with pytest.raises(ValidationError):
        QcRuleEvidence.model_validate(document)
    other = QualityEngine().evaluate(_input(source_sha256="c" * 64), _profile())
    with pytest.raises(ValueError, match="differs"):
        current.evidence.validate_report(other)
    wrong = current.evidence.model_copy(update={"profile": _profile(profile_version=99)})
    with pytest.raises(ValueError, match="differs"):
        wrong.validate_report(current.report)


def test_evidence_wire_is_stable_across_worker_hash_seeds():
    current = QualityEngine().evaluate_with_evidence(
        _input(), _profile(required_topics={f"/camera/{index}" for index in range(24)})
    )
    expected = current.evidence.model_dump_json()
    wire = json.loads(expected)
    assert wire["profile"]["required_topics"] == sorted(current.evidence.profile.required_topics)
    wire["profile"]["required_topics"].reverse()
    # Equivalent set order must neither change evidence bytes nor its identity.
    script = (
        "import sys; from hc_data_platform.quality.evidence import QcRuleEvidence; "
        "sys.stdout.write(QcRuleEvidence.model_validate_json(sys.stdin.read()).model_dump_json())"
    )
    for seed in ("0", "1"):
        output = subprocess.run(
            [sys.executable, "-c", script],
            input=json.dumps(wire),
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert output.stdout == expected


@pytest.mark.parametrize("streamed", [False, True])
def test_boundary_engine_evidence_records_information_without_changing_verdict(streamed):
    from hc_data_platform.quality.models import QualityInputV1, QualityProfileV1
    from tests.quality.test_boundaries import timestamps

    topics = {"camera": timestamps(0, 20), "action": timestamps(3, 17)}
    data = QualityInputV1(
        rollout_id="boundary-evidence",
        source_sha256="a" * 64,
        start_ns=0,
        end_ns=20 * 10**9,
        topic_timestamps_ns=topics,
    )
    profile = QualityProfileV1(
        profile_id="boundary-evidence",
        engine_version="be06-qc/3",
        required_topics={"camera", "action"},
        default_timing={
            "target_frequency_hz": 10,
            "minimum_frequency_hz_risk": 9,
            "minimum_frequency_hz_reject": 0,
        },
        action={"minimum_observation_count_risk": 0, "minimum_observation_count_reject": 0},
    )
    observations = (
        (
            QualityStreamObservationV1(topic=topic, timestamp_ns=t)
            for topic, stamps in topics.items()
            for t in stamps
        )
        if streamed
        else None
    )
    evaluation = QualityEngine().evaluate_with_evidence(data, profile, observations=observations)
    assert evaluation.report.status.value == "PASS"
    assert {item.code for item in evaluation.evidence.rules} == set(QualityCode)
    for code in (QualityCode.LEADING_IDLE, QualityCode.TRAILING_IDLE):
        boundary = rule(evaluation, code, "action")
        assert boundary.status == "info"
        assert boundary.participates_in_verdict is False
        assert boundary.reason == "INFORMATIONAL_FINDING"
    assert QcEvaluation.model_validate_json(evaluation.model_dump_json()) == evaluation
