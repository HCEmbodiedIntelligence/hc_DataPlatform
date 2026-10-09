import math

import pytest

from hc_data_platform.data_formats.mcap_numeric import normalize_vector


def test_ros_joint_state_preserves_axis_order_precision_and_units_without_invented_names():
    raw = {
        "header": {"frame_id": "base", "stamp": {"sec": 3}},
        "name": ["right", "left"],
        "position": [0.25, 0.25 + 1e-12],
        "velocity": [1, 2],
        "effort": [0, 0],
        "units": "rad",
    }
    normalized = normalize_vector(raw)
    assert normalized == {"values": raw["position"], "names": ["right", "left"], "units": "rad"}
    assert normalized["values"][0] != normalized["values"][1]
    assert normalize_vector({"value": [1, 2]}) == {"values": [1.0, 2.0], "names": None}
    assert raw["velocity"] == [1, 2]  # Input is not mutated; sidecar retains full original.


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"values": []},
        {"values": [True]},
        {"values": [math.nan]},
        {"values": [math.inf]},
        {"values": [1], "position": [2]},
        {"position": [1], "name": ["x", "y"]},
        {"position": [1, 2], "name": ["x", "x"]},
        {"position": [1], "name": ["x"], "names": ["y"]},
        {"values": [2**53 + 1]},
    ],
)
def test_numeric_contract_rejects_missing_ambiguous_and_invalid_vectors(raw):
    with pytest.raises(ValueError):
        normalize_vector(raw)
