"""Freeze the new aligned QC contract without inheriting a parent decision."""

import copy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from hc_data_platform.episode_store import derivation_quality
from hc_data_platform.episode_store.models import DataVersionRef
from hc_data_platform.quality.engine import QualityEngine
from hc_data_platform.quality.models import QualityInputV1, QualityProfileV1
from tests.episode_store.test_writer import identity


@pytest.mark.parametrize("fps", [29.97, 30])
def test_parent_domain_limits_are_frozen_but_child_timing_and_verdict_are_independent(
    monkeypatch, fps
):
    profile = QualityProfileV1(
        profile_id="actual-parent-profile",
        required_topics={"/action", "/joint_states"},
        action={"topic": "/action", "maximum_jump_risk": 0.5},
        joint_limits={"elbow": (-1, 1)},
    )
    evaluation = QualityEngine().evaluate_with_evidence(
        QualityInputV1(
            rollout_id="parent",
            source_sha256="a" * 64,
            start_ns=0,
            end_ns=1_000_000_000,
            topic_timestamps_ns={},
        ),
        profile,
    )
    assert evaluation.report.status.value == "RISK"
    documents = {
        "hc/quality.json": evaluation.report.model_dump(mode="json"),
        "hc/quality_rules.json": evaluation.evidence.model_dump(mode="json"),
    }
    package = SimpleNamespace(
        receipt=SimpleNamespace(
            data=DataVersionRef(
                identity=identity().model_copy(update={"rollout_id": "parent"}),
                data_version_id="test-parent",
                version=1,
                content_hash="b" * 64,
                schema_snapshot_id="test-schema",
                package_prefix="test-only",
                frame_count=30,
            )
        ),
        manifest=SimpleNamespace(input=SimpleNamespace(source_sha256="a" * 64)),
        entries=documents,
        document=documents.__getitem__,
    )
    info = {
        "fps": fps,
        "hc.feature_mapping": {"/action": "action", "/joint_states": "observation.state"},
    }
    result = derivation_quality.freeze_quality(package, info, 100, 33_366_800, None, 2)
    assert result["profile"]["default_timing"]["minimum_frequency_hz_risk"] == (
        27 if fps == 30 else fps * 0.95
    )
    assert result["scope"] == "selected_aligned_episode"
    assert "status" not in result
    assert result["profile"]["action"] == profile.action.model_dump(mode="json")
    assert result["profile"]["joint_limits"] == {"elbow": [-1.0, 1.0]}
    assert result["profile"]["default_timing"]["target_frequency_hz"] == fps
    assert result["expected_frames"] == {"/action": 2, "/joint_states": 2}
    assert result["end_basis"] == "last_sample_plus_nominal_period"
    assert result["parent_profile_sha256"] == profile.content_sha256()
    monkeypatch.setattr(
        derivation_quality, "QualityProfileV1", lambda **kw: pytest.fail("retry refroze policy")
    )
    assert (
        derivation_quality.freeze_quality(package, info, 100, 33_366_800, None, 2, result) == result
    )
    for bad_count in (True, 2.0, 0, -1):
        forged = copy.deepcopy(result)
        forged["expected_frames"]["/action"] = bad_count
        with pytest.raises(ValidationError):
            derivation_quality.freeze_quality(package, info, 100, 33_366_800, None, 2, forged)
