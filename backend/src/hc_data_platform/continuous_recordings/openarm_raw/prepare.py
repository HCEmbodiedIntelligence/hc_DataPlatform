"""Platform-side disk indexing, source-time association and preview materialization.

The SQLite database is temporary Worker scratch space, never a robot artifact.
Original objects and completion manifests are immutable. Only derived objects
are published, with a versioned receipt used on replay.
"""

import hashlib
import json
import sqlite3
import struct
from itertools import chain, pairwise
from contextlib import closing, suppress
from pathlib import Path

import av
import numpy as np
from mcap.reader import make_reader
from mcap.writer import CompressionType, Writer

from hc_data_platform import recording_fields as public
from hc_data_platform.data_formats.depth_evidence import DepthEvidenceVideo
from hc_data_platform.data_formats.depth_source import profile_hash, require_depth_source
from hc_data_platform.data_formats.rgb_video import RgbVideoWriter

from .alignment import Aligner, target_count, target_time
from .buffers import TimeCache
from .camera_rules import depth_reference
from .config import config_hash, validate
from .models import Sample
from .recorded_actions import apply_recorded_targets
from .primary_timeline import uses_primary_timeline, frame_identity
from .export_timeline import export_rows
from .video import FrameReader

VERSION = "openarm-raw-platform/11"


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def load_index(root, database, *, require_depth_sources=False):
    depth_streams = {}
    database.executescript("""
        CREATE TABLE raw_samples(source TEXT,seq INTEGER,capture_ns INTEGER,body TEXT);
        CREATE INDEX raw_time ON raw_samples(source,capture_ns,seq);
        CREATE TABLE frames(source TEXT,seq INTEGER,body TEXT,PRIMARY KEY(source,seq));
        CREATE TABLE depths(source TEXT,seq INTEGER,log_ns INTEGER,PRIMARY KEY(source,seq));
        CREATE TABLE pointclouds(source TEXT,seq INTEGER,PRIMARY KEY(source,seq));
        CREATE TABLE events(kind TEXT,body TEXT);
        CREATE TABLE action_targets(source TEXT,k INTEGER,body TEXT,PRIMARY KEY(source,k));
        CREATE INDEX action_primary ON action_targets(source,json_extract(body,'$.primary_frame.source_seq'));
    """)
    with (root / "raw.mcap").open("rb") as stream:
        for _, channel, message in make_reader(stream, validate_crcs=True).iter_messages(
            log_time_order=False
        ):
            # Public ROS/CDR topics may coexist with the lossless source envelopes.
            if not channel.topic.startswith("/humanoid/session/"):
                continue
            kind = channel.topic.removeprefix("/humanoid/session/").split("/")[0]
            if channel.message_encoding == "humanoid-rgb" and kind == "rgb":
                # Deferred robot encoding retains the exact source pixels in
                # MCAP. Preview lookup uses its committed MP4 frame bindings.
                continue
            if channel.message_encoding in {"humanoid-depth", "humanoid-pointcloud"}:
                length = struct.unpack("<I", message.data[:4])[0]
                data = json.loads(message.data[4 : 4 + length])
                table = "depths" if kind == "depth" else "pointclouds"
                if table == "depths":
                    if require_depth_sources:
                        meta = data["depth"]
                        pixels = np.frombuffer(
                            message.data[4 + length :], dtype=meta["dtype"]
                        ).reshape(meta["shape"])
                        profile = require_depth_source(meta, pixels)
                        if (
                            profile["registered_to_rgb"] is True
                            and meta["shape"] != data["rgb"]["shape"][:2]
                        ):
                            raise ValueError("depth registered grid differs from RGB")
                        stream = depth_streams.setdefault(
                            data["source_id"], {"shape": list(pixels.shape), "profiles": {}}
                        )
                        if stream["shape"] != list(pixels.shape):
                            raise ValueError("depth source grid changed within recording")
                        stream["profiles"][profile_hash(profile)] = profile
                        if len(stream["profiles"]) > 64:
                            raise ValueError("too many depth source profile versions in recording")
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
                if kind == 'depth_video_frame':
                    from hc_data_platform.data_formats.depth_source import validate_profile
                    from hc_data_platform.data_formats.holobrain_depth import DEFAULT_PROFILE
                    ref, meta = data['depth_video'], data['depth']
                    relative = Path(ref['video_segment_path'])
                    if (relative.is_absolute() or '..' in relative.parts
                            or not (root / relative).resolve().is_relative_to(root.resolve())
                            or not (root / relative).is_file()):
                        raise ValueError('depth video binding outside committed files')
                    if ref['profile'] != DEFAULT_PROFILE.document() or ref['profile_sha256'] != DEFAULT_PROFILE.sha256:
                        raise ValueError('unsupported compact depth quantization')
                    source_profile = validate_profile(meta['source_profile'])
                    depth_stream = depth_streams.setdefault(data['source_id'], {'shape':meta['shape'], 'profiles':{}})
                    if depth_stream['shape'] != meta['shape']:
                        raise ValueError('depth source grid changed within recording')
                    depth_stream['profiles'][profile_hash(source_profile)] = source_profile
                    database.execute('INSERT INTO depths VALUES(?,?,?)',
                                     (data['source_id'],data['source_seq'],message.log_time))
                elif kind == "raw":
                    database.execute(
                        "INSERT INTO raw_samples VALUES(?,?,?,?)",
                        (data["source_id"], data["source_seq"], data["capture_time_ns"], body),
                    )
                elif kind == "action_target":
                    database.execute("INSERT INTO action_targets VALUES(?,?,?)",
                                     (data["source_id"], data["target_frame_index"], body))
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
    return depth_streams


