"""Export only required axes from richer or differently ordered joint messages."""

from collections import Counter

import pytest

from hc_data_platform.continuous_recordings.joint_normalization import HANDS, HOLOBRAIN_JOINTS, JOINTS
from hc_data_platform.episode_store.holobrain_export import joints


def inputs():
    value = {"name": list(JOINTS), "position": list(range(17)), "velocity": [], "effort": []}
    hands = {name: ({"name": [name], "position": [20 + i]}, {})
             for i, name in enumerate(HANDS.values())}
    return value, hands


def test_extra_axes_and_hand_fields_are_ignored_and_required_axes_reordered():
    value, hands = inputs()
    names = ["head_1", *reversed(JOINTS), "head_1", "head_2"]
    value["name"] = names
    for field in ("position", "velocity", "effort"):
        value[field] = [JOINTS.index(n) if n in JOINTS else float("nan") for n in names]
    for hand, _ in hands.values():
        hand["name"] = ["unused", *hand["name"], "unused"]
        hand["position"] = [float("nan"), *hand["position"], "invalid extra"]
    actual = joints((value, {"capture_time_ns": 123}), hands, Counter())
    assert actual["names"] == list(HOLOBRAIN_JOINTS)
    assert actual["position"]["data"] == [*range(7), 20, *range(7, 14), 21, *range(14, 17)]
    assert actual["velocity"]["data"] == [*range(7), 0, *range(7, 14), 0, *range(14, 17)]
    assert actual["effort"] == actual["velocity"]
    assert actual["position"]["shape"] == [1, 19]


def test_unused_axes_do_not_need_values():
    value, hands = inputs()
    value["name"] += ["head_1", "head_2"]
    for hand, _ in hands.values():
        hand["name"] += ["unused"]
    missing = Counter()
    actual = joints((value, {"capture_time_ns": 123}), hands, missing)
    assert len(actual["position"]["data"]) == 19
    assert actual["velocity"]["data"] == [0] * 19
    assert sum(missing.values()) == 38


@pytest.mark.parametrize("defect", ["missing", "duplicate", "missing_position", "nonfinite_position", "partial_velocity"])
def test_required_axis_defects_still_fail(defect):
    value, hands = inputs()
    if defect == "missing":
        value["name"].pop()
    elif defect == "duplicate":
        value["name"].append(JOINTS[0])
    elif defect == "missing_position":
        value["position"].pop()
    elif defect == "nonfinite_position":
        value["position"][0] = float("nan")
    else:
        value["velocity"] = [1]
    with pytest.raises(ValueError, match="HOLOBRAIN_"):
        joints((value, {"capture_time_ns": 123}), hands, Counter())


@pytest.mark.parametrize("defect", ["missing", "duplicate", "missing_position", "nonfinite_position"])
def test_required_hand_defects_still_fail(defect):
    value, hands = inputs()
    name = next(iter(hands))
    hand = hands[name][0]
    if defect == "missing":
        del hands[name]
    elif defect == "duplicate":
        hand["name"].append(name)
    elif defect == "missing_position":
        hand["position"] = []
    else:
        hand["position"] = [float("nan")]
    with pytest.raises(ValueError, match="HOLOBRAIN_"):
        joints((value, {"capture_time_ns": 123}), hands, Counter())
