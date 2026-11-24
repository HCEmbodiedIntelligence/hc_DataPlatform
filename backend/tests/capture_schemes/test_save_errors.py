import pytest
from pydantic import ValidationError

from hc_data_platform.capture_schemes.save_errors import save_validation_hint
from hc_data_platform.continuous_recordings.mcap_contracts import McapProcessingConfig
from .test_mcap_v4 import config


def test_explain_action_policy_conflict_including_legacy_union_errors():
    cfg = config().model_dump(mode="json")
    cfg["postprocessing_rules"] = {"version": 2, "rules": [
        {"kind": k, "enabled": True}
        for k in ("fill_initial_action", "fill_action_from_next_frame", "interpolate_short_gaps")
    ]}
    with pytest.raises(ValidationError) as rejected:
        McapProcessingConfig.model_validate(cfg)
    reason, message = save_validation_hint(rejected.value.errors())
    assert reason == "ActionPolicyConflict"
    assert "首尾" in message and "首条" in message


def test_untrusted_value_and_location_are_not_echoed():
    reason, message = save_validation_hint([{"loc": ("body", "quality_policy", "private-example"),
        "msg": "Value error, private-example", "input": "private-example"}])
    assert reason == "QualityPolicy"
    assert "private-example" not in message