def capture_targets(database, cfg, start, end):
    if not uses_primary_timeline(cfg['alignment']):
        for index in range(target_count(start, end, cfg['dataset']['fps'])):
            yield index, target_time(start, index, cfg['dataset']['fps']), None, None
        return
    primary = cfg['alignment']['primary_camera']
    cursor = database.execute('SELECT body FROM raw_samples WHERE source=? AND capture_ns>=? '
                              'AND capture_ns<? ORDER BY capture_ns,seq', (primary, start, end))
    identities = (frame_identity(primary, json.loads(body)) for (body,) in cursor)
    previous = None
    for index, (current, following) in enumerate(pairwise(chain(identities, [None]))):
        if previous is not None and current['capture_time_ns'] <= previous:
            raise ValueError('primary_camera_duplicate_or_backward_timestamp')
        previous = current['capture_time_ns']
        yield index, previous, current, following


def rows(database, cfg, start, end):
    aligner = Aligner(cfg, None, VERSION)
    # Presence anywhere, including a late command or invalidation, wins over all
    # fallback candidates. An expired real command must remain a QC failure.
    missing_actions = [ident for ident, source in cfg["sources"].items()
                       if source["kind"] == "action" and source.get("named_axes")
                       and cfg["alignment"].get("derive_action_from_next_state", False)
                       and not database.execute("SELECT 1 FROM raw_samples WHERE source=? LIMIT 1",
                                                (ident,)).fetchone()]
    for index, target, primary, following in capture_targets(database, cfg, start, end):
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
        row = aligner.row(index, start, target, deadline=2**63 - 1, target_ns=target)
        if primary:
            row.update(primary_frame=primary, next_primary_frame=following,
                       timeline_mode='primary_camera')
        recorded = {}
        for ident in missing_actions:
            saved = (database.execute("SELECT body FROM action_targets WHERE source=? AND "
                                      "json_extract(body,'$.primary_frame.source_seq')=?",
                                      (ident, primary['source_seq'])).fetchone() if primary else
                     database.execute("SELECT body FROM action_targets WHERE source=? AND k=?",
                                      (ident, index)).fetchone())
            if saved:
                recorded[ident] = json.loads(saved[0])
        if recorded:
            apply_recorded_targets(row, recorded, cfg)
        if cache.evictions:
            row["export_qualified"] = row["valid"] = False
            row["invalid_reasons"].append("platform_association_capacity_exceeded")
        row["association_policy"] = "platform_offline_source_time"
        if row["valid"]:
            for ident, group in row["images"].items():
                for stream in ("rgb", "depth"):
                    if stream in group:
                        image = group[stream]
                        aligner.previous_images[(ident, stream)] = (
                            ident, stream, image["clock_id"], image["clock_epoch"], image["source_seq"])
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


