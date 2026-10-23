"""Project explicit ROS signal mappings into a shared capture output contract.

The original MCAP stays untouched. Numeric precision, source timestamps and
availability are carried into the derived recording alongside the camera data.
"""

from __future__ import annotations

import bisect
import json
import math
from pathlib import Path

import numpy as np
from mcap.reader import make_reader
from mcap.writer import Writer

from hc_data_platform.episode_store.mcap_images import get_field
from hc_data_platform.episode_store.models import canonical
from hc_data_platform.verification.ports import McapRos2DecoderProbe

GENERATED = {
    "observation.source_time",
    "observation.source_age",
    "observation.source_available",
    "observation.source_valid",
    "observation.recording_time",
}


def _number(value, path, *, dtype=None):
    for part in path.split("."):
        if isinstance(value, (list, tuple, np.ndarray)) and part.isdigit():
            if int(part) >= len(value):
                raise ValueError(f"MCAP_NUMERIC_INVALID: {path}")
            value = value[int(part)]
        else:
            value = get_field(value, part)
    if dtype == "bool":
        if type(value) is not bool:
            raise ValueError(f"MCAP_BOOLEAN_INVALID: {path}")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"MCAP_NUMERIC_INVALID: {path}")
    return value


def _cast(values, feature):
    dtype = feature.dtype
    if dtype not in {"bool", "float32", "float64", "int32", "int64", "uint32", "uint64"}:
        raise ValueError(f"MCAP_NUMERIC_DTYPE_UNSUPPORTED: {dtype}")
    if len(feature.shape) != 1 or len(values) != feature.shape[0]:
        raise ValueError("MCAP_NUMERIC_LAYOUT_MISMATCH")
    if dtype == "bool":
        if any(v is not None and type(v) is not bool for v in values):
            raise ValueError("MCAP_BOOLEAN_INVALID")
        return values
    if any(v is None for v in values):
        return values
    if dtype.startswith(("int", "uint")):
        bounds = np.iinfo(dtype)
        if any(int(v) != v or not bounds.min <= v <= bounds.max for v in values):
            raise ValueError("MCAP_NUMERIC_RANGE_INVALID")
    with np.errstate(over="raise", invalid="raise"):
        result = np.asarray(values, dtype=dtype)
    if not np.isfinite(result).all():
        raise ValueError("MCAP_NUMERIC_RANGE_INVALID")
    return result.tolist()


def _definition(feature):
    return {
        key: feature.model_dump(mode="json")[key] for key in ("dtype", "shape", "names", "units")
    }


