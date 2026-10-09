"""Decode numeric QC facts without retaining a whole MCAP in memory."""

import math
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from hc_data_platform.quality.models import (
    ActionObservation,
    JointObservation,
    QualityInputV1,
    QualityProfileV1,
)
from hc_data_platform.quality.spooled import SpilledQualityInput
from hc_data_platform.verification.ports import DecoderProbe

MCAP_NUMERIC_QUALITY_REVISION = "mcap-numeric-quality/1"


def numeric_vector(value: object) -> tuple[float, ...]:
    if not isinstance(value, dict):
        raise ValueError("MCAP_NUMERIC_FACTS_INVALID: numeric message must be an object")
    candidates = [value[key] for key in ("values", "value", "position", "data") if key in value]
    if len(candidates) != 1:
        raise ValueError("MCAP_NUMERIC_FACTS_INVALID: numeric vector is missing or ambiguous")
    vector = candidates[0]
    if (
        not isinstance(vector, (list, tuple))
        or not 1 <= len(vector) <= 256
        or any(type(item) not in (int, float) or not math.isfinite(item) for item in vector)
    ):
        raise ValueError("MCAP_NUMERIC_FACTS_INVALID: numeric vector must contain finite values")
    return tuple(float(item) for item in vector)


@contextmanager
def numeric_quality_input(
    base: QualityInputV1,
    profile: QualityProfileV1,
    messages: Iterable[tuple[Any, Any, Any]],
    decoder: DecoderProbe | None,
) -> Iterator[SpilledQualityInput]:
    with SpilledQualityInput(base) as facts:
        for schema, channel, message in messages:
            topic = str(channel.topic)
            if topic not in (profile.action.topic, profile.joint_topic):
                continue
            if (
                schema is None
                or decoder is None
                or not decoder.supports(channel.message_encoding, schema.encoding)
            ):
                raise ValueError("MCAP_NUMERIC_FACTS_INVALID: numeric decoder unavailable")
            value = decoder.probe(
                message_encoding=channel.message_encoding,
                schema_encoding=schema.encoding,
                schema_name=schema.name,
                schema_data=schema.data,
                message_data=message.data,
            )
            values = numeric_vector(value)
            assert isinstance(value, dict)
            timestamp = int(message.log_time)
            if topic == profile.action.topic:
                facts.add("action", ActionObservation(timestamp_ns=timestamp, values=values))
            if topic == profile.joint_topic:
                names = value.get("name", value.get("names"))
                # Unnamed vectors cannot prove named joint limits. Never invent names.
                if names is None and not profile.joint_limits:
                    continue
                if (
                    not isinstance(names, list)
                    or len(names) != len(values)
                    or any(not isinstance(name, str) or not name.strip() for name in names)
                    or len(set(names)) != len(names)
                ):
                    raise ValueError(
                        "MCAP_NUMERIC_FACTS_INVALID: joint names are missing or invalid"
                    )
                facts.add(
                    "joint",
                    JointObservation(
                        timestamp_ns=timestamp, positions=dict(zip(names, values, strict=True))
                    ),
                )
        yield facts
