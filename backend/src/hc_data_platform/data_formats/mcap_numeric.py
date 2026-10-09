"""Explicit MCAP vector normalization; callers freeze the selected signal topics."""

import math
from typing import Any

REVISION = "mcap-numeric-vector/1"


def normalize_vector(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("MCAP numeric vector must be an object")
    keys = [key for key in ("values", "value", "position", "positions", "data") if key in value]
    if len(keys) != 1:
        raise ValueError("MCAP numeric vector is missing or ambiguous")
    vector = value[keys[0]]
    if (
        not isinstance(vector, (tuple, list))
        or not 1 <= len(vector) <= 256
        or any(type(item) not in (int, float) or not math.isfinite(item) for item in vector)
    ):
        raise ValueError("MCAP numeric vector must contain finite values")
    if any(type(item) is int and int(float(item)) != item for item in vector):
        raise ValueError("MCAP numeric integer cannot be represented exactly as float64")
    if "name" in value and "names" in value and value["name"] != value["names"]:
        raise ValueError("MCAP numeric vector has conflicting names")
    names = value.get("name", value.get("names"))
    if names is not None and (
        not isinstance(names, (tuple, list))
        or len(names) != len(vector)
        or any(not isinstance(name, str) or not name.strip() for name in names)
        or len(set(names)) != len(names)
    ):
        raise ValueError("MCAP numeric vector names differ from its dimensions")
    return {
        "values": [float(item) for item in vector],
        "names": None if names is None else list(names),
        **({"units": value["units"]} if "units" in value else {}),
    }
