"""Export synchronized HC TJ ROS messages from immutable Episode training rows and videos."""

import io
import json
import math
from collections import Counter
from contextlib import ExitStack, closing
from fractions import Fraction
from itertools import groupby

import av
import numpy as np
import pyarrow.parquet as pq
from mcap.reader import make_reader
from mcap.records import Schema
from mcap.writer import Writer
from mcap_ros2._dynamic import serialize_dynamic
from PIL import Image

from hc_data_platform.continuous_recordings.joint_normalization import JOINTS
from hc_data_platform.continuous_recordings.mcap_selection import ros_type
from hc_data_platform.data_formats.holobrain_depth import DepthProfile, dequantize
from hc_data_platform.data_formats.lerobot_layout import DATA_PATH, VIDEO_PATH

from .models import canonical

RGB = "observation.images.head"
DEPTH = "observation.images.head_depth"
JOINT_FEATURES = {
    "/observation/joint_states": "observation.state",
    "/action/joint_states": "action",
    "/action/right_hand/joint_states": "action.hand.right",
    "/action/left_hand/joint_states": "action.hand.left",
}
IMAGE_TOPICS = {
    RGB: "/observation/cameras/head/color/compressed",
    DEPTH: "/observation/cameras/head/depth/compressed",
}
PARAMETERS = ("/observation/cameras/head/calibration", "/observation/cameras/head/extrinsics")


def matches(cfg):
    from hc_data_platform.continuous_recordings.mcap_selection import selected_topics

    return bool(
        cfg.normalization
        and selected_topics(cfg)
        == set(JOINT_FEATURES) | set(IMAGE_TOPICS.values()) | set(PARAMETERS)
        and cfg.state_topic == "/observation/joint_states"
        and cfg.action_topic == "/action/joint_states"
    )


# Historical JSON channels use the same ROS fields but do not supply a ROS definition.
_HEADER = (
    "\n"
    + "=" * 80
    + "\nMSG: std_msgs/Header\nbuiltin_interfaces/Time stamp\nstring frame_id\n"
    + "=" * 80
    + "\nMSG: builtin_interfaces/Time\nint32 sec\nuint32 nanosec\n"
)
_ROS_DEFINITIONS = {
    "sensor_msgs/msg/JointState": "\n".join(
        (
            "std_msgs/Header header",
            "string[] name",
            "float64[] position",
            "float64[] velocity",
            "float64[] effort",
        )
    )
    + _HEADER,
    "sensor_msgs/msg/CompressedImage": "std_msgs/Header header\nstring format\nuint8[] data"
    + _HEADER,
    "std_msgs/msg/String": "string data\n",
}


def source_contract(path, source_topics=None):
    # Only schema definitions and recorded camera parameters come from the frozen native reference.
    with path.open("rb") as stream:
        reader = make_reader(stream, validate_crcs=True)
        summary = reader.get_summary()
        if source_topics is not None and summary is None:
            return None
        schemas = {}
        parameters = []
        for channel in summary.channels.values():
            topic = channel.topic if source_topics is None else source_topics.get(channel.topic)
            if topic not in (*JOINT_FEATURES, *IMAGE_TOPICS.values(), *PARAMETERS):
                continue
            schema = summary.schemas.get(channel.schema_id)
            if source_topics is not None:
                # A frozen alias must identify one recorded channel unambiguously.
                if schema is None or topic in schemas:
                    return None
                schema = Schema(id=schema.id, data=schema.data, encoding=schema.encoding, name=ros_type(schema.name))
            if schema is None:
                raise ValueError(f"MCAP_ROS_SCHEMA_REQUIRED: {channel.topic}")
            if channel.message_encoding == "json":
                if source_topics is not None and schema.name not in _ROS_DEFINITIONS:
                    return None
                schema = Schema(
                    id=schema.id,
                    name=schema.name,
                    encoding="ros2msg",
                    data=_ROS_DEFINITIONS[schema.name].encode(),
                )
            elif (channel.message_encoding, schema.encoding) != ("cdr", "ros2msg"):
                if source_topics is not None:
                    return None
                raise ValueError(f"MCAP_ROS_SCHEMA_REQUIRED: {channel.topic}")
            schemas[topic] = schema
            if topic in PARAMETERS:
                parameters.append(channel.topic)
        if source_topics is not None and not {"/observation/joint_states", *IMAGE_TOPICS.values(), *PARAMETERS} <= schemas.keys():
            return None
        for topic in JOINT_FEATURES:
            schemas[topic] = schemas["/observation/joint_states"]
        params = {t: [] for t in PARAMETERS}
        for _, channel, msg in reader.iter_messages(topics=parameters):
            topic = channel.topic if source_topics is None else source_topics[channel.topic]
            data = msg.data
            if channel.message_encoding == "json":
                schema = schemas[topic]
                encode = serialize_dynamic(schema.name, schema.data.decode())[schema.name]
                data = encode(json.loads(data))
            params[topic].append((msg.log_time, data))
    return schemas, params


