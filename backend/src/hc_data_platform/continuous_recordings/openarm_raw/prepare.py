"""Platform-side disk indexing, source-time association and preview materialization.

The SQLite database is temporary Worker scratch space, never a robot artifact.
Original objects and completion manifests are immutable. Only derived objects
are published, with a versioned receipt used on replay.
"""

import hashlib
import json
import sqlite3
import struct
from contextlib import closing, suppress
from pathlib import Path

import av
import numpy as np
from mcap.reader import make_reader
from mcap.writer import CompressionType, Writer

from hc_data_platform import recording_fields as public
from hc_data_platform.publishing.holobrain_depth import DepthVideo

from .alignment import Aligner, target_count, target_time
from .buffers import TimeCache
from .config import config_hash, validate
from .models import Sample
from .video import FrameReader, TrainingVideo

VERSION = "openarm-raw-platform/1"


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def load_index(root, database):
    database.executescript("""
        CREATE TABLE raw_samples(source TEXT,seq INTEGER,capture_ns INTEGER,body TEXT);
        CREATE INDEX raw_time ON raw_samples(source,capture_ns,seq);
        CREATE TABLE frames(source TEXT,seq INTEGER,body TEXT,PRIMARY KEY(source,seq));
        CREATE TABLE depths(source TEXT,seq INTEGER,log_ns INTEGER,PRIMARY KEY(source,seq));
        CREATE TABLE pointclouds(source TEXT,seq INTEGER,PRIMARY KEY(source,seq));
        CREATE TABLE events(kind TEXT,body TEXT);
    """)
    with (root / "raw.mcap").open("rb") as stream:
        for _, channel, message in make_reader(stream, validate_crcs=True).iter_messages(
            log_time_order=False
        ):
            # Public ROS/CDR topics may coexist with the lossless source envelopes.
            if not channel.topic.startswith("/humanoid/session/"):
                continue
            kind = channel.topic.removeprefix("/humanoid/session/").split("/")[0]
            if channel.message_encoding in {"humanoid-depth", "humanoid-pointcloud"}:
                length = struct.unpack("<I", message.data[:4])[0]
                data = json.loads(message.data[4 : 4 + length])
                table = "depths" if kind == "depth" else "pointclouds"
                if table == "depths":
                    database.execute(
                        "INSERT INTO depths VALUES(?,?,?)",
                        (data["source_id"], data["source_seq"], message.log_time),
                    )
                else:
                    database.execute(
                        "INSERT INTO pointclouds VALUES(?,?)",
                        (data["source_id"], data["source_seq"]),
                    )
            else:
                data = json.loads(message.data)
                body = canonical(data).decode()
                if kind == "raw":
                    database.execute(
                        "INSERT INTO raw_samples VALUES(?,?,?,?)",
                        (data["source_id"], data["source_seq"], data["capture_time_ns"], body),
                    )
                elif kind == "video_frame":
                    relative = Path(data["video_segment_path"])
                    if (
                        relative.is_absolute()
                        or ".." in relative.parts
                        or not (root / relative).is_file()
                    ):
                        raise ValueError("video binding is outside committed original files")
                    database.execute(
                        "INSERT INTO frames VALUES(?,?,?)",
                        (data["source_id"], data["source_seq"], body),
                    )
                elif kind == "aligned":
                    raise ValueError("raw recording unexpectedly contains robot-aligned rows")
                else:
                    database.execute("INSERT INTO events VALUES(?,?)", (kind, body))
    database.commit()


