"""Versioned row selection; source acquisition clocks are never rewritten."""

import json

POLICY = "drop_invalid_rows_preserve_source_time/v1"
PROCESSING_POLICY = "drop_invalid_compact_all_modalities/v2"


def usable_row(row):
    return bool(row.get("hc.sample_valid", True)) and not row.get("hc.annotation.excluded", False)


def source_stamp(row, column):
    stamp = row[column]
    if isinstance(stamp, list) and len(stamp) == 1:
        stamp = stamp[0]
    if type(stamp) is not int:
        raise ValueError("retained source timestamp must be an integer")
    return stamp


def refresh_repetition(row, previous):
    """A repeat is relative to the preceding retained row, using source identity."""
    for key in row:
        if not key.startswith("hc.repeated."):
            continue
        feature = key.removeprefix("hc.repeated.")
        stamps = row.get("hc.source_timestamps_ns." + feature, "[]")
        stamps = json.loads(stamps) if isinstance(stamps, str) else stamps
        identity = tuple(stamps) if stamps and len(stamps) == 1 else None
        reference = row.get("hc.source_ref." + feature)
        reference = json.loads(reference) if isinstance(reference, str) else reference
        if isinstance(reference, dict) and reference.get("depth_source_seq") is not None:
            identity = (reference["depth_source_id"], reference["depth_source_seq"])
        if not row.get("hc.valid." + feature, True):
            identity = None
        row[key] = identity is not None and identity == previous.get(feature)
        previous[feature] = identity
