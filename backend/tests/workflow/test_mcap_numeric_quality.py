import json
from types import SimpleNamespace

import pytest
from hc_data_platform.verification.ports import RegisteredDecoderProbe

from hc_data_platform.quality.engine import QualityEngine
from hc_data_platform.quality.models import (
    QualityCode,
    QualityInputV1,
    QualityProfileV1,
    QualityStreamObservationV1,
)
from hc_data_platform.workflow.mcap_quality import numeric_quality_input, numeric_vector
from hc_data_platform.workflow.models import QualityActivityInput


def inputs():
    base = QualityInputV1(
        rollout_id="mcap-numeric",
        source_sha256="a" * 64,
        start_ns=0,
        end_ns=1_000_000_000,
        topic_timestamps_ns={},
    )
    profile = QualityProfileV1(profile_id="numeric-default", required_topics=frozenset({"/action"}))
    decoder = RegisteredDecoderProbe(
        {("json", "jsonschema"): lambda _schema, body: json.loads(body)}
    )
    return base, profile, decoder


def message(topic, value, stamp=0):
    return (
        SimpleNamespace(name="hc/Numeric", encoding="jsonschema", data=b"{}"),
        SimpleNamespace(topic=topic, message_encoding="json"),
        SimpleNamespace(log_time=stamp, data=json.dumps(value).encode()),
    )


@pytest.mark.parametrize("present", [True, False])
def test_numeric_evidence_drives_default_action_rule_even_with_identical_topic_counts(present):
    base, profile, decoder = inputs()
    messages = (
        (message("/action", {"value": [i / 100]}, i * 1_000_000_000 // 30) for i in range(30))
        if present
        else ()
    )
    with numeric_quality_input(base, profile, messages, decoder) as facts:
        assert len(facts.actions) == (30 if present else 0)
        result = QualityEngine().evaluate_with_evidence(
            facts,
            profile,
            observations=(
                QualityStreamObservationV1(topic="/action", timestamp_ns=i * 1_000_000_000 // 30)
                for i in range(30)
            ),
        )
        assert (
            QualityCode.ACTION_MISSING in {f.code for f in result.report.findings}
        ) is not present
    # Missing revision remains the legacy projection for persisted workflow payloads.
    historical = QualityActivityInput(
        organization_id="o", project_id="p", region_code="r", data=base, profile=profile
    )
    assert historical.numeric_projection_revision is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        {"value": [True]},
        {"value": [float("nan")]},
        {"value": [0], "values": [0]},
        {"value": [0] * 257},
    ],
)
def test_invalid_or_ambiguous_numeric_content_is_not_counted_as_valid_action(body):
    with pytest.raises(ValueError, match="MCAP_NUMERIC_FACTS_INVALID"):
        numeric_vector(body)


def test_joint_names_are_observed_and_not_fabricated():
    base, profile, decoder = inputs()
    profile = profile.model_copy(update={"joint_limits": {"j1": (-1.0, 1.0)}})
    with numeric_quality_input(
        base, profile, [message("/joint_states", {"name": ["j1"], "position": [0.3]})], decoder
    ) as facts:
        assert next(iter(facts.joints)).positions == {"j1": 0.3}
    with pytest.raises(ValueError, match="joint names"), numeric_quality_input(
        base, profile, [message("/joint_states", {"position": [0.3]})], decoder
    ):
        pass
