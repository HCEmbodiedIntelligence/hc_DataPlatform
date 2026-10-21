"""Aggregate declared capture gaps without turning each missing row into rejection."""

import json
from collections import Counter

from mcap.reader import make_reader

POLICY = "capture-availability/v1"


def summarize(paths, start_ns, end_ns, profile):
    frames = {}
    for path in paths:
        with path.open("rb") as stream:
            for _, channel, message in make_reader(stream, validate_crcs=True).iter_messages():
                if (
                    channel.topic != "/openarm/capture_validity"
                    or not start_ns <= message.log_time < end_ns
                ):
                    continue
                value = json.loads(message.data)
                if (
                    type(value.get("valid")) is not bool
                    or not isinstance(value.get("reasons"), list)
                    or any(not isinstance(x, str) for x in value["reasons"])
                    or message.log_time in frames
                ):
                    raise ValueError("malformed or duplicate capture validity evidence")
                frames[message.log_time] = value
    return aggregate(frames, start_ns, end_ns, profile)


def aggregate(frames, start_ns, end_ns, profile):
    total = len(frames)
    fps = profile.default_timing.target_frequency_hz
    expected = round(end_ns * fps / 1e9) - round(start_ns * fps / 1e9)
    if total != expected:
        raise ValueError("capture validity evidence does not cover the selected window")
    invalid = {stamp for stamp, value in frames.items() if not value["valid"]}
    reasons = Counter(reason for value in frames.values() for reason in set(value["reasons"]))
    run = longest = 0
    for _stamp, value in sorted(frames.items()):
        if value["valid"]:
            run = 0
        else:
            run += 1
            longest = max(longest, run)
    ratio = (total - len(invalid)) / total
    # Use the frozen profile's existing completeness and interruption limits.
    timing = profile.default_timing
    rejects, warnings = [], []
    if ratio < profile.complete_step.minimum_ratio_reject:
        rejects.append("CAPTURE_VALID_RATIO_LOW")
    elif ratio < profile.complete_step.minimum_ratio_risk:
        warnings.append("CAPTURE_VALID_RATIO_LOW")
    if longest > timing.maximum_consecutive_missing_reject:
        rejects.append("CAPTURE_INTERRUPTION_TOO_LONG")
    elif longest > timing.maximum_consecutive_missing_risk:
        warnings.append("CAPTURE_INTERRUPTION")
    if invalid:
        warnings.append("CAPTURE_ROWS_MASKED")
    return {
        "policy": POLICY,
        "total_rows": total,
        "valid_rows": total - len(invalid),
        "invalid_rows": len(invalid),
        "valid_ratio": ratio,
        "longest_invalid_run": longest,
        "longest_invalid_duration_ns": round(longest * 1e9 / fps),
        "reason_counts": dict(reasons),
        "training_policy": "drop_invalid_rows_preserve_source_time/v1",
        "reject_codes": rejects,
        "warning_codes": warnings,
        "invalid_offsets_ns": sorted(invalid),
        "thresholds": {
            "minimum_valid_ratio": profile.complete_step.minimum_ratio_reject,
            "maximum_invalid_run": timing.maximum_consecutive_missing_reject,
        },
    }