def rows(database, cfg, start, end):
    aligner = Aligner(cfg, None, VERSION)
    for index in range(target_count(start, end, cfg["dataset"]["fps"])):
        target = target_time(start, index, cfg["dataset"]["fps"])
        cache = TimeCache(cfg["buffers"])
        aligner.cache = cache
        retention = round(cfg["buffers"]["retention_ms"] * 1e6)
        for ident, source in cfg["sources"].items():
            if source["kind"] == "event":
                continue
            queries = [
                (
                    "SELECT body FROM raw_samples WHERE source=? "
                    "AND capture_ns>=? AND capture_ns<=? ORDER BY capture_ns,seq",
                    (ident, target - retention, target + retention),
                )
            ]
            if "named_axes" in source:
                for axis in source["named_axes"]:
                    for event in (False, True):
                        queries.append(
                            (
                                "SELECT body FROM raw_samples WHERE source=? AND capture_ns<? "
                                "AND EXISTS(SELECT 1 FROM json_each(body,'$.joint_names') "
                                "WHERE value=?) "
                                "AND (json_extract(body,'$.validity_event') IS NOT NULL)=? "
                                "ORDER BY capture_ns DESC,seq DESC LIMIT 1",
                                (ident, target - retention, axis["name"], int(event)),
                            )
                        )
            else:
                queries.append(
                    (
                        "SELECT body FROM raw_samples WHERE source=? AND capture_ns<? "
                        "ORDER BY capture_ns DESC,seq DESC LIMIT 1",
                        (ident, target - retention),
                    )
                )
            for sql, args in queries:
                for (body,) in database.execute(sql, args):
                    data = json.loads(body)
                    cache.add(
                        Sample(
                            ident,
                            data["source_seq"],
                            data["capture_time_ns"],
                            data["receive_time_ns"],
                            data["arrival_time_ns"],
                            data["clock_epoch"],
                            body.encode(),
                        ),
                        target,
                    )
        # Offline processing accepts late delivery, not future action timestamps.
        row = aligner.row(index, start, target, deadline=2**63 - 1)
        if cache.evictions:
            row["export_qualified"] = row["valid"] = False
            row["invalid_reasons"].append("platform_association_capacity_exceeded")
        row["association_policy"] = "platform_offline_source_time"
        yield row


def invalid_ranges(database, end):
    active, ranges = {}, []
    for (body,) in database.execute(
        "SELECT body FROM events WHERE kind='annotation' ORDER BY rowid"
    ):
        data = json.loads(body)
        when = data["target_time_ns"]
        if data["action"] == "recording":
            ranges.append((0, end))
        elif data["action"] == "point":
            ranges.append((when - 16_666_667, when + 16_666_667))
        elif data["action"] == "start":
            active[data["id"]] = when
        elif data["action"] == "end" and data["id"] in active:
            ranges.append((active.pop(data["id"]), when))
    return ranges + [(start, end) for start in active.values()]


def vectors(row, kind, cfg):
    values = {}
    for ident, source in cfg["sources"].items():
        if source["kind"] == kind and source["required"]:
            data = row[kind][ident]["components"]
            for index, axis in enumerate(source["named_axes"]):
                values[axis["name"]] = {key: value[index] for key, value in data.items()}
    return [values[a["source_name"]] for a in public.axes(cfg["capture"]["profile"])]


