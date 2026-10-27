"""The scheme is the only user configuration for format admission/QC."""
from hc_data_platform.capture_schemes.hc_tj import configuration
from hc_data_platform.capture_schemes.quality import from_snapshot
from hc_data_platform.capture_schemes.schema import native_topic


def test_quality_compiles_selected_scheme_without_project_policy_or_fixed_rate():
    cfg, output = configuration(clock_evidence="synthetic single ROS clock")
    snapshot = {"output": output.model_dump(mode="json"), "effective_adapter": {
        "source_format": "MCAP", "configuration": cfg.model_dump(mode="json")}}
    topics = {native_topic(name) for name in output.features}
    compiled = from_snapshot(snapshot, topics, 29)
    assert compiled.required_topics == topics
    assert compiled.default_timing.target_frequency_hz == 29
    assert compiled.default_timing.minimum_frequency_hz_reject == 29 * .75
    assert compiled.joint_topic == native_topic("observation.state")
    assert compiled.action.topic == native_topic("action")
    assert compiled == from_snapshot(snapshot, topics, 29)
    cfg2, output2 = configuration(holobrain=False, clock_evidence="synthetic single ROS clock")
    other = from_snapshot({"output": output2.model_dump(mode="json"), "effective_adapter": {
        "source_format": "MCAP", "configuration": cfg2.model_dump(mode="json")}},
        {native_topic(name) for name in output2.features}, 31)
    assert compiled.profile_id != other.profile_id
    assert native_topic("action.hand.right") not in other.required_topics


def test_scheme_rules_change_qc_result_and_preserve_prior_snapshot():
    import copy
    from hc_data_platform.quality.engine import QualityEngine
    from hc_data_platform.quality.models import QualityInputV1, JointObservation
    _, output = configuration(holobrain=False, clock_evidence="synthetic clock")
    old = {"output": output.model_dump(mode="json"), "quality_policy": {"joint_limits": {"leg_1": [-1, 1]}}}
    new = copy.deepcopy(old)
    new["quality_policy"]["joint_limits"]["leg_1"] = [-2, 2]
    a = from_snapshot(old, {"/observation/state"}, 30)
    b = from_snapshot(new, {"/observation/state"}, 30)
    assert a.profile_id != b.profile_id
    assert a == from_snapshot(old, {"/observation/state"}, 30)
    data = QualityInputV1(rollout_id="fixture", source_sha256="a"*64, start_ns=0,
        end_ns=10**9, topic_timestamps_ns={"/observation/state": tuple(i*10**9//30 for i in range(30))},
        joints=(JointObservation(timestamp_ns=0, positions={"leg_1": 1.5}),))
    first, second = (QualityEngine().evaluate(data, p) for p in (a, b))
    assert any(f.code == "QC_JOINT_OUT_OF_RANGE" for f in first.findings)
    assert not any(f.code == "QC_JOINT_OUT_OF_RANGE" for f in second.findings)
    evaluation = QualityEngine().evaluate_with_evidence(data, a)
    assert evaluation.report == first
    assert evaluation.evidence.engine_version == 'be06-qc/4'
    assert 'UNSUPPORTED_ENGINE_EVIDENCE' not in evaluation.evidence.model_dump_json()


def test_all_legacy_rule_groups_are_owned_by_scheme_and_use_source_mapping():
    from hc_data_platform.capture_schemes.models import SchemeDraft
    cfg, output = configuration(clock_evidence="synthetic clock")
    value = SchemeDraft(name="QC fixture", expected_revision=0, output=output,
        adapters=[dict(source_format="MCAP", parser_version="native-mcap-recording/4", configuration=cfg)],
        quality_policy={"timing": {"maximum_gap_ns_reject": 200000000},
          "feature_images": {"observation.images.head": {"maximum_black_frame_ratio_reject": .8}},
          "feature_timing": {"action": {"minimum_frequency_ratio_reject": .2}},
          "image": {"black_luma_threshold": 8}, "action": {"maximum_jump_reject": 1.2},
          "point_cloud": {"minimum_point_count": 10},
          "feature_point_clouds": {"observation.state": {"maximum_point_count": 100}},
          "synchronization": {"maximum_p95_offset_ns_reject": 200000000},
          "completeness": {"minimum_ratio_reject": .5}}).model_dump(mode="json")
    topics = {name: '/mapped/' + name for name in output.features}
    p = from_snapshot(value, set(topics.values()), 29, feature_topics=topics)
    assert p.image_for(topics['observation.images.head']).maximum_black_frame_ratio_reject == .8
    assert p.timing_for(topics['action']).minimum_frequency_hz_reject == 29 * .2
    assert p.default_timing.maximum_gap_ns_reject == 200000000
    assert p.action.maximum_jump_reject == 1.2
    assert p.default_image.black_luma_threshold == 8
    assert p.default_point_cloud.minimum_point_count == 10
    assert p.point_cloud_for(topics['observation.state']).maximum_point_count == 100
    assert p.modality_offset.maximum_p95_offset_ns_reject == 200000000
    assert p.complete_step.minimum_ratio_reject == .5


def test_historical_empty_policy_serialization_and_invalid_rules():
    import pytest
    from pydantic import ValidationError
    from hc_data_platform.capture_schemes.quality_models import CaptureQualityPolicy
    assert CaptureQualityPolicy.model_validate({}).model_dump(mode='json') == {}
    for value in ({"timing": {"minimum_frequency_ratio_reject": 1}},
                  {"joint_limits": {"axis": [2, 1]}}, {"action": {"topic": "/other"}}):
        with pytest.raises(ValidationError):
            CaptureQualityPolicy.model_validate(value)