def joint_payload(row, feature, names, header):
    values = row[feature]
    values = values if isinstance(values, list) else [values]
    valid = json.loads(row["hc.axis_validity." + feature])
    if len(names) != len(set(names)) or len(values) != len(names) or len(valid) != len(names):
        raise ValueError(f"MCAP_COMMITTED_JOINT_LAYOUT: {feature}")
    axes = {n: v for n, v, ok in zip(names, values, valid, strict=True) if ok}
    if feature == "observation.state":
        order = JOINTS
    elif feature == "action":
        order = [n for n in names if n in axes]
        if not set(JOINTS[:14]) <= axes.keys():
            raise ValueError("MCAP_COMMITTED_ARM_ACTION_MISSING")
    else:
        order = ["R_ban" if feature.endswith("right") else "L_ban"]
    if not row["hc.valid." + feature] or any(
        n not in axes or not math.isfinite(axes[n]) for n in order
    ):
        raise ValueError(f"MCAP_COMMITTED_JOINT_INVALID: {feature}")
    if feature.startswith("action.hand.") and axes[order[0]] not in (0, 1):
        raise ValueError(f"MCAP_COMMITTED_GRIPPER_INVALID: {feature}")
    return {
        "header": header,
        "name": list(order),
        "position": [axes[n] for n in order],
        "velocity": [],
        "effort": [],
    }


def depth_png_encoder(width, height):
    # MCAP applies Zstandard to the full chunk. Store filtered 16-bit samples
    # in the PNG and compress them once, together with the ROS messages.
    encoder = av.CodecContext.create("png", "w")
    encoder.width, encoder.height = width, height
    encoder.pix_fmt = "gray16be"
    encoder.time_base = Fraction(1, 30)
    encoder.thread_count = 1
    encoder.options = {"compression_level": "0", "pred": "up"}
    encoder.open()
    return encoder


def media_frames(package, wanted, info, *, fast_png=False):
    evidence = package.document("hc/modalities.json")["depth"][DEPTH]["evidence_path"]
    declaration = package.document(evidence + "/profile.json")
    profile = DepthProfile(
        **{k: declaration[k] for k in ("depth_min", "depth_max", "shift", "use_log", "version")}
    )
    if profile.sha256 != declaration["profile_sha256"]:
        raise ValueError("MCAP_DEPTH_PROFILE_MISMATCH")
    depth_mm = dequantize(np.arange(4096, dtype=np.uint16), profile, output_unit="mm")
    if depth_mm.max() <= 65535:
        depth_mm = depth_mm.astype(np.uint16)
    with ExitStack() as stack:
        videos = []
        for feature in (RGB, DEPTH):
            name = VIDEO_PATH.format(video_key=feature, chunk_index=0, file_index=0)
            path = stack.enter_context(package.file(name))
            container = stack.enter_context(av.open(str(path)))
            if fast_png:
                container.streams.video[0].codec_context.thread_count = 1
            videos.append(container.decode(video=0))
        flags_path = stack.enter_context(package.file(evidence + "/validity/flags.parquet"))
        masks = (
            row
            for batch in pq.ParquetFile(flags_path).iter_batches(batch_size=32)
            for row in batch.to_pylist()
        )
        samples = zip(*videos, masks, strict=True)
        png_encoder = None
        current = -1
        for index in wanted:
            if index < current or index < 0:
                raise ValueError("MCAP_COMMITTED_VIDEO_ORDER")
            while current < index:
                rgb, depth, mask = next(samples, (None, None, None))
                current += 1
                if mask is None or mask["frame_index"] != current:
                    raise ValueError("MCAP_COMMITTED_MEDIA_MISSING")
                if any(
                    abs(float(f.pts * f.time_base) - current / info["fps"]) > 1e-6
                    for f in (rgb, depth)
                ):
                    raise ValueError("MCAP_COMMITTED_VIDEO_TIMESTAMP")
            with Image.open(io.BytesIO(mask["flags_png"])) as im:
                valid = (np.asarray(im) & 1) != 0
            mm = depth_mm[depth.to_ndarray(format="gray12le")]
            if valid.shape != mm.shape:
                raise ValueError("MCAP_COMMITTED_DEPTH_INVALID")
            mm[~valid] = 0
            if mm.dtype != np.uint16 and mm.max() > 65535:
                raise ValueError("MCAP_COMMITTED_DEPTH_INVALID")
            jpg = io.BytesIO()
            rgb.to_image().save(jpg, format="JPEG", quality=95, subsampling=0)
            if not fast_png:
                png = io.BytesIO()
                Image.fromarray(mm.astype(np.uint16, copy=False)).save(png, format="PNG", compress_level=1)
                yield jpg.getvalue(), png.getvalue()
                continue
            if png_encoder is None:
                png_encoder = depth_png_encoder(mm.shape[1], mm.shape[0])
            frame = av.VideoFrame.from_ndarray(mm.astype(np.uint16, copy=False), format="gray16le")
            packet, = png_encoder.encode(frame.reformat(format="gray16be"))
            yield jpg.getvalue(), bytes(packet)