def prepare(root, destination, command, progress=lambda: None):
    root, destination = Path(root), Path(destination)
    snapshot = json.loads((root / "session.json").read_text())
    manifest = json.loads((root / "manifest.json").read_text())
    cfg = validate(snapshot["config"])
    context = dict(snapshot["metadata"]["capture_context"])
    if (root / "capture-binding.json").exists():
        context.update(json.loads((root / "capture-binding.json").read_text()))
    if (
        snapshot.get("recording_mode") != "raw"
        or manifest.get("state") != "completed"
        or snapshot["session_id"] != manifest["session_id"]
        or config_hash(cfg) != snapshot["config_hash"]
        or (context["robot_id"], context["local_robot_id"], context["collection_task_id"])
        != (command.robot_id, command.device_id, command.collection_task_id)
    ):
        raise ValueError("raw capture identity/configuration mismatch")
    expected = {item["path"] for item in manifest["committed"]} | {"session.json", "manifest.json"}
    if (root / "capture-binding.json").exists():
        expected.add("capture-binding.json")
    if expected != {a.path for a in command.assets}:
        raise ValueError("raw capture commit inventory mismatch")
    if any(not item.get("closed") for item in manifest["committed"]):
        raise ValueError("unclosed original capture file")
    fps = cfg["dataset"]["fps"]
    start, end = manifest["t0_ns"], manifest["end_ns"]
    if fps != 30 or not 0 < end - start <= 24 * 3600 * 10**9:
        raise ValueError("unsupported raw recording time grid/duration")
    profile = cfg["capture"]["profile"]
    names = [a["name"] for a in public.axes(profile)]
    units = [a["unit"] for a in public.axes(profile)]
    destination.mkdir(parents=True)
    (destination / "sensors").mkdir()
    (destination / "source").mkdir()
    topics = ("/observation/state", "/action", "/openarm/capture_validity", "source.timestamp_ns")
    videos, readers = {}, {}
    invalid = 0
    try:
        with closing(sqlite3.connect(root.parent / "worker-index.sqlite3")) as db:
            load_index(root, db)
            ranges = invalid_ranges(db, end)
            with (
                (destination / "sensors/robot.mcap").open("wb") as stream,
                (destination / "source/frames.jsonl").open("w") as trace,
            ):
                writer = Writer(
                    stream,
                    compression=CompressionType.ZSTD,
                    enable_crcs=True,
                    enable_data_crcs=True,
                )
                writer.start(profile=VERSION)
                schema = writer.register_schema("openarm-recording-value/v1", "jsonschema", b"{}")
                channels = {
                    topic: writer.register_channel(topic, "json", schema) for topic in topics
                }
                for index, row in enumerate(rows(db, cfg, start, end)):
                    reasons = list(row["invalid_reasons"])
                    qualified = row["export_qualified"]
                    if any(a <= row["target_time_ns"] < b for a, b in ranges):
                        reasons.append("operator_marked_invalid")
                        qualified = False
                    try:
                        stamp = public.source_timestamp(row, cfg)
                    except (KeyError, ValueError):
                        stamp = None
                        reasons.append("primary_rgb_timestamp_missing")
                        qualified = False
                    values = {"source.timestamp_ns": stamp}
                    for kind, topic in zip(("state", "action"), topics, strict=False):
                        parts = vectors(row, kind, cfg)
                        qualified = qualified and all(p["valid"] for p in parts)
                        values[topic] = {
                            "values": [
                                float(np.float32(p["values"])) if p["valid"] else None
                                for p in parts
                            ],
                            "names": names,
                            "units": units,
                        }
                    for camera in profile["cameras"]:
                        ident = camera["id"]
                        if ident not in videos:
                            videos[ident] = TrainingVideo(
                                destination / f"videos/{ident}.mp4",
                                fps,
                                (camera["height"], camera["width"], 3),
                            )
                            readers[ident] = FrameReader(root)
                        group = row["images"].get(ident)
                        binding = (
                            db.execute(
                                "SELECT body FROM frames WHERE source=? AND seq=?",
                                (ident, group["source_seq"]),
                            ).fetchone()
                            if group
                            else None
                        )
                        depth = (
                            db.execute(
                                "SELECT log_ns FROM depths WHERE source=? AND seq=?",
                                (ident, group["source_seq"]),
                            ).fetchone()
                            if group
                            else None
                        )
                        if not depth:
                            reasons.append(ident + ":depth_not_committed")
                            qualified = False
                        try:
                            if not binding:
                                raise ValueError("frame missing")
                            pixels = readers[ident].read(json.loads(binding[0]))
                        except (ValueError, OSError):
                            pixels = np.zeros(
                                (camera["height"], camera["width"], 3), dtype=np.uint8
                            )
                            reasons.append(ident + ":preview_placeholder")
                            qualified = False
                        videos[ident].write(pixels)
                        depth_id = ident + "_depth"
                        if depth_id not in videos:
                            videos[depth_id] = DepthVideo(
                                destination / f"videos/{depth_id}.mp4",
                                fps,
                                (camera["height"], camera["width"]),
                            )
                        depth_array = np.zeros((camera["height"], camera["width"]), dtype=np.uint16)
                        scale = 0.001
                        if depth:
                            with (root / "raw.mcap").open("rb") as raw_stream:
                                matches = make_reader(raw_stream).iter_messages(
                                    start_time=depth[0], end_time=depth[0] + 1
                                )
                                for _, channel, message in matches:
                                    if channel.message_encoding != "humanoid-depth":
                                        continue
                                    size = struct.unpack("<I", message.data[:4])[0]
                                    metadata = json.loads(message.data[4 : 4 + size])
                                    depth_array = np.frombuffer(
                                        message.data[4 + size :], dtype=metadata["depth"]["dtype"]
                                    ).reshape(metadata["depth"]["shape"])
                                    scale = metadata["depth"]["depth_scale"]
                                    break
                                else:
                                    raise ValueError("committed depth message missing")
                        videos[depth_id].write(depth_array, scale)
                    for ident, cloud in row["pointclouds"].items():
                        if (
                            cloud["required"]
                            and not db.execute(
                                "SELECT 1 FROM pointclouds WHERE source=? AND seq=?",
                                (ident, cloud["source_seq"]),
                            ).fetchone()
                        ):
                            reasons.append(ident + ":pointcloud_not_committed")
                            qualified = False
                    offset = index * 10**9 // fps
                    values[topics[2]] = {
                        "valid": bool(qualified),
                        "source_frame": index,
                        "reasons": sorted(set(reasons)),
                    }
                    invalid += int(not qualified)
                    for topic, value in values.items():
                        writer.add_message(
                            channels[topic],
                            log_time=offset,
                            publish_time=offset,
                            sequence=index,
                            data=canonical(value),
                        )
                    trace.write(
                        canonical(
                            {
                                "source_frame": index,
                                "offset_ns": offset,
                                "row": row,
                                "valid": bool(qualified),
                            }
                        ).decode()
                        + "\n"
                    )
                    if index % 30 == 0:
                        progress()
                writer.finish()
        for video in videos.values():
            video.close()
        videos.clear()
        cameras = []
        roles = {
            "sensors/robot.mcap": ("SENSOR_DATA", None, "application/mcap"),
            "source/frames.jsonl": ("AUXILIARY", None, "application/x-ndjson"),
            "recording-config.json": ("RECORDING_CONFIG", None, "application/json"),
            "capture-provenance.json": ("AUXILIARY", None, "application/json"),
        }
        for camera in profile["cameras"]:
            ident = camera["id"]
            path = f"videos/{ident}.mp4"
            with av.open(str(destination / path)) as container:
                base = container.streams.video[0].time_base
            for depth in (False, True):
                camera_id = public.camera_name(ident) + ("_depth" if depth else "")
                relative = f"videos/{ident}" + ("_depth" if depth else "") + ".mp4"
                with av.open(str(destination / relative)) as container:
                    base = container.streams.video[0].time_base
                cameras.append(
                    {
                        "camera_id": camera_id,
                        "topic": public.image_key(ident, depth),
                        "fps": fps,
                        "width": camera["width"],
                        "height": camera["height"],
                        "codec": "hevc" if depth else "h264",
                        "clock_domain": "recording_grid",
                        "time_base_numerator": base.numerator,
                        "time_base_denominator": base.denominator,
                        "capture_start_offset_ns": "0",
                    }
                )
                roles[relative] = ("RAW_VIDEO", camera_id, "video/mp4")
        config = {
            "schema_version": "recording-config/v1",
            "recorder_version": "openarm-session/v2",
            "primary_clock_domain": "recording_grid",
            "cameras": cameras,
            "sensors": [
                {
                    "topic": t,
                    "clock_domain": "recording_grid",
                    "timestamp_mode": "RECORDING_OFFSET_NS",
                    "required": True,
                }
                for t in topics
            ],
        }
        (destination / "recording-config.json").write_bytes(canonical(config))
        (destination / "capture-provenance.json").write_bytes(
            canonical(
                {
                    "processor_version": VERSION,
                    "capture_context": context,
                    "session_id": snapshot["session_id"],
                    "source_t0_ns": start,
                    "source_end_ns": end,
                    "original_manifest_sha256": hashlib.sha256(
                        (root / "manifest.json").read_bytes()
                    ).hexdigest(),
                    "invalid_frame_count": invalid,
                    "frame_count": target_count(start, end, fps),
                    "preview_policy": (
                        "platform generated; missing frames explicitly invalid; "
                        "original files retained"
                    ),
                }
            )
        )
        return config, roles
    finally:
        for reader in readers.values():
            reader.close()
        for video in videos.values():
            with suppress(Exception):
                video.close()
