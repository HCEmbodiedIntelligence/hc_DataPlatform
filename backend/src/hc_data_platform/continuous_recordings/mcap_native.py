"""Prepare a standalone MCAP for the existing recording/slicing workbench.

Original bytes remain authoritative. Derived CFR media retains elapsed log time;
missing observations are explicit, never compressed across acquisition gaps.
"""

import bisect
import copy
import hashlib
import json
import math
import sqlite3
import statistics
from pathlib import Path

import numpy as np
from mcap.reader import make_reader
from mcap.writer import CompressionType, Writer

from hc_data_platform.data_formats.depth_evidence import DepthEvidenceVideo
from hc_data_platform.data_formats.rgb_video import RgbVideoWriter
from hc_data_platform.episode_store.mcap_images import decode_camera, get_field
from hc_data_platform.episode_store.models import canonical
from hc_data_platform.verification.ports import McapRos2DecoderProbe

from .mcap_contracts import CameraMapping as CameraMapping
from .mcap_contracts import McapProcessingConfig as McapProcessingConfig

VERSION = "native-mcap-recording/2"
TOPICS = ("/observation/state", "/action", "/openarm/capture_validity", "source.timestamp_ns")


def decoded(schema, channel, message, decoder):
    if channel.message_encoding == "json":
        return json.loads(message.data)
    return decoder.probe(
        message_encoding=channel.message_encoding,
        schema_encoding=schema.encoding,
        schema_name=schema.name,
        schema_data=schema.data,
        message_data=message.data,
    )


def inspect(path, progress=lambda: None):
    result, decoder = {}, McapRos2DecoderProbe()
    last_arrival = {}
    with Path(path).open("rb") as stream:
        reader = make_reader(stream, validate_crcs=True)
        summary = reader.get_summary()
        if summary:
            for channel in summary.channels.values():
                schema = summary.schemas.get(channel.schema_id)
                name = schema.name if schema else ""
                result[channel.topic] = {
                    "schema": name,
                    "kind": "joints"
                    if name.endswith("/JointState")
                    else "rgb"
                    if name.endswith(("/Image", "/CompressedImage"))
                    else "other",
                    "encoding": "",
                    "joint_names": [],
                    "message_count": 0,
                    "first_log_time_ns": None,
                    "last_log_time_ns": None,
                }
        for schema, channel, message in reader.iter_messages(log_time_order=False):
            progress()
            name = schema.name if schema else ""
            entry = result.setdefault(
                channel.topic,
                {
                    "schema": name,
                    "kind": "other",
                    "encoding": "",
                    "joint_names": [],
                    "message_count": 0,
                    "first_log_time_ns": None,
                    "last_log_time_ns": None,
                },
            )
            entry["message_count"] += 1
            previous = last_arrival.get(channel.topic)
            for key, condition in (
                ("backwards_timestamps", previous is not None and message.log_time < previous),
                ("duplicate_timestamps", previous == message.log_time),
            ):
                entry[key] = entry.get(key, 0) + int(condition)
            last_arrival[channel.topic] = message.log_time
            entry["first_log_time_ns"] = min(
                entry["first_log_time_ns"]
                if entry["first_log_time_ns"] is not None
                else message.log_time,
                message.log_time,
            )
            entry["last_log_time_ns"] = max(
                entry["last_log_time_ns"]
                if entry["last_log_time_ns"] is not None
                else message.log_time,
                message.log_time,
            )
            if entry["message_count"] > 1 or not name.endswith(
                ("/Image", "/CompressedImage", "/JointState")
            ):
                continue
            value = decoded(schema, channel, message, decoder)
            encoding = (
                get_field(value, "encoding", "")
                or get_field(value, "format", "").split(";", 1)[0].strip()
            )
            entry["encoding"] = encoding
            if name.endswith("/JointState"):
                entry.update(kind="joints", joint_names=list(get_field(value, "name", [])))
            else:
                entry["kind"] = "depth" if encoding in {"16UC1", "mono16", "32FC1"} else "rgb"
    return result


