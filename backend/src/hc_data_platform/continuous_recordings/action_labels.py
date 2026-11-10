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
