import pytest

from hc_data_platform.continuous_recordings.processing import (
    PostgresContinuousEpisodeWorkflowInputResolver,
)
from hc_data_platform.quality.models import QualityProfileV1
from hc_data_platform.workflow.ingest_dispatch import IngestWorkflowPlanBlocked


class Cursor:
    def __init__(self, profiles):
        self.rows = [(p.model_dump(mode="json"),) for p in profiles]

    def execute(self, *args):
        pass

    def fetchall(self):
        return self.rows


def select(profiles, topics=frozenset({"/state"})):
    return PostgresContinuousEpisodeWorkflowInputResolver._quality_profile(
        Cursor(profiles), "01", topics
    )


def test_new_threshold_version_preserves_history_and_is_selected():
    old = QualityProfileV1(profile_id="capture", required_topics={"/state"})
    old = old.model_copy(
        update={
            "default_timing": old.default_timing.model_copy(
                update={"minimum_frequency_hz_risk": 28.5}
            )
        }
    )
    new = old.model_copy(
        update={
            "profile_version": 2,
            "default_timing": old.default_timing.model_copy(
                update={"minimum_frequency_hz_risk": 27.0}
            ),
        }
    )
    assert select([new, old]) == new
    assert select([old, new]) == new
    assert old.default_timing.minimum_frequency_hz_risk == 28.5


def test_identical_policy_aliases_select_one_existing_identity_deterministically():
    first = QualityProfileV1(profile_id="a", required_topics={"/state"})
    alias = first.model_copy(update={"profile_id": "b", "profile_version": 3})
    assert select([alias, first]) == first
    assert select([first, alias]) == first
    assert alias.profile_id == "b" and alias.profile_version == 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("engine_version", "be06-qc/3"),
        ("joint_limits", {"arm": (-1.0, 1.0)}),
        ("joint_topic", "/other-state"),
        ("default_timing", {"minimum_frequency_hz_risk": 28.5}),
        ("action", {"topic": "/action", "maximum_jump_risk": 0.1}),
    ],
)
def test_different_matching_policy_rules_remain_ambiguous(field, value):
    first = QualityProfileV1(profile_id="a", required_topics={"/state"})
    changed = QualityProfileV1.model_validate(
        {
            **first.model_dump(),
            "profile_id": "b",
            field: value,
        }
    )
    with pytest.raises(IngestWorkflowPlanBlocked, match="exactly one"):
        select([first, changed])


def test_new_topic_contract_does_not_fall_back_to_old_version():
    old = QualityProfileV1(profile_id="capture", required_topics={"/state"})
    new = old.model_copy(update={"profile_version": 2, "required_topics": frozenset({"/other"})})
    with pytest.raises(IngestWorkflowPlanBlocked, match="exactly one"):
        select([old, new])