def resolve(config, inventory):
    config = McapProcessingConfig.model_validate(config)
    joints = [
        k for k, v in inventory.items() if v["kind"] == "joints" and v.get("message_count", 1) > 0
    ]
    missing = [
        role
        for role in ("state", "action")
        if not getattr(config, role + "_topic")
        and not (role == "action" and config.derive_action_from_next_state)
    ]
    if missing:
        raise ValueError(
            f"MCAP_MAPPING_REQUIRED: missing {', '.join(missing)} mapping; candidates={joints}"
        )
    state, action = config.state_topic, config.action_topic
    for role, topic in (("state", state), ("action", action)):
        if topic and topic not in joints:
            raise ValueError(
                f"MCAP_MAPPING_INVALID: {role} topic {topic!r} absent, empty or incompatible; "
                f"candidates={joints}"
            )
    rgb = [k for k, v in inventory.items() if v["kind"] == "rgb" and v.get("message_count", 1) > 0]
    primary = config.primary_camera
    if config.collection_mode == "RGB" and config.time_source == "RGB_LOG_TIME":
        if primary and (
            primary not in inventory or inventory[primary].get("message_count", 1) == 0
        ):
            raise ValueError(
                f"MCAP_REQUIRED_RGB_MISSING: required topic={primary}; message_count=0; "
                "文件中未录入必需的 RGB 图像"
            )
        if primary and primary not in rgb:
            raise ValueError(
                f"MCAP_MAPPING_INVALID: primary RGB topic {primary!r} has incompatible type"
            )
        if not rgb:
            raise ValueError(
                "MCAP_REQUIRED_RGB_MISSING: no RGB messages; 文件中未录入必需的 RGB 图像"
            )
        if not primary:
            raise ValueError(f"MCAP_MAPPING_REQUIRED: missing primary camera; candidates={rgb}")
    elif config.collection_mode == "JOINT_ONLY" and (
        primary or config.cameras or config.time_source != "STATE_LOG_TIME"
    ):
        raise ValueError(
            "MCAP_MAPPING_INVALID: JOINT_ONLY requires no cameras and explicit STATE_LOG_TIME"
        )
    for camera in config.cameras:
        found = inventory.get(camera.topic)
        if not found or found.get("message_count", 1) == 0:
            if camera.required:
                code = (
                    "MCAP_REQUIRED_RGB_MISSING"
                    if camera.kind == "rgb"
                    else "MCAP_REQUIRED_DEPTH_MISSING"
                )
                raise ValueError(f"{code}: required topic={camera.topic}; message_count=0")
        elif found["kind"] != camera.kind or found["schema"] != camera.message_type:
            raise ValueError(
                f"MCAP_MAPPING_INVALID: {camera.topic} "
                f"expected {camera.message_type}/{camera.kind}, "
                f"actual {found['schema']}/{found['kind']}"
            )
    state_names = inventory[state]["joint_names"]
    action_names = inventory[action]["joint_names"] if action else state_names
    if (
        not state_names
        or not action_names
        or len(set(state_names)) != len(state_names)
        or len(set(action_names)) != len(action_names)
    ):
        raise ValueError("MCAP_JOINT_LAYOUT_MISMATCH: empty or duplicate joints")
    layouts = {}
    for role, observed in (("state", state_names), ("action", action_names)):
        names, _ = config.layout(role)
        names = names or tuple(observed)
        layouts[role + "_joint_names"] = names
        absent = sorted(set(names) - set(observed))
        if absent:
            raise ValueError(
                f"MCAP_JOINT_LAYOUT_MISMATCH: {role} missing={absent}; actual={observed}"
            )
    if (
        config.collection_mode == "RGB"
        and any(
            v["kind"] == "depth"
            and v.get("message_count", 1)
            and (not config.cameras or k in {c.topic for c in config.cameras})
            for k, v in inventory.items()
        )
        and config.depth_scale_m_per_unit is None
    ):
        raise ValueError("MCAP_DEPTH_UNITS_REQUIRED: configure depth scale from source evidence")
    return config.model_copy(update=layouts)


def depth_contract(encoding, scale):
    return {
        "schema_version": "hc-depth-source/1",
        "encoding": encoding,
        "unit": "m" if scale == 1 else "mm" if scale == 0.001 else "source_unit",
        "scale_m_per_unit": scale,
        "invalid_values": [0],
        "invalid_nonfinite": True,
        "registered_to_rgb": None,
        "registration_evidence": "",
        "evidence": {
            "kind": "explicit_configuration",
            "reference": "robot MCAP processing configuration",
            "version": VERSION,
        },
    }


