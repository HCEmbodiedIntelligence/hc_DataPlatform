import copy

import pytest
from pydantic import ValidationError

from hc_data_platform.capture_schemes.contracts import (
    compare_lerobot,
    effective_adapter,
    structure_summary,
)
from hc_data_platform.capture_schemes.models import DeviceMapping, SchemeDraft
from hc_data_platform.core.errors import ProblemException


def draft(revision=0, *, fps=15):
    return SchemeDraft.model_validate(
        {
            "expected_revision": revision,
            "name": "Joint capture",
            "software": "fixture/v1",
            "adapters": [{"source_format": "LEROBOT_V3"}],
            "output": {
                "frequency_hz": fps,
                "time_source": "FRAME_TIMESTAMP",
                "features": {
                    "observation.state": {
                        "dtype": "float32",
                        "shape": [2],
                        "names": ["left", "right"],
                        "units": ["rad", "rad"],
                        "role": "state",
                    },
                    "action": {
                        "dtype": "float32",
                        "shape": [2],
                        "names": ["right", "left"],
                        "units": ["rad", "rad"],
                        "role": "action",
                    },
                    "observation.base.velocity": {
                        "dtype": "float32",
                        "shape": [1],
                        "names": ["vx"],
                        "units": ["m/s"],
                        "role": "numeric",
                        "required": False,
                    },
                },
            },
        }
    )


def info():
    return {
        "codebase_version": "v3.0",
        "fps": 15,
        "total_frames": 167,
        "features": {
            "observation.state": {
                "dtype": "float32",
                "shape": [2],
                "names": ["left", "right"],
            },
            "action": {"dtype": "float32", "shape": [2], "names": ["right", "left"]},
        },
    }


def test_semantic_comparison_preserves_axis_order_units_and_batch_independent_hash():
    command = draft()
    adapter = command.adapters[0].model_dump(mode="json")
    result = compare_lerobot(command.output, adapter, info())
    assert result["differences"] == []
    assert result["presence"] == {
        "observation.state": True,
        "action": True,
        "observation.base.velocity": False,
    }
    for field, actual in (
        ("names", ["left", "right"]),
        ("shape", [3]),
        ("dtype", "float64"),
        ("units", ["degree", "degree"]),
    ):
        changed = info()
        changed["features"]["action"][field] = actual
        delta = compare_lerobot(command.output, adapter, changed)
        assert any(d["path"] == "features.action." + field for d in delta["differences"])
    assert structure_summary(info()) == structure_summary({**info(), "total_frames": 1000})
    changed = info()
    changed["fps"] = 30
    assert compare_lerobot(command.output, adapter, changed)["differences"] == [
        {"path": "fps", "expected": 15, "actual": 30},
    ]


def test_device_mapping_cannot_override_units_layout_or_unknown_features():
    with pytest.raises(ValidationError):
        DeviceMapping.model_validate({"units": ["degrees"]})
    document = draft().model_dump(mode="json", exclude={"expected_revision"})
    with pytest.raises(ProblemException, match="CAPTURE_MAPPING_INVALID"):
        effective_adapter(
            document,
            "LEROBOT_V3",
            DeviceMapping(
                sources={"LEROBOT_V3": {"unknown": "arbitrary"}},
            ),
        )
    original = copy.deepcopy(document)
    adapter = effective_adapter(
        document,
        "LEROBOT_V3",
        DeviceMapping(
            sources={"LEROBOT_V3": {"observation.state": "observation.joints"}},
        ),
    )
    assert adapter["feature_sources"]["observation.state"] == "observation.joints"
    assert document == original


def test_unknown_axis_semantics_cannot_be_published_as_a_dimension_guess():
    for change in ({"names": None}, {"units": ["unknown", "unknown"]}, {"names": ["a", "a"]}):
        value = draft().model_dump(mode="json")
        value["output"]["features"]["action"].update(change)
        with pytest.raises(ValidationError):
            SchemeDraft.model_validate(value)


def test_automatic_inputs_share_one_definition_and_one_ros_mapping():
    from hc_data_platform.capture_schemes.draft_frequency import with_frequency

    value = draft(fps=None).model_dump(mode="json")
    value["input_mode"] = "AUTO"
    auto = SchemeDraft.model_validate(value).model_dump(mode="json")
    assert {a["source_format"] for a in auto["adapters"]} == {
        "MCAP",
        "CAPTURE_BUNDLE",
        "LEROBOT_V3",
    }
    resolved = with_frequency(auto, 15)
    assert resolved["adapters"][1]["configuration"] == resolved["adapters"][2]["configuration"]
    assert resolved["adapters"][1]["configuration"]["semantic_output"] == resolved["output"]
    mapping = DeviceMapping(sources={"MCAP": {"state": "/actual/state"}})
    for fmt in ("MCAP", "CAPTURE_BUNDLE"):
        assert (
            effective_adapter(resolved, fmt, mapping)["configuration"]["state_topic"]
            == "/actual/state"
        )
    with pytest.raises(ProblemException):
        effective_adapter(
            resolved,
            "MCAP",
            DeviceMapping(sources={"MCAP": {"state": "/one"}, "CAPTURE_BUNDLE": {"state": "/two"}}),
        )
