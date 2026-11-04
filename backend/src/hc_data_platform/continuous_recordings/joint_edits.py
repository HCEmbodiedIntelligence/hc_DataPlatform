"""Apply version 2 edits to derived main JointState messages before quality checks."""

from bisect import bisect_left

from .joint_normalization import JOINTS, capture_time, get_field, nearest_index, reference
from .mcap_selection import joint_values

FIELDS = ("position", "velocity", "effort")


def read_fields(value, names, *, topic, stamp, optional=(), nan_axes=()):
    """Validate only retained values, but always check the raw array layout."""
    positions = joint_values(value, names, set(names) - set(optional), topic=topic, stamp=stamp,
                             nan_as_missing=nan_axes, allow_empty=not names or bool(optional))
    indices = {name: i for i, name in enumerate(get_field(value, "name", []))}
    result = {"position": positions}
    for field in FIELDS[1:]:
        values = get_field(value, field, [])
        result[field] = {n: float(values[indices[n]]) for n in names if n in indices} if len(values) else {}
    return result


def edit_joints(value, names, *, rules, action, states, state_times, topic, message, ordinal, profile=True, required=None):
    aligns = rules.action_from_state or "fill_position" in rules.active
    stamp = capture_time(value, message, verify_domain=profile) if profile or aligns else message.log_time
    names = list(dict.fromkeys([*names, *rules.zero_joints]))
    zeros = set(rules.zero_joints)
    copies = set(rules.action_from_state) if action else set()
    kept = [n for n in names if n not in zeros and n not in copies]
    fill = rules.active.get("fill_position") if action else None
    optional = (JOINTS[14:] if fill.missing_position_axes is None else fill.missing_position_axes) if fill else ()
    sparse = set(kept) - set(required) if required is not None and not profile else set()
    raw = read_fields(value, kept, topic=topic, stamp=stamp, optional=set(optional) | sparse, nan_axes=fill.nan_position_axes if fill else ())
    absent = sparse - raw["position"].keys() - set(optional) - set(fill.nan_position_axes if fill else ())
    kept = [n for n in kept if n not in absent]
    names = [n for n in names if n not in absent]
    missing = [n for n in kept if n not in raw["position"]]
    audited = {n: {"source": "command" if action else "measured_state"} for n in kept}
    measured = {}
    if copies:
        # Sorted ROS header stamps, never arrival order, log time or an earlier sample.
        idx = bisect_left(state_times, stamp)
        if idx == len(state_times):
            raise ValueError(f"MCAP_FOLLOWING_STATE_UNAVAILABLE: {topic} capture_time_ns={stamp}")
        measured, ref = states[idx]
        audited.update({n: {"source": "state_replacement", "state": ref, "delta_ns": state_times[idx] - stamp}
                        for n in copies})
    if missing:
        idx = nearest_index(state_times, stamp, round(fill.max_state_delta_ms * 1_000_000)) if fill else None
        if idx is None:
            raise ValueError(f"MCAP_WAIST_STATE_UNAVAILABLE: {topic} axes={missing} capture_time_ns={stamp}")
        snapshot, ref = states[idx]
        for n in missing:
            raw["position"][n] = snapshot["position"][n]
            audited[n] = {"source": "state_fallback", "state": ref, "delta_ns": state_times[idx] - stamp}
    audited.update({n: {"source": "constant", "value": 0} for n in zeros})
    header = get_field(value, "header", {})
    raw_stamp = get_field(header, "stamp", {})
    result = {"header": {"stamp": {"sec": get_field(raw_stamp, "sec", 0), "nanosec": get_field(raw_stamp, "nanosec", 0)},
                         "frame_id": get_field(header, "frame_id", "")}, "name": names}
    unavailable = []
    for field in FIELDS:
        values = {**raw[field], **{n: 0.0 for n in zeros}}
        values.update({n: measured[field][n] for n in copies if n in measured[field]})
        # JointState optional arrays must be either complete or empty.
        result[field] = [values[n] for n in names] if len(values) == len(names) else []
        if not result[field]:
            unavailable.append(field)
    return result, {"topic": topic, "action" if action else "message": reference(message, stamp, ordinal),
                    "axes": audited, "unavailable_fields": unavailable}