def write_episodes(root, members, selected, provenance, file, kept, contract, outcome, checkpoint, *, fast_png=False):
    package = selected.package
    info = package.document("meta/info.json")
    features = info["features"]
    with package.file(DATA_PATH) as path:
        parquet = pq.ParquetFile(path)
        columns = [
            c
            for f in JOINT_FEATURES.values()
            for c in (f, "hc.valid." + f, "hc.axis_validity." + f)
        ]
        for c in ("frame_index", "hc.video_frame_index", "hc.action_source_origins"):
            if c in parquet.schema_arrow.names:
                columns.append(c)
        rows = parquet.read(columns=columns).to_pylist()
    selected_rows = [
        (stamp, frame, rows[frame["parent_frame_index"]]) for stamp, frame in sorted(kept.items())
    ]
    schemas, parameters = contract
    encoders = {}
    for topic in ("/observation/joint_states", IMAGE_TOPICS[RGB]):
        schema = schemas[topic]
        encoders[schema.name] = serialize_dynamic(schema.name, schema.data.decode())[schema.name]
    indices = (row.get("hc.video_frame_index", row["frame_index"]) for _, _, row in selected_rows)
    media = media_frames(package, indices, info, fast_png=fast_png)
    with closing(media):
        for _, grouped in groupby(selected_rows, key=lambda item: item[1]["segment"]):
            group = list(grouped)
            first, frame, _ = group[0]
            end = group[-1][1]["end_ns"]
            target = root / f"episode-{len(members):06d}.mcap"
            span = {**frame, "start_ns": first, "end_ns": end}
            doc = {
                "format": "hc-mcap-selection/3",
                "span": span,
                **outcome,
                "data": package.receipt.data.model_dump(mode="json"),
                "output": provenance["config"]["semantic_output"],
                "parser": provenance["format"],
                "source_sha256": provenance["selected_mcap"]["source_sha256"],
                "selected_sha256": file.object.sha256,
                "annotation_revision": selected.annotation_file.file.revision
                if selected.annotation_file
                else None,
                "topic_policy": "committed_episode_rows_and_media/v1",
            }
            counts = Counter()
            frame_map = []
            with target.open("wb") as out:
                writer = Writer(out, enable_crcs=True, enable_data_crcs=True)
                writer.start(profile="ros2", library="hc-episode-training-mcap/1")
                ids, channels = {}, {}
                for topic in (*JOINT_FEATURES, *IMAGE_TOPICS.values(), *PARAMETERS):
                    schema = schemas[topic]
                    if schema.id not in ids:
                        ids[schema.id] = writer.register_schema(
                            schema.name, schema.encoding, schema.data
                        )
                    channels[topic] = writer.register_channel(topic, "cdr", ids[schema.id])
                for topic in PARAMETERS:
                    before = [v for v in parameters[topic] if v[0] < first]
                    within = [v for v in parameters[topic] if first <= v[0] < end]
                    values = before[-1:] + within
                    if not values:
                        raise ValueError(f"MCAP_CAMERA_PARAMETER_MISSING: {topic}")
                    for stamp, data in values:
                        writer.add_message(channels[topic], stamp, data, stamp)
                        counts[topic] += 1
                for i, (stamp, _, row) in enumerate(group):
                    checkpoint()
                    sec, ns = divmod(stamp, 10**9)
                    header = {"stamp": {"sec": sec, "nanosec": ns}, "frame_id": ""}
                    for topic, feature in JOINT_FEATURES.items():
                        msg = joint_payload(row, feature, features[feature]["names"], header)
                        writer.add_message(
                            channels[topic], stamp, encoders[schemas[topic].name](msg), stamp, i
                        )
                        counts[topic] += 1
                    for feature, body, fmt in zip(
                        (RGB, DEPTH), next(media), ("jpeg", "16UC1; png"), strict=True
                    ):
                        topic = IMAGE_TOPICS[feature]
                        msg = {"header": {**header, "frame_id": "eye"}, "data": body, "format": fmt}
                        writer.add_message(
                            channels[topic], stamp, encoders[schemas[topic].name](msg), stamp, i
                        )
                        counts[topic] += 1
                    record = {
                        "frame_index": i,
                        "parent_frame_index": row["frame_index"],
                        "source_timestamp_ns": stamp,
                        "video_frame_index": row.get("hc.video_frame_index", row["frame_index"]),
                    }
                    if row.get("hc.action_source_origins"):
                        record["action_source"] = json.loads(row["hc.action_source_origins"])
                    frame_map.append(record)
                writer.add_metadata("hc.capture.selection", {"document": canonical(doc).decode()})
                writer.finish()
            target.with_name(target.stem + "-frames.json").write_bytes(canonical(frame_map))
            members.append({**doc, "path": target.name, "message_counts": dict(counts)})