def prepare(path, destination, config, progress=lambda: None):
    path, destination = Path(path), Path(destination)
    with path.open("rb") as stream:
        if make_reader(stream).get_header().profile == "hc-episode-mcap/1":
            from .mcap_transport import prepare_transport

            return prepare_transport(path, destination, progress)
    inventory = inspect(path, progress)
    cfg = resolve(config, inventory)
    clock = cfg.primary_camera if cfg.time_source == "RGB_LOG_TIME" else cfg.state_topic
    if inventory[clock].get("backwards_timestamps"):
        raise ValueError(
            f"MCAP_TIME_DISCONTINUITY: declared clock {clock} moves backwards; "
            "inspect original channel evidence"
        )
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "sensors").mkdir()
    (destination / "videos").mkdir()
    (destination / "source").mkdir()
    decoder = McapRos2DecoderProbe()
    # Disk-backed pixels keep memory bounded to one frame per selected camera.
    db = sqlite3.connect(destination.parent / (destination.name + "-frames.sqlite3"))
    db.execute(
        "CREATE TABLE frames(topic TEXT, stamp INTEGER, seq INTEGER, "
        "data BLOB, shape TEXT, dtype TEXT)"
    )
    numeric = {cfg.state_topic: [], **({cfg.action_topic: []} if cfg.action_topic else {})}
    cameras, times = {}, {}
    declared_cameras = {c.topic: c for c in cfg.cameras}
    try:
        with path.open("rb") as stream:
            for schema, channel, message in make_reader(stream, validate_crcs=True).iter_messages(
                log_time_order=False
            ):
                progress()
                kind = inventory.get(channel.topic, {}).get("kind", "other")
                if kind in {"rgb", "depth"} and cfg.collection_mode == "RGB":
                    if declared_cameras and channel.topic not in declared_cameras:
                        continue
                    projected = copy.copy(channel)
                    if kind == "depth":
                        projected.metadata = {
                            **channel.metadata,
                            "hc.depth_source": json.dumps(
                                depth_contract(
                                    inventory[channel.topic]["encoding"], cfg.depth_scale_m_per_unit
                                )
                            ),
                        }
                    frame = decode_camera(schema, projected, message, decoder)
                    camera = cameras.setdefault(
                        channel.topic,
                        {
                            "id": declared_cameras[channel.topic].camera_id
                            if channel.topic in declared_cameras
                            else "cam_"
                            + hashlib.sha256(channel.topic.encode()).hexdigest()[:10]
                            + ("_depth" if kind == "depth" else ""),
                            "kind": kind,
                            "shape": frame.pixels.shape,
                            "profile": frame.profile,
                        },
                    )
                    if camera["shape"] != frame.pixels.shape:
                        raise ValueError("MCAP_CAMERA_LAYOUT_CHANGED")
                    db.execute(
                        "INSERT INTO frames VALUES(?,?,?,?,?,?)",
                        (
                            channel.topic,
                            message.log_time,
                            message.sequence,
                            frame.pixels.tobytes(),
                            json.dumps(frame.pixels.shape),
                            frame.pixels.dtype.str,
                        ),
                    )
                    times.setdefault(channel.topic, []).append(message.log_time)
                elif channel.topic in numeric:
                    value = decoded(schema, channel, message, decoder)
                    names = get_field(value, "name")
                    if len(set(names)) != len(names):
                        raise ValueError(
                            f"MCAP_JOINT_LAYOUT_MISMATCH: duplicate joints at {message.log_time}"
                        )
                    values = dict(
                        zip(get_field(value, "name"), get_field(value, "position"), strict=True)
                    )
                    required_names = {
                        name
                        for role in ("state", "action")
                        if (getattr(cfg, role + "_topic") or cfg.state_topic) == channel.topic
                        for name in cfg.layout(role)[0]
                    }
                    absent = required_names - set(values)
                    if absent:
                        raise ValueError(
                            f"MCAP_JOINT_LAYOUT_MISMATCH: {channel.topic} "
                            f"missing={sorted(absent)} at {message.log_time}"
                        )
                    selected = {n: float(values[n]) for n in required_names}
                    if not all(math.isfinite(v) for v in selected.values()):
                        raise ValueError("MCAP_NONFINITE_JOINT")
                    numeric[channel.topic].append((message.log_time, selected))
        db.execute("CREATE INDEX frame_time ON frames(topic,stamp)")
        db.commit()
        for topic in times:
            times[topic] = sorted(set(times[topic]))
        for topic in numeric:
            numeric[topic].sort(key=lambda x: x[0])
            if not numeric[topic]:
                raise ValueError("MCAP_EMPTY_REQUIRED_TOPIC")
        primary = (
            times[clock]
            if cfg.time_source == "RGB_LOG_TIME"
            else [x[0] for x in numeric[cfg.state_topic]]
        )
        start = primary[0]
        # Nanosecond rounding at fractional periods must not manufacture a frame.
        count = round((primary[-1] - start) * cfg.fps / 1e9) + 1
        if count > 24 * 3600 * cfg.fps:
            raise ValueError("MCAP_DURATION_LIMIT: split recordings exceeding 24 hours")
        limit = round(cfg.maximum_time_error_ms * 1e6)

        def nearest(stamps, target):
            i = bisect.bisect_left(stamps, target)
            candidates = [j for j in (i - 1, i) if 0 <= j < len(stamps)]
            j = min(candidates, key=lambda j: (abs(stamps[j] - target), stamps[j]))
            return j if abs(stamps[j] - target) <= limit else None

        writers = {}
        media = {}
        for topic, c in cameras.items():
            target = destination / f"videos/{c['id']}.mp4"
            evidence = "hc/depth/" + c["id"].removesuffix("_depth")
            writers[topic] = (
                DepthEvidenceVideo(
                    target, destination / evidence, cfg.fps, c["shape"], require_source_profile=True
                )
                if c["kind"] == "depth"
                else RgbVideoWriter(target, cfg.fps, c["shape"])
            )
            media[c["id"]] = {
                "kind": c["kind"],
                "video_path": str(target.relative_to(destination)),
                **({"evidence_path": evidence} if c["kind"] == "depth" else {}),
            }
        numeric_times = {k: [x[0] for x in v] for k, v in numeric.items()}
        invalid = 0
        previous_primary = None
        with (
            (destination / "sensors/robot.mcap").open("wb") as output,
            (destination / "source/frames.jsonl").open("wb") as trace,
        ):
            writer = Writer(output, compression=CompressionType.ZSTD, enable_crcs=True)
            writer.start(profile=VERSION)
            schema = writer.register_schema("hc-recording-value/1", "jsonschema", b"{}")
            channels = {t: writer.register_channel(t, "json", schema) for t in TOPICS}
            try:
                for index in range(count):
                    progress()
                    offset = round(index * 10**9 / cfg.fps)
                    target = start + offset
                    reasons, images = [], {}
                    p = nearest(primary, target)
                    stamp = primary[p] if p is not None else target
                    if p is None or stamp == previous_primary:
                        reasons.append(
                            "primary_camera_missing_or_repeated"
                            if cfg.primary_camera
                            else "state_missing_or_repeated"
                        )
                    previous_primary = stamp
                    values = {}
                    for key, topic in (
                        (TOPICS[0], cfg.state_topic),
                        (TOPICS[1], cfg.action_topic or cfg.state_topic),
                    ):
                        names, units = cfg.layout("state" if key == TOPICS[0] else "action")
                        reference = stamp
                        if key == TOPICS[1] and not cfg.action_topic:
                            reference = (
                                primary[p + 1]
                                if p is not None
                                and p + 1 < len(primary)
                                and primary[p + 1] - stamp <= 100_000_000
                                else -1
                            )
                        pos = nearest(numeric_times[topic], reference)
                        if pos is None:
                            reasons.append(key + ":missing")
                        values[key] = {
                            "values": [numeric[topic][pos][1][name] for name in names]
                            if pos is not None
                            else [None] * len(names),
                            "validity": [pos is not None] * len(names),
                            "source_timestamp_ns": numeric[topic][pos][0]
                            if pos is not None
                            else None,
                            "names": list(names),
                            "units": list(units) if units else ["unknown"] * len(names),
                        }
                    for topic, c in cameras.items():
                        j = nearest(times[topic], stamp)
                        row = (
                            db.execute(
                                "SELECT seq,data,shape,dtype FROM frames "
                                "WHERE topic=? AND stamp=? LIMIT 1",
                                (topic, times[topic][j]),
                            ).fetchone()
                            if j is not None
                            else None
                        )
                        if row is None:
                            reasons.append(topic + ":missing")
                        pixels = (
                            np.frombuffer(row[1], dtype=row[3]).reshape(json.loads(row[2]))
                            if row
                            else np.zeros(
                                c["shape"], dtype=np.uint16 if c["kind"] == "depth" else np.uint8
                            )
                        )
                        if c["kind"] == "depth":
                            writers[topic].write(
                                pixels,
                                cfg.depth_scale_m_per_unit if row else None,
                                source_id=topic,
                                source_seq=row[0] if row else None,
                                source_timestamp_ns=times[topic][j] if row else None,
                                target_timestamp_ns=target,
                                missing=row is None,
                                invalid_values=(0,) if row else (),
                                source_profile=c["profile"] if row else None,
                            )
                        else:
                            writers[topic].write(pixels)
                        if row:
                            images.setdefault(c["id"].removesuffix("_depth"), {})[c["kind"]] = {
                                "source_timestamp_ns": times[topic][j]
                            }
                    valid = not reasons
                    invalid += not valid
                    values[TOPICS[2]] = {"valid": valid, "source_frame": index, "reasons": reasons}
                    values[TOPICS[3]] = stamp
                    for topic, value in values.items():
                        writer.add_message(
                            channels[topic], offset, canonical(value), offset, sequence=index
                        )
                    trace.write(
                        canonical(
                            {
                                "source_frame": index,
                                "offset_ns": offset,
                                "valid": valid,
                                "row": {
                                    "images": images,
                                    "target_time_ns": stamp,
                                    "invalid_reasons": reasons,
                                },
                            }
                        )
                        + b"\n"
                    )
                writer.finish()
            finally:
                for video in writers.values():
                    video.close()
        import av

        for c in cameras.values():
            with av.open(str(destination / f"videos/{c['id']}.mp4")) as container:
                stream = container.streams.video[0]
                c["time_base"] = stream.time_base
                c["codec"] = stream.codec_context.name
        recording_config = {
            "schema_version": "recording-config/v2" if not cameras else "recording-config/v1",
            "collection_mode": cfg.collection_mode if cameras else "JOINT_ONLY",
            "time_source": cfg.time_source,
            "recorder_version": VERSION,
            "primary_clock_domain": "recording_grid",
            "cameras": [
                {
                    "camera_id": c["id"],
                    "topic": "observation.images." + c["id"],
                    "fps": cfg.fps,
                    "height": c["shape"][0],
                    "width": c["shape"][1],
                    "codec": c["codec"],
                    "clock_domain": "recording_grid",
                    "time_base_numerator": c["time_base"].numerator,
                    "time_base_denominator": c["time_base"].denominator,
                    "capture_start_offset_ns": "0",
                }
                for c in cameras.values()
            ],
            "sensors": [
                {
                    "topic": t,
                    "clock_domain": "recording_grid",
                    "timestamp_mode": "RECORDING_OFFSET_NS",
                    "required": True,
                }
                for t in TOPICS
            ],
        }
        provenance = {
            "format": VERSION,
            "source_origin_ns": start,
            "duration_ns": round(count * 1e9 / cfg.fps),
            "clock": "mcap_log_time",
            "time_source": cfg.time_source,
            "collection_mode": cfg.collection_mode,
            "config": cfg.model_dump(mode="json"),
            "inventory": inventory,
            "invalid_frames": invalid,
            "frames": count,
            "source_frequency_hz": (
                1e9
                / statistics.median(
                    b - a for a, b in zip(primary, primary[1:], strict=False) if b > a
                )
                if len(set(primary)) > 1
                else None
            ),
            "output_features": {
                **{
                    name: {
                        "dtype": "float64",
                        "shape": [len(cfg.layout(role)[0])],
                        "names": list(cfg.layout(role)[0]),
                        "units": list(cfg.layout(role)[1]),
                    }
                    for role, name in (("state", "observation.state"), ("action", "action"))
                },
                **{
                    "observation.images." + c["id"]: {
                        "dtype": "video",
                        "shape": list(c["shape"]) if c["kind"] == "rgb" else [*c["shape"], 1],
                        "names": ["height", "width", "channels"],
                        "info": {
                            "video.fps": cfg.fps,
                            "video.codec": c["codec"],
                            "video.pix_fmt": "yuv420p" if c["kind"] == "rgb" else "gray12le",
                            "is_depth_map": c["kind"] == "depth",
                        },
                    }
                    for c in cameras.values()
                },
                "source.timestamp_ns": {"dtype": "int64", "shape": [1]},
            },
            "action_origin": "sent" if cfg.action_topic else "next_state",
            "joint_value_semantics": "source position values; no delta conversion",
            "topic_mapping": {
                **{
                    topic: "observation.images." + camera["id"] for topic, camera in cameras.items()
                },
                cfg.state_topic: "/observation/state",
                **({cfg.action_topic: "/action"} if cfg.action_topic else {}),
            },
        }
        for name, body in (
            ("recording-config.json", recording_config),
            ("recording-media.json", {"format": "hc-recording-media/1", "cameras": media}),
            ("mcap-provenance.json", provenance),
        ):
            (destination / name).write_bytes(canonical(body))
        return recording_config, provenance
    finally:
        db.close()