def read_depth_message(path, source_id, source_seq, log_ns, readers=None):
    """A timestamp is not a camera identity: multiple cameras can share it."""
    with Path(path).open("rb") as raw_stream:
        matches = make_reader(raw_stream, validate_crcs=True).iter_messages(
            start_time=log_ns, end_time=log_ns + 1
        )
        for _, channel, message in matches:
            if channel.topic.startswith('/humanoid/session/depth_video_frame/'):
                metadata=json.loads(message.data)
                if (metadata['source_id'],metadata['source_seq']) != (source_id,source_seq):
                    continue
                binding=metadata['depth_video']
                root=Path(path).parent
                relative=Path(binding['video_segment_path'])
                if relative.is_absolute() or not (root/relative).resolve().is_relative_to(root.resolve()):
                    raise ValueError('depth video path escapes recording')
                reader=(readers.setdefault(source_id,FrameReader(root,'gray12le'))
                        if readers is not None else FrameReader(root,'gray12le'))
                try:
                    pixels=reader.read(binding)
                    from hc_data_platform.data_formats.compact_depth import validate_encoded_depth
                    validate_encoded_depth(pixels,binding,metadata['depth']['source_profile'],metadata['depth']['shape'])
                    return pixels,metadata
                finally:
                    if readers is None:reader.close()
            if channel.message_encoding != "humanoid-depth":
                continue
            size = struct.unpack("<I", message.data[:4])[0]
            metadata = json.loads(message.data[4 : 4 + size])
            if (metadata["source_id"], metadata["source_seq"]) != (source_id, source_seq):
                continue
            pixels = np.frombuffer(
                message.data[4 + size :], dtype=metadata["depth"]["dtype"]
            ).reshape(metadata["depth"]["shape"])
            return pixels, metadata
    raise ValueError("committed depth message missing for source identity")


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
    videos, readers, depth_readers = {}, {}, {}
    invalid = 0
    try:
        with closing(sqlite3.connect(root.parent / "worker-index.sqlite3")) as db:
            depth_streams = load_index(root, db, require_depth_sources=True)
            if any(
                camera["id"] not in depth_streams
                for camera in profile["cameras"]
                if cfg["sources"][camera["id"]].get("depth_enabled", True)
            ):
                raise ValueError(
                    "DEPTH_SOURCE_CONTRACT_REQUIRED: no observed depth grid for camera"
                )
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
                for index, row in enumerate(export_rows(rows(db, cfg, start, end), cfg, start, end)):
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
                        if kind == "action":
                            values[topic]["source_origins"] = {
                                ident: value.get("action_origin", "recorded_command")
                                for ident, value in row["action"].items()
                            }
                    for camera in profile["cameras"]:
                        ident = camera["id"]
                        if ident not in videos:
                            videos[ident] = RgbVideoWriter(
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
                        if cfg["sources"][camera["id"]].get("depth_enabled", True) and not depth:
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
                        if not cfg["sources"][camera["id"]].get("depth_enabled", True):
                            continue
                        depth_id = ident + "_depth"
                        if depth_id not in videos:
                            videos[depth_id] = DepthEvidenceVideo(
                                destination / f"videos/{depth_id}.mp4",
                                destination / f"hc/depth/{ident}",
                                fps,
                                depth_streams[ident]["shape"],
                                require_source_profile=True,
                            )
                        depth_array, scale, metadata = None, None, None
                        if depth:
                            depth_array, metadata = read_depth_message(
                                root / "raw.mcap", ident, group["source_seq"], depth[0], depth_readers
                            )
                            scale = metadata["depth"].get("depth_scale")
                        depth_meta = metadata["depth"] if depth else {}
                        depth_target, depth_limit = depth_reference(row, ident, cfg)
                        videos[depth_id].write(
                            depth_array,
                            scale,
                            source_id=ident,
                            source_seq=depth_meta.get("source_seq") if depth else None,
                            source_timestamp_ns=depth_meta.get("capture_time_ns"),
                            target_timestamp_ns=depth_target,
                            missing=not bool(depth),
                            invalid_values=depth_meta.get("source_profile", {}).get(
                                "invalid_values", ()
                            ),
                            source_profile=depth_meta.get("source_profile"),
                            max_time_error_ns=depth_limit,
                            encoded_frame=metadata.get('depth_video') if metadata else None,
                        )
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
                        "timing_warnings": row.get('timing_warnings', []),
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
        for evidence_file in (destination / "hc/depth").rglob("*"):
            if evidence_file.is_file():
                suffix = evidence_file.suffix
                media_type = {".json": "application/json", ".png": "image/png"}.get(
                    suffix, "application/octet-stream"
                )
                roles[evidence_file.relative_to(destination).as_posix()] = (
                    "AUXILIARY",
                    None,
                    media_type,
                )
        for camera in profile["cameras"]:
            ident = camera["id"]
            path = f"videos/{ident}.mp4"
            with av.open(str(destination / path)) as container:
                base = container.streams.video[0].time_base
            for depth in (
                (False, True)
                if cfg["sources"][camera["id"]].get("depth_enabled", True)
                else (False,)
            ):
                camera_id = public.camera_name(ident) + ("_depth" if depth else "")
                relative = f"videos/{ident}" + ("_depth" if depth else "") + ".mp4"
                with av.open(str(destination / relative)) as container:
                    base = container.streams.video[0].time_base
                cameras.append(
                    {
                        "camera_id": camera_id,
                        "topic": public.image_key(ident, depth),
                        "fps": fps,
                        "width": depth_streams[ident]["shape"][1] if depth else camera["width"],
                        "height": depth_streams[ident]["shape"][0] if depth else camera["height"],
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
                    "depth_sources": depth_streams,
                    "capture_context": context,
                    "recorder_action_fallback": manifest.get("action_fallback"),
                    "timeline_mode": cfg['alignment'].get('timeline_mode', 'fixed_grid'),
                    "require_camera_sync": cfg['alignment'].get('require_camera_sync', False),
                    "export_time_policy": ('fixed-rate export of primary-exposure associations; '
                                           'source.timestamp_ns retains actual exposure; gaps invalid'),
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
        for reader in depth_readers.values():
            reader.close()
        for reader in readers.values():
            reader.close()
        for video in videos.values():
            with suppress(Exception):
                video.close()
