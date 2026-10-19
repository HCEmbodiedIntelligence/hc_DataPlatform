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
from pathlib import Path

import numpy as np
from mcap.reader import make_reader
from mcap.writer import CompressionType, Writer
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hc_data_platform.data_formats.depth_evidence import DepthEvidenceVideo
from hc_data_platform.data_formats.rgb_video import RgbVideoWriter
from hc_data_platform.episode_store.mcap_images import decode_camera, get_field
from hc_data_platform.episode_store.models import canonical
from hc_data_platform.verification.ports import McapRos2DecoderProbe

VERSION = "native-mcap-recording/1"
TOPICS = ("/observation/state", "/action", "/openarm/capture_validity", "source.timestamp_ns")


class McapProcessingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # Empty topic selections are resolved only when schema/name discovery is unambiguous.
    state_topic: str | None = None
    action_topic: str | None = None
    primary_camera: str | None = None
    joint_names: tuple[str, ...] = ()
    joint_units: tuple[str, ...] = ()
    depth_scale_m_per_unit: float | None = Field(default=None, gt=0, le=1)
    derive_action_from_next_state: bool = False
    maximum_time_error_ms: float = Field(default=50, gt=0, le=1000)
    fps: int = Field(default=30, ge=30, le=30)

    @model_validator(mode="after")
    def axes(self):
        if self.joint_units and len(self.joint_units) != len(self.joint_names):
            raise ValueError("joint names/units must have equal length")
        if len(set(self.joint_names)) != len(self.joint_names):
            raise ValueError("joint names must be unique")
        return self


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
    with Path(path).open("rb") as stream:
        for schema, channel, message in make_reader(stream, validate_crcs=True).iter_messages(
            log_time_order=False
        ):
            progress()
            if channel.topic in result:
                continue
            if schema is None:
                continue
            if not schema.name.endswith(("/Image", "/CompressedImage", "/JointState")):
                result[channel.topic] = {
                    "schema": schema.name,
                    "kind": "other",
                    "encoding": "",
                    "joint_names": [],
                }
                continue
            value = decoded(schema, channel, message, decoder)
            kind = "other"
            encoding = (
                get_field(value, "encoding", "")
                or get_field(value, "format", "").split(";", 1)[0].strip()
            )
            if schema.name.endswith(("/Image", "/CompressedImage")):
                kind = "depth" if encoding in {"16UC1", "mono16", "32FC1"} else "rgb"
            elif schema.name.endswith("/JointState"):
                kind = "joints"
            result[channel.topic] = {
                "schema": schema.name,
                "kind": kind,
                "encoding": encoding,
                "joint_names": list(get_field(value, "name", [])) if kind == "joints" else [],
            }
    return result


def resolve(config, inventory):
    config = McapProcessingConfig.model_validate(config)
    joints = [k for k, v in inventory.items() if v["kind"] == "joints"]

    def unique(selected, candidates, role):
        if selected:
            if selected not in candidates:
                raise ValueError(
                    f"MCAP_MAPPING_INVALID: {role} topic {selected!r} absent or incompatible"
                )
            return selected
        if len(candidates) != 1:
            raise ValueError(f"MCAP_MAPPING_REQUIRED: select {role} from {candidates}")
        return candidates[0]

    state = unique(
        config.state_topic,
        joints if config.state_topic else [t for t in joints if "state" in t.lower()],
        "state",
    )
    action_candidates = [
        t
        for t in joints
        if any(x in t.lower() for x in ("cmd", "command", "action")) and "finger" not in t.lower()
    ]
    action = (
        None
        if not action_candidates and config.derive_action_from_next_state
        else unique(
            config.action_topic, joints if config.action_topic else action_candidates, "action"
        )
    )
    rgb = [k for k, v in inventory.items() if v["kind"] == "rgb"]
    primary = (
        unique(config.primary_camera, rgb, "primary camera")
        if rgb or config.primary_camera
        else None
    )
    names = config.joint_names or tuple(inventory[state]["joint_names"])
    if not names or not set(names) <= set(inventory[state]["joint_names"]):
        raise ValueError("MCAP_MAPPING_INVALID: state joint names do not match robot configuration")
    if action and not set(names) <= set(inventory[action]["joint_names"]):
        # Do not quietly manufacture feedback or omit configured axes.
        if config.joint_names:
            raise ValueError("MCAP_MAPPING_INVALID: action omits configured joints")
        names = tuple(n for n in names if n in inventory[action]["joint_names"])
    if (
        any(v["kind"] == "depth" for v in inventory.values())
        and config.depth_scale_m_per_unit is None
    ):
        raise ValueError(
            "MCAP_DEPTH_UNITS_REQUIRED: configure the robot's depth scale before processing"
        )
    return config.model_copy(
        update={
            "state_topic": state,
            "action_topic": action,
            "primary_camera": primary,
            "joint_names": names,
        }
    )


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
    try:
        with path.open("rb") as stream:
            for schema, channel, message in make_reader(stream, validate_crcs=True).iter_messages(
                log_time_order=False
            ):
                progress()
                kind = inventory.get(channel.topic, {}).get("kind", "other")
                if kind in {"rgb", "depth"}:
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
                            "id": "cam_"
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
                    values = dict(
                        zip(get_field(value, "name"), get_field(value, "position"), strict=True)
                    )
                    selected = [float(values[n]) for n in cfg.joint_names]
                    if not all(math.isfinite(v) for v in selected):
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
            times[cfg.primary_camera]
            if cfg.primary_camera
            else [x[0] for x in numeric[cfg.state_topic]]
        )
        start = primary[0] // 1000 * 1000
        count = math.ceil((primary[-1] - start) * 30 / 1e9) + 1
        if count > 24 * 3600 * 30:
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
                    target, destination / evidence, 30, c["shape"], require_source_profile=True
                )
                if c["kind"] == "depth"
                else RgbVideoWriter(target, 30, c["shape"])
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
                    offset = index * 10**9 // 30
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
                            "values": numeric[topic][pos][1]
                            if pos is not None
                            else [None] * len(cfg.joint_names),
                            "names": list(cfg.joint_names),
                            "units": list(cfg.joint_units)
                            if cfg.joint_units
                            else ["unknown"] * len(cfg.joint_names),
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
            "schema_version": "recording-config/v1",
            "recorder_version": VERSION,
            "primary_clock_domain": "recording_grid",
            "cameras": [
                {
                    "camera_id": c["id"],
                    "topic": "observation.images." + c["id"],
                    "fps": 30,
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
            "duration_ns": math.ceil(count * 1e9 / 30 / 1000) * 1000,
            "clock": "mcap_log_time",
            "config": cfg.model_dump(mode="json"),
            "inventory": inventory,
            "invalid_frames": invalid,
            "frames": count,
            "action_origin": "sent" if cfg.action_topic else "next_state",
            "joint_value_semantics": "source position values; no delta conversion",
            "topic_mapping": {
                **{
                    topic: "observation.images." + camera["id"]
                    for topic, camera in cameras.items()
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
