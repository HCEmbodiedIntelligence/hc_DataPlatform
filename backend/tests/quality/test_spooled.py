"""Disk-backed numeric facts must execute exactly the tuple-based QC rules."""

from pathlib import Path

import pytest

from hc_data_platform.quality.engine import QualityEngine
from hc_data_platform.quality.models import ActionObservation, JointObservation
from hc_data_platform.quality.spooled import SpilledQualityInput
from tests.quality.test_quality import _input, _profile


def test_spilled_facts_preserve_report_evidence_order_percentile_and_cleanup():
    stamps = [0, 33_333_333, 66_666_667]
    actions = tuple(ActionObservation(timestamp_ns=t, values=[i, -i]) for i, t in enumerate(stamps))
    joints = tuple(
        JointObservation(timestamp_ns=t, positions={"elbow": i}) for i, t in enumerate(stamps)
    )
    offsets = tuple((-1 if i % 2 else 1) * i * 1_000_000 for i in range(101))
    data = _input(
        actions=actions,
        joints=joints,
        modality_offsets_ns={"/camera/front": offsets},
        complete_step_ratio=0.5,
    )
    profile = _profile(
        joint_limits={"elbow": (-0.5, 0.5)},
        action={"maximum_jump_risk": 0.1, "maximum_jump_reject": 1.0},
    )
    expected = QualityEngine().evaluate_with_evidence(data, profile)
    base = _input(complete_step_ratio=0.5, actions=())
    with SpilledQualityInput(base) as spilled:
        directory = Path(spilled.directory.name)
        for action in actions:
            spilled.add("action", action)
        for joint in joints:
            spilled.add("joint", joint)
        for value in offsets:
            spilled.add_offset("/camera/front", value)
        assert tuple(spilled.actions) == tuple(spilled.actions) == actions
        assert tuple(spilled.joints) == joints
        assert tuple(spilled.modality_offsets_ns["/camera/front"]) == offsets
        assert spilled.modality_offsets_ns["/camera/front"].absolute_percentile(0.95) == 95_000_000
        assert QualityEngine().evaluate_with_evidence(spilled, profile) == expected
        with pytest.raises(ValueError, match="outside"):
            spilled.add("action", ActionObservation(timestamp_ns=base.end_ns, values=[1]))
        with pytest.raises(ValueError, match="finite"):
            spilled.add("action", actions[0].model_copy(update={"values": (float("nan"),)}))
        with pytest.raises(ValueError, match="integer"):
            spilled.add_offset("/camera/front", 1.5)
    assert not directory.exists()


def test_spilled_facts_cleanup_on_failure():
    with pytest.raises(RuntimeError), SpilledQualityInput(_input(actions=())) as facts:
        directory = Path(facts.directory.name)
        raise RuntimeError("interrupted")
    assert not directory.exists()
