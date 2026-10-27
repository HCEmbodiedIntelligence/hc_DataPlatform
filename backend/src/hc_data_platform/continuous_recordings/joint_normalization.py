"""Name-based HC TJ normalization, shared by all derived formats.

Only absent waist position commands may use measured state. Auxiliary fields
remain absent when incomplete; the original message and all clocks stay intact.
"""

from bisect import bisect_left

JOINTS = tuple(f"Joint{i}_{side}" for side in ("R", "L") for i in range(1, 8)) + (
    "leg_1", "leg_2", "zhi",
)
HANDS = {"/action/right_hand/joint_states": "R_ban", "/action/left_hand/joint_states": "L_ban"}
HOLOBRAIN_JOINTS = JOINTS[:7] + ("R_ban",) + JOINTS[7:14] + ("L_ban",) + JOINTS[14:]


def get_field(value, name, default=None):
    # This module is also imported by pure workflow contract validators.
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def capture_time(value, message):
    """Read integer ROS acquisition time; reject missing or unrelated clocks."""
    stamp = get_field(get_field(value, "header", {}), "stamp", {})
    sec, ns = get_field(stamp, "sec", None), get_field(stamp, "nanosec", None)
    if type(sec) is not int or type(ns) is not int or not 0 <= ns < 10**9:
        raise ValueError(f"MCAP_CAPTURE_CLOCK_REQUIRED: log_time={message.log_time}")
    result = sec * 10**9 + ns
    # Evidence is explicitly bound in the profile; this catches epoch/domain errors.
    if result <= 0 or abs(result - message.log_time) > 2_000_000_000:
        raise ValueError(f"MCAP_CLOCK_DOMAIN_MISMATCH: header={result} log={message.log_time}")
    return result


def nearest_index(stamps, target, tolerance):
    i = bisect_left(stamps, target)
    candidates = [j for j in (i - 1, i) if 0 <= j < len(stamps)]
    if not candidates:
        return None
    j = min(candidates, key=lambda j: (abs(stamps[j] - target), stamps[j], j))
    return j if abs(stamps[j] - target) <= tolerance else None


def reference(message, stamp, ordinal):
    return {"message_index": ordinal, "sequence": message.sequence,
            "capture_time_ns": stamp, "log_time_ns": message.log_time,
            "publish_time_ns": message.publish_time}


def normalize(value, names, *, topic, message, ordinal, states=(), state_times=(), action=False):
    from .mcap_selection import joint_values

    stamp = capture_time(value, message)
    selected = joint_values(value, names, names[:14] if action else names,
                            topic=topic, stamp=stamp)
    source = reference(message, stamp, ordinal)
    axes = {name: {"source": "command" if action else "measured_state"}
            for name in selected}
    missing = [name for name in names if name not in selected]
    fallback = None
    if missing:
        match = nearest_index(state_times, stamp, 20_000_000)
        if not action or set(missing) - set(JOINTS[14:]) or match is None:
            raise ValueError(f"MCAP_WAIST_STATE_UNAVAILABLE: {topic} axes={missing} capture_time_ns={stamp} tolerance_ms=20")
        measured, fallback = states[match]
        for name in missing:
            selected[name] = measured[name]
            axes[name] = {"source": "state_fallback", "state": fallback,
                          "delta_ns": fallback["capture_time_ns"] - stamp}
    header = get_field(value, "header", {})
    raw_stamp = get_field(header, "stamp", {})
    result = {"header": {"stamp": {"sec": get_field(raw_stamp, "sec"),
                                    "nanosec": get_field(raw_stamp, "nanosec")},
                         "frame_id": get_field(header, "frame_id", "")},
              "name": list(names), "position": [selected[name] for name in names]}
    original_names = list(get_field(value, "name", []))
    unavailable = []
    for field in ("velocity", "effort"):
        values = list(get_field(value, field, []))
        complete = bool(values) and all(name in original_names for name in names)
        result[field] = [values[original_names.index(name)] for name in names] if complete else []
        if not complete:
            unavailable.append(field)
    return result, {"topic": topic, "action" if action else "message": source,
                    "axes": axes, "unavailable_fields": unavailable}
