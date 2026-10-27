"""Aggregate declared capture gaps without turning each missing row into rejection."""

import json
from collections import Counter

from mcap.reader import make_reader

POLICY = "capture-availability/v1"


def alignment_offsets(paths, start_ns, end_ns):
    """Read measured source-to-anchor deltas, including rejected nearest matches.

    Old recordings without a nearest diagnostic contribute accepted matches only;
    their missing rows remain visible in the independent completeness evidence.
    """
    from collections import defaultdict

    frames = defaultdict(dict)
    for path in paths:
        with path.open("rb") as stream:
            for _, channel, message in make_reader(stream, validate_crcs=True).iter_messages():
                if start_ns <= message.log_time < end_ns and channel.topic in {
                    "/action", "/observation/state", "source.timestamp_ns"
                }:
                    frames[message.log_time][channel.topic] = json.loads(message.data)
    offsets = defaultdict(list)
    for frame in frames.values():
        anchor = frame.get("source.timestamp_ns")
        if type(anchor) is not int:
            continue
        for topic in ("/action", "/observation/state"):
            value = frame.get(topic, {})
            stamp = value.get("source_timestamp_ns")
            if stamp is None:
                stamp = value.get("nearest_source_timestamp_ns")
            reference = value.get("alignment_reference_timestamp_ns", anchor)
            if type(stamp) is int and type(reference) is int:
                offsets[topic].append(stamp - reference)
    return dict(offsets)


def summarize(paths, start_ns, end_ns, profile, *, output=None):
    frames = {}
    vectors = {"/action": {}, "/observation/state": {}}
    versioned = (output or {}).get("algorithm") in {"capture-structure/v2", "capture-structure/v3"}
    for path in paths:
        with path.open("rb") as stream:
            for _, channel, message in make_reader(stream, validate_crcs=True).iter_messages():
                if (
                    not start_ns <= message.log_time < end_ns
                ):
                    continue
                if versioned and channel.topic in vectors:
                    if message.log_time in vectors[channel.topic]:
                        raise ValueError("duplicate joint validity evidence")
                    vectors[channel.topic][message.log_time] = json.loads(message.data)
                    continue
                if channel.topic != "/openarm/capture_validity":
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
    axes = {}
    if versioned:
        for topic, name in (("/action", "action"), ("/observation/state", "observation.state")):
            declaration = output["features"][name]
            names = declaration["names"]
            required = declaration.get("required_axes")
            required = set(names if required is None else required)
            if set(vectors[topic]) != set(frames):
                raise ValueError("joint validity evidence does not cover the selected window")
            missing = Counter()
            for stamp, value in vectors[topic].items():
                mask = value.get("validity", [])
                if value.get("names") != names or len(mask) != len(names) or any(type(v) is not bool for v in mask):
                    raise ValueError("malformed per-axis validity")
                missing.update(n for n, valid in zip(names, mask, strict=True) if not valid)
                absent = [n for n, valid in zip(names, mask, strict=True) if n in required and not valid]
                if absent:
                    frames[stamp]["valid"] = False
                    frames[stamp]["reasons"].extend(name + ":missing:" + n for n in absent)
            axes[name] = {"required_axes": sorted(required), "missing_rows_by_axis": dict(missing),
                          "optional_axes": [n for n in names if n not in required]}
    result = aggregate(frames, start_ns, end_ns, profile, preserve_source=versioned)
    if versioned:
        result["axis_validity"] = axes
        result["policy"] = "capture-availability/v2"
        if any(v["missing_rows_by_axis"].get(n) for v in axes.values() for n in v["optional_axes"]):
            result["warning_codes"].append("CAPTURE_OPTIONAL_AXES_UNAVAILABLE")
    return result


def aggregate(frames, start_ns, end_ns, profile, *, preserve_source=False):
    total = len(frames)
    fps = profile.default_timing.target_frequency_hz
    expected = round(end_ns * fps / 1e9) - round(start_ns * fps / 1e9)
    if not total or (not preserve_source and total != expected):
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