def prepare_semantic(path, destination, cfg, progress=lambda: None):
    from hc_data_platform.capture_schemes.models import OutputContract
    from hc_data_platform.capture_schemes.schema import native_topic

    from .mcap_native import decoded, prepare

    path, destination = Path(path), Path(destination)
    output = OutputContract.model_validate(cfg.semantic_output)
    # FRAME_TIMESTAMP means the preserved state timeline for this ROS adapter.
    if output.time_source == "FRAME_TIMESTAMP" and cfg.time_source != "STATE_LOG_TIME":
        raise ValueError("MCAP_FRAME_CLOCK_REQUIRES_STATE")
    base = cfg.model_copy(
        update={"semantic_output": None, "numeric_sources": {}, "alignment_policy": "PRESERVE"}
    )
    recording, evidence = prepare(path, destination, base, progress, preserve_source=True)
    features = output.features
    cameras = {"observation.images." + camera.camera_id: camera for camera in cfg.cameras}
    supported = {"observation.state", "action", *cameras, *cfg.numeric_sources, *GENERATED}
    if set(features) - supported:
        raise ValueError(
            "MCAP_SOURCE_MAPPING_REQUIRED: " + ", ".join(sorted(set(features) - supported))
        )
    if set(cfg.numeric_sources) - set(features):
        raise ValueError("MCAP_SOURCE_MAPPING_INVALID: undeclared numeric output")
    logical_topics = {
        "observation.state": cfg.state_topic,
        "action": cfg.action_topic,
        **{key: camera.topic for key, camera in cameras.items()},
        **{key: mapping.topic for key, mapping in cfg.numeric_sources.items()},
    }
    source_times = {name: [] for name in logical_topics}
    values = {name: [] for name in cfg.numeric_sources}
    by_topic = {}
    for name, topic in logical_topics.items():
        by_topic.setdefault(topic, []).append(name)
    decoder = McapRos2DecoderProbe()
    origin = None
    reference_frames = {}
    with path.open("rb") as stream:
        for schema, channel, message in make_reader(stream, validate_crcs=True).iter_messages():
            progress()
            origin = message.log_time if origin is None else min(origin, message.log_time)
            for name in by_topic.get(channel.topic, []):
                source_times[name].append(message.log_time)
                mapping = cfg.numeric_sources.get(name)
                if mapping is None:
                    continue
                if schema is None or schema.name != mapping.message_type:
                    raise ValueError(f"MCAP_NUMERIC_TYPE_MISMATCH: {name}")
                value = decoded(schema, channel, message, decoder)
                header = get_field(value, "header", None)
                frame = get_field(header, "frame_id", "") if header is not None else ""
                if reference_frames.setdefault(name, frame) != frame:
                    raise ValueError(f"MCAP_REFERENCE_FRAME_CHANGED: {name}")
                numbers = [
                    _number(value, field, dtype=features[name].dtype) for field in mapping.fields
                ]
                values[name].append(_cast(numbers, features[name]))
    if origin is None:
        raise ValueError("MCAP_EMPTY_REQUIRED_TOPIC")
    for name, samples in values.items():
        if not samples and features[name].required:
            raise ValueError(f"MCAP_REQUIRED_SIGNAL_MISSING: {name}")

    actual = dict(evidence["output_features"])
    for name in ("observation.state", "action"):
        feature = features.get(name)
        if feature is None:
            continue
        observed = actual[name]
        if (
            list(feature.shape) != observed["shape"]
            or list(feature.names) != observed["names"]
            or list(feature.units) != observed["units"]
        ):
            raise ValueError(f"MCAP_JOINT_LAYOUT_MISMATCH: {name}")
        actual[name] = _definition(feature)
    for name, samples in values.items():
        if samples:
            actual[name] = _definition(features[name])
    for name in set(features) & GENERATED:
        feature = features[name]
        if name == "observation.recording_time":
            if feature.shape != (1,) or feature.names != ("seconds_since_first_log_time",):
                raise ValueError("MCAP_RECORDING_TIME_LAYOUT_INVALID")
        elif (
            not isinstance(feature.names, tuple)
            or len(feature.names) != feature.shape[0]
            or set(feature.names) - set(logical_topics)
        ):
            raise ValueError(f"MCAP_SOURCE_TIME_LAYOUT_INVALID: {name}")
        actual[name] = _definition(feature)
    for name in cameras:
        if name not in actual:
            continue
        item = actual[name]
        height, width, channels = item["shape"]
        item["info"].update(
            {
                "has_audio": False,
                "video.width": width,
                "video.height": height,
                "video.channels": channels,
                "video.is_depth_map": cameras[name].kind == "depth",
            }
        )

    original = destination / "sensors/robot.mcap"
    replacement = destination / "sensors/semantic.mcap"
    present = {name for name, samples in values.items() if samples} | (set(features) & GENERATED)
    with original.open("rb") as source, replacement.open("wb") as target:
        writer = Writer(target, enable_crcs=True, enable_data_crcs=True)
        writer.start(profile=evidence["format"])
        schema_id = writer.register_schema("hc-recording-value/1", "jsonschema", b"{}")
        channels = {}

        def emit(topic, value, message):
            channel_id = channels.setdefault(topic, 0)
            if not channel_id:
                channel_id = channels[topic] = writer.register_channel(topic, "json", schema_id)
            writer.add_message(
                channel_id,
                message.log_time,
                canonical(value),
                message.publish_time,
                message.sequence,
            )

        for _, channel, message in make_reader(source, validate_crcs=True).iter_messages():
            progress()
            value = json.loads(message.data)
            logical = {"/observation/state": "observation.state", "/action": "action"}.get(
                channel.topic
            )
            if logical in features:
                value["values"] = _cast(value["values"], features[logical])
            emit(channel.topic, value, message)
            if logical in features:
                emit(native_topic(logical), value, message)
            if channel.topic != "source.timestamp_ns":
                continue
            stamp = value
            selected = {
                name: bisect.bisect_right(times, stamp) - 1 for name, times in source_times.items()
            }
            for name in present:
                feature = features[name]
                if name in values:
                    pos = selected[name]
                    row = values[name][pos] if pos >= 0 else [None] * feature.shape[0]
                elif name == "observation.recording_time":
                    row = [(stamp - origin) / 1e9]
                else:
                    row = []
                    for key in feature.names:
                        pos = selected[key]
                        source_stamp = source_times[key][pos] if pos >= 0 else None
                        age = (stamp - source_stamp) / 1e9 if source_stamp is not None else -1.0
                        row.append(
                            (source_stamp - origin) / 1e9
                            if name == "observation.source_time" and source_stamp is not None
                            else -1.0
                            if name == "observation.source_time"
                            else age
                            if name == "observation.source_age"
                            else pos >= 0
                            if name == "observation.source_available"
                            else pos >= 0 and age <= (0.25 if key in cameras else 0.2)
                        )
                emit(native_topic(name), _cast(row, feature), message)
            emit("/metadata/source", {"format": "MCAP", "source_timestamp_ns": stamp}, message)
        writer.finish()
    replacement.replace(original)
    canonical_sensors = {
        native_topic(name): features[name].required
        for name in present | ({"observation.state", "action"} & set(features))
    }
    canonical_sensors["/metadata/source"] = True
    recording["sensors"].extend(
        {
            "topic": name,
            "clock_domain": "recording_grid",
            "timestamp_mode": "RECORDING_OFFSET_NS",
            "required": required,
        }
        for name, required in sorted(canonical_sensors.items())
    )
    for camera in recording["cameras"]:
        camera["topic"] = native_topic(camera["topic"])
    evidence.update(
        config={
            **cfg.model_dump(mode="json"),
            "fps": evidence["config"]["fps"],
            "alignment_policy": "PRESERVE",
        },
        output_features=actual,
        semantic_projection="explicit-ros-features/v1",
        source_reference_frames=reference_frames,
    )
    for name, document in (
        ("recording-config.json", recording),
        ("mcap-provenance.json", evidence),
    ):
        (destination / name).write_bytes(canonical(document))
    return recording, evidence
