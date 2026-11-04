"""Lossless selection into the versioned upload format; originals are never modified."""

import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from mcap.reader import make_reader
from mcap.writer import Writer

from hc_data_platform.episode_store.mcap_images import get_field
from hc_data_platform.episode_store.models import canonical
from hc_data_platform.verification.ports import McapRos2DecoderProbe

ALIASES = {
    "/observation/joint_states": ("io_teleop/joint_states", "/io_teleop/joint_states"),
    "/action/joint_states": ("io_teleop/joint_cmd", "/io_teleop/joint_cmd"),
    "/action/right_hand/joint_states": ("/io_teleop/joint_cmd_finger_right",),
    "/action/left_hand/joint_states": ("/io_teleop/joint_cmd_finger_left",),
    **{
        "/observation/cameras/head/" + target: ("/io_teleop/camera_head/" + source,)
        for target, source in (
            ("color/compressed", "color"), ("depth/compressed", "depth"),
            ("calibration", "calibration"), ("extrinsics", "extrinsics"),
        )
    },
}


def ros_type(name):
    parts = name.split("/")
    return "/".join((parts[0], "msg", parts[1])) if len(parts) == 2 else name


def selected_topics(cfg):
    return {t for t in (cfg.state_topic, cfg.action_topic, cfg.primary_camera) if t} | {
        c.topic for c in cfg.cameras
    } | {m.topic for m in cfg.numeric_sources.values()} | set(cfg.metadata_sources.values())


def joint_values(value, selected, required, *, topic, stamp, nan_as_missing=(), allow_empty=False):
    names = list(get_field(value, "name", []))
    positions = list(get_field(value, "position", []))
    empty = allow_empty and not positions
    if (not names and not empty) or (not empty and len(names) != len(positions)) or len(set(names)) != len(names):
        raise ValueError(f"MCAP_JOINT_LAYOUT_MISMATCH: {topic} duplicate names or name/position length at {stamp}")
    indices = {name: i for i, name in enumerate(names)}
    absent = set(required) if empty else set(required) - indices.keys()
    if absent:
        raise ValueError(f"MCAP_JOINT_LAYOUT_MISMATCH: {topic} missing={sorted(absent)} at {stamp}")
    ignored = {
        n for n in nan_as_missing if positions and n in indices and math.isnan(float(positions[indices[n]]))
    }
    for field in ("position", "velocity", "effort"):
        values = list(get_field(value, field, []))
        if values and len(values) != len(names):
            raise ValueError(f"MCAP_JOINT_LAYOUT_MISMATCH: {topic} name/{field} length at {stamp}")
        if values and any(
            isinstance(values[indices[n]], bool) or not math.isfinite(float(values[indices[n]]))
            for n in selected if n in indices and not (field == "position" and n in ignored)
        ):
            raise ValueError(f"MCAP_NONFINITE_JOINT: {topic}.{field} at {stamp}")
    return {n: float(positions[indices[n]]) for n in selected if positions and n in indices and n not in ignored}


