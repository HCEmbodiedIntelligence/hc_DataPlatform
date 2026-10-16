"""Raw robot configs use the same depth contract as platform preparation."""
import json
from pathlib import Path

import pytest

from hc_data_platform.continuous_recordings.openarm_raw.config import validate


def test_declared_depth_source_is_checked_by_platform_validator():
    fixture = Path(__file__).parents[1] / "fixtures/openarm-raw-v1/session.json"
    config = json.loads(fixture.read_text())["config"]
    source = next(s for s in config["sources"].values() if s["kind"] == "rgbd")
    source["depth_source"] = {
        "schema_version": "hc-depth-source/1", "encoding": "16UC1", "unit": "mm",
        "scale_m_per_unit": 0.001, "invalid_values": [0], "invalid_nonfinite": True,
        "registered_to_rgb": False, "registration_evidence": "native depth grid",
        "evidence": {"kind": "explicit_configuration", "reference": "test", "version": "1"},
    }
    checked = validate(config)
    assert any(s.get("depth_source") == source["depth_source"] for s in checked["sources"].values())
    source["depth_source"]["scale_m_per_unit"] = 1.0
    with pytest.raises(ValueError, match="unit disagrees with scale"):
        validate(config)
