"""Project missing-action training labels inside the frozen episode window."""
from dataclasses import replace
from itertools import groupby
import json

from hc_data_platform.episode_store.models import canonical


def window_config(snapshot):
    cfg = (snapshot or {}).get("effective_adapter", {}).get("configuration", {})
    rules = cfg.get("postprocessing_rules") or {}
    if any(r.get("kind") == "fill_action_from_next_frame" and r.get("enabled")
           for r in rules.get("rules", ())):
        return cfg
    return None


def window_messages(messages, start, end, cfg):
    if not cfg:
        yield from messages
        return
    # One group of channels per frame. The last frame inside a user cut can
    # differ from the last frame of the recording; never use a state outside it.
    previous = []
    for stamp, group in groupby(messages, key=lambda item: item[2].log_time):
        if stamp < start:
            continue
        if stamp >= end:
            break
        yield from previous
        previous = list(group)
    yield from terminal_messages(previous, cfg)


def terminal_messages(messages, cfg):
    from hc_data_platform.capture_schemes.schema import native_topic
    from hc_data_platform.capture_schemes.models import OutputContract
    from .mcap_semantic import _cast

    data = {c.topic: m.data for _, c, m in messages}
    if "/action" not in data:
        yield from messages
        return
    action = json.loads(data["/action"])
    if action.get("action_origin") not in {"next_frame_state", "unfilled_state"}:
        yield from messages
        return
    state = json.loads(data["/observation/state"])
    meta = json.loads(data["/metadata/source"])
    stamp = json.loads(data["source.timestamp_ns"])
    flag = json.loads(data["/openarm/capture_validity"])
    by_name = dict(zip(state["names"], zip(state["values"], state["validity"], strict=True), strict=True))
    values, mask = map(list, zip(*(by_name.get(n, (0.0, False)) for n in action["names"]), strict=True))
    output = OutputContract.model_validate(cfg["semantic_output"])
    spec = output.features["action"]
    available = state["source_timestamp_ns"] is not None and all(
        valid for n, valid in zip(action["names"], mask, strict=True)
        if n in (spec.required_axes if spec.required_axes is not None else action["names"]))
    time = state["source_timestamp_ns"] if available else None
    if not available:
        values, mask = [0.0] * len(mask), [False] * len(mask)
    action.update(values=_cast(values, spec), validity=mask, action_origin="terminal_state" if available else "unfilled_state",
                  source_frame=flag["source_frame"], state_reference_timestamp_ns=stamp,
                  source_timestamp_ns=time, source_topic=cfg["state_topic"])
    meta["action_fill"] = {k: action[k] for k in ("action_origin", "source_frame", "state_reference_timestamp_ns",
                                                   "source_timestamp_ns", "source_topic", "nearest_source_timestamp_ns")}
    flag["reasons"] = [r for r in flag["reasons"] if r != "/action:missing"]
    if not available:
        flag["reasons"].append("/action:missing")
    flag["valid"] = not flag["reasons"]
    changed = {"/action": action, native_topic("action"): action, "/metadata/source": meta, "/openarm/capture_validity": flag}
    if spec.validity_feature:
        changed[native_topic(spec.validity_feature)] = mask
    metrics = {"observation.source_time": (time - meta["recording_origin_ns"]) / 1e9 if available else -1.0,
               "observation.source_age": (stamp - time) / 1e9 if available else -1.0,
               "observation.source_available": available, "observation.source_valid": available}
    for name, metric in metrics.items():
        feature = output.features.get(name)
        if feature and "action" in feature.names:
            topic = native_topic(name)
            row = json.loads(data[topic])
            row[feature.names.index("action")] = metric
            changed[topic] = _cast(row, feature)
    for schema, channel, message in messages:
        if channel.topic in changed:
            message = replace(message, data=canonical(changed[channel.topic]))
        yield schema, channel, message


ALIGNED_ACTION_VERSION = "continuous-episode-package/7"


def left_action_axes(snapshot):
    """Only axes explicitly selected by the frozen action-from-state rule."""
    from .joint_normalization import JOINTS

    config = (snapshot or {}).get("effective_adapter", {}).get("configuration", {})
    rules = (config.get("postprocessing_rules") or {}).get("rules", ())
    axes = next((rule["axes"] for rule in rules if rule["kind"] == "action_from_state"
                 and rule.get("enabled", True)), ())
    return tuple(axis for axis in JOINTS[7:14] if axis in axes)


def aligned_left_action(snapshot, topics):
    """Bind names once, then copy from the observation selected for each output row."""
    import numpy as np
    from hc_data_platform.data_formats.feature_names import feature_names

    logical = {target: name for name, target in feature_names(sorted(topics)).items()}
    state_key, action_key = logical["observation.state"], logical["action"]
    features = snapshot["output"]["features"]
    state_feature, action_feature = features["observation.state"], features["action"]
    state_names, action_names = state_feature["names"], action_feature["names"]
    axes = [(axis, state_names.index(axis), action_names.index(axis)) for axis in left_action_axes(snapshot)]
    mixed_precision = state_feature["dtype"] != action_feature["dtype"]

    def apply(row):
        observed, command = row.modalities[state_key], row.modalities[action_key]
        if not observed.valid or not command.valid:
            return row
        state, action = observed.value, command.value
        positions = list(action["values"])
        valid = list(action["validity"])
        origins = dict(action.get("source_origins", {}))
        for name, si, ai in axes:
            value = state["values"][si]
            valid[ai] = valid[ai] and state["validity"][si]
            if mixed_precision and value is not None:
                value = np.asarray(value, dtype=state_feature["dtype"]).item()
                if np.asarray(value, dtype=action_feature["dtype"]).item() != value:
                    raise ValueError(f"aligned state cannot be represented exactly in action: {name}")
            positions[ai] = value
            origins[name] = {"source": "aligned_observation_state", "frame": row.step_index}
        updated = {**action, "values": positions, "validity": valid, "source_origins": origins}
        return row.model_copy(update={"modalities": {**row.modalities,
            action_key: command.model_copy(update={"value": updated})}})

    return apply
