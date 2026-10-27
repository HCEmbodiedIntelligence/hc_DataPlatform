"""QC is compiled from the selected immutable scheme, never a project-level match."""
import copy
import pytest
from hc_data_platform.capture_schemes.quality import from_snapshot


def snapshot(policy=None):
    return {"output": {"features": {"observation.state": {"role": "state"}, "action": {"role": "action"}}},
            "quality_policy": policy or {}}


def test_new_threshold_version_preserves_frozen_history():
    old = snapshot({"timing": {"minimum_frequency_ratio_risk": .95}})
    new = copy.deepcopy(old)
    new["quality_policy"]["timing"]["minimum_frequency_ratio_risk"] = .9
    first = from_snapshot(old, {'/observation/state','/action'}, 30)
    second = from_snapshot(new, {'/observation/state','/action'}, 30)
    assert first.profile_id != second.profile_id
    assert first.default_timing.minimum_frequency_hz_risk == 28.5
    assert second.default_timing.minimum_frequency_hz_risk == 27
    assert first == from_snapshot(old, {'/observation/state','/action'}, 30)


@pytest.mark.parametrize('field,value', [
    ('joint_limits', {'arm': [-1, 1]}),
    ('timing', {'maximum_gap_ns_reject': 200000000}),
    ('action', {'maximum_jump_risk': .1}),
    ('image', {'black_luma_threshold': 8}),
    ('point_cloud', {'minimum_point_count': 10}),
    ('synchronization', {'maximum_p95_offset_ns_reject': 200000000}),
    ('completeness', {'minimum_ratio_reject': .5}),
])
def test_each_scheme_rule_group_changes_execution_identity(field,value):
    first = from_snapshot(snapshot(), {'/observation/state','/action'}, 30)
    changed = from_snapshot(snapshot({field:value}), {'/observation/state','/action'}, 30)
    assert first.profile_id != changed.profile_id
    assert changed.engine_version == 'be06-qc/4'


def test_changing_selected_fields_cannot_reuse_previous_rules():
    first = from_snapshot(snapshot(), {'/observation/state','/action'}, 30)
    other = from_snapshot(snapshot(), {'/other'}, 30)
    assert first.profile_id != other.profile_id
    assert other.required_topics == {'/other'}