def select_mcap(path, target, cfg, progress=lambda: None):
    """Validate the full container, decode only selected fields, preserve all three clocks."""
    from .mcap_native import decoded, inspect
    inventory = inspect(path, progress, selected_topics=set())
    sources = {}
    for topic in selected_topics(cfg):
        aliases = cfg.topic_aliases.get(topic, ALIASES.get(topic, ()))
        found = [t for t in (topic, *aliases) if inventory.get(t, {}).get("message_count", 0)]
        if len(set(found)) > 1:
            raise ValueError(f"MCAP_AMBIGUOUS_SOURCE: {topic}: {found}")
        if found:
            if found[0] in sources:
                raise ValueError(f"MCAP_AMBIGUOUS_SOURCE: {found[0]}")
            sources[found[0]] = topic
    counts, layouts = Counter(), {}
    decoder = McapRos2DecoderProbe()
    encoders, schemas, channels = {}, {}, {}
    states, state_times, audit, dropped = [], [], [], []
    synthesize_action = False
    if cfg.normalization:
        from .joint_normalization import JOINTS, capture_time, normalize, reference

        absent = selected_topics(cfg) - set(sources.values())
        if (
            cfg.action_topic in absent
            and cfg.postprocessing_rules
            and cfg.postprocessing_rules.missing_action_topic == "copy_state"
            and all(signal.topic != cfg.action_topic for signal in cfg.numeric_sources.values())
        ):
            absent.remove(cfg.action_topic)
            synthesize_action = True
        if absent:
            raise ValueError(f"MCAP_SELECTED_SOURCE_MISSING: {sorted(absent)}")
        with Path(path).open("rb") as source:
            for ordinal, (schema, channel, message) in enumerate(
                make_reader(source, validate_crcs=True).iter_messages(log_time_order=False)
            ):
                if sources.get(channel.topic) != cfg.state_topic:
                    continue
                value = decoded(schema, channel, message, decoder)
                stamp = capture_time(value, message)
                measured = joint_values(value, JOINTS, JOINTS, topic=channel.topic, stamp=stamp)
                states.append((measured, reference(message, stamp, ordinal)))
        states.sort(key=lambda item: (item[1]["capture_time_ns"], item[1]["message_index"]))
        state_times = [item[1]["capture_time_ns"] for item in states]
        if len(set(state_times)) != len(state_times):
            raise ValueError("MCAP_TIME_DISCONTINUITY: duplicate state acquisition time")
    with Path(path).open("rb") as source, Path(target).open("wb") as output:
        reader = make_reader(source, validate_crcs=True)
        writer = Writer(output, enable_crcs=True, enable_data_crcs=True)
        generated_channels = {}
        writer.start(profile="ros2", library="hc-selected-mcap/2" if cfg.normalization else "hc-selected-mcap/1")
        for ordinal, (schema, channel, message) in enumerate(reader.iter_messages(log_time_order=False)):
            progress()
            topic = sources.get(channel.topic)
            if topic is None:
                continue
            if schema is None:
                raise ValueError(f"MCAP_SCHEMA_REQUIRED: {topic}")
            kind = ros_type(schema.name)
            data = message.data
            roles = [role for role in ("state", "action") if getattr(cfg, role + "_topic") == topic]
            numeric = {name: mapping for name, mapping in cfg.numeric_sources.items() if mapping.topic == topic and ros_type(mapping.message_type) == "sensor_msgs/msg/JointState"}
            if roles or numeric:
                if kind != "sensor_msgs/msg/JointState":
                    raise ValueError(f"MCAP_MAPPING_INVALID: {topic} requires JointState")
                value = decoded(schema, channel, message, decoder)
                names = list(dict.fromkeys(n for role in roles for n in cfg.layout(role)[0]))
                required = {n for role in roles for n in cfg.required_axes(role)}
                for name, mapping in numeric.items():
                    for field in mapping.fields:
                        parts = field.split(".")
                        if len(parts) != 2 or parts[0] not in {"position", "velocity", "effort"} or parts[1].isdigit():
                            raise ValueError("MCAP_NAMED_JOINT_REQUIRED: use position.<joint_name> for selected JointState fields")
                        if parts[1] not in names:
                            names.append(parts[1])
                        required.add(parts[1])
                if not cfg.normalization:
                    selected = joint_values(value, names, required, topic=topic, stamp=message.log_time)
                elif numeric:
                    mapped = [field.split(".")[1] for mapping in numeric.values() for field in mapping.fields]
                    joint_values(value, mapped, mapped, topic=topic, stamp=message.log_time)
                if cfg.normalization:
                    try:
                        projected, evidence = normalize(
                            value, names, topic=topic, message=message, ordinal=ordinal,
                            rules=cfg.postprocessing_rules if "action" in roles else None,
                            states=states, state_times=state_times, action="action" in roles or topic.startswith("/action/"),
                        )
                    except ValueError as exc:
                        if not str(exc).startswith("MCAP_WAIST_STATE_UNAVAILABLE:"):
                            raise
                        dropped.append({"topic": topic, "message_index": ordinal,
                                        "log_time_ns": message.log_time, "reason": str(exc)})
                        continue
                    if "action" in roles or numeric:
                        audit.append(evidence)
                    selected = dict(zip(projected["name"], projected["position"], strict=True))
                else:
                    order = [i for n in names for i, old in enumerate(get_field(value, "name")) if n == old]
                    header = get_field(value, "header", {})
                    stamp = get_field(header, "stamp", {})
                    projected = {"header": {
                        "stamp": {"sec": get_field(stamp, "sec", 0), "nanosec": get_field(stamp, "nanosec", 0)},
                        "frame_id": get_field(header, "frame_id", ""),
                    }}
                    for field in ("name", "position", "velocity", "effort"):
                        values = list(get_field(value, field, []))
                        replacement = [values[i] for i in order] if values else []
                        projected[field] = replacement
                if channel.message_encoding == "json":
                    data = canonical(projected)
                else:
                    from mcap_ros2._dynamic import serialize_dynamic
                    if schema.id not in encoders:
                        encoders[schema.id] = serialize_dynamic(kind, schema.data.decode())[kind]
                    data = encoders[schema.id](projected)
                stats = layouts.setdefault(topic, {"messages": 0, "valid_axes": Counter(), "layouts": Counter()})
                stats["messages"] += 1
                stats["valid_axes"].update(selected.keys())
                stats["layouts"][len(selected)] += 1
            if schema.id not in schemas:
                schemas[schema.id] = writer.register_schema(kind, schema.encoding, schema.data)
            if channel.id not in channels:
                channels[channel.id] = writer.register_channel(topic, channel.message_encoding, schemas[schema.id])
            writer.add_message(channels[channel.id], message.log_time, data, message.publish_time, message.sequence)
            counts[topic] += 1
            if synthesize_action and topic == cfg.state_topic:
                if channel.id not in generated_channels:
                    generated_channels[channel.id] = writer.register_channel(cfg.action_topic, channel.message_encoding, schemas[schema.id])
                action = {**projected, "velocity": [], "effort": []}
                payload = canonical(action) if channel.message_encoding == "json" else encoders[schema.id](action)
                writer.add_message(generated_channels[channel.id], message.log_time, payload, message.publish_time, message.sequence)
                counts[cfg.action_topic] += 1
                state_ref = evidence["message"]
                audit.append({
                    "topic": cfg.action_topic, "action": state_ref, "generated_from_state": True,
                    "axes": {name: {"source": "state_fallback", "state": state_ref, "delta_ns": 0}
                             for name in action["name"]}, "unavailable_fields": ["velocity", "effort"],
                })
        if synthesize_action:
            layouts[cfg.action_topic] = layouts[cfg.state_topic]
        # Source metadata/attachments are not an implicit export whitelist.
        writer.add_metadata("hc.capture.format", {"parser_version": "native-mcap-recording/4" if cfg.normalization else "native-mcap-recording/3", "output": json.dumps(cfg.semantic_output)})
        writer.finish()
    def sha(file):
        h = hashlib.sha256()
        with Path(file).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()
    normalization = None
    if cfg.normalization:
        audit_path = Path(target).with_suffix(".audit.json")
        fallback_counts = Counter(
            name for row in audit for name, detail in row["axes"].items()
            if detail["source"] == "state_fallback"
        )
        normalization = {
            "policy": cfg.normalization.model_dump(mode="json"),
            **({"postprocessing_rules": cfg.postprocessing_rules.model_dump(mode="json"),
                "generated_action_messages": counts[cfg.action_topic] if synthesize_action else 0}
               if cfg.postprocessing_rules else {}),
            "fallback_by_axis": dict(fallback_counts),
            "fallback_messages": sum(any(v["source"] == "state_fallback" for v in row["axes"].values()) for row in audit),
            "dropped_action_messages": len(dropped),
            "unavailable_fields": dict(Counter(f for row in audit for f in row["unavailable_fields"])),
        }
        audit_path.write_bytes(canonical({**normalization, "messages": audit, "dropped": dropped}))
        normalization["audit"] = {"path": "source/normalization.json", "sha256": sha(audit_path)}
    return {
        "format": "hc-selected-mcap/2" if cfg.normalization else "hc-selected-mcap/1", "path": "source/selected.mcap",
        "sha256": sha(target), "size_bytes": Path(target).stat().st_size,
        "source_sha256": sha(path), "source_size_bytes": Path(path).stat().st_size,
        "source_retained": True, "requires_original_for_unselected_fields": True,
        "source_topics": sources, "message_counts": dict(counts), "joint_evidence": layouts,
        **({"normalization": normalization} if normalization else {}),
    }
