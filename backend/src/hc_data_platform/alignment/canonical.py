"""Canonical JSON encoding used by fragment hashes and Arrow payloads."""

from __future__ import annotations

import base64
import json
import math
from enum import Enum
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Encode supported aligned values without process- or locale-dependent details."""

    return json.dumps(
        normalize_for_json(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def normalize_for_json(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$bytes_base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite values cannot be encoded deterministically")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("aligned object keys must be strings")
        return {str(key): normalize_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_for_json(item) for item in value]
    if isinstance(value, Enum):
        return normalize_for_json(value.value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"unsupported aligned value type: {type(value).__name__}")
