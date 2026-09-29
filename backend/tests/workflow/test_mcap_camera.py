from __future__ import annotations

import io
import json
from fractions import Fraction

import av
import numpy as np
import pytest
from mcap.records import Channel, Message, Schema
from mcap_ros2._dynamic import serialize_dynamic
from PIL import Image

from hc_data_platform.verification import McapRos2DecoderProbe
from hc_data_platform.workflow.mcap_camera import McapCameraDecoder
from hc_data_platform.workflow.mcap_projection import build_projection, read_projection

MESSAGE_TYPE = "sensor_msgs/msg/CompressedImage"
DEFINITION = """std_msgs/Header header
string format
uint8[] data
================================================================================
MSG: std_msgs/Header
builtin_interfaces/Time stamp
string frame_id
================================================================================
MSG: builtin_interfaces/Time
int32 sec
uint32 nanosec
"""
SCHEMA = Schema(id=1, name=MESSAGE_TYPE, encoding="ros2msg", data=DEFINITION.encode())
CHANNEL = Channel(id=1, schema_id=1, topic="/camera/color", message_encoding="cdr", metadata={})


def message(payload: bytes, fmt: str, stamp: int = 123) -> Message:
    encode = serialize_dynamic(MESSAGE_TYPE, DEFINITION)[MESSAGE_TYPE]
    value = {
        "header": {"stamp": {"sec": 0, "nanosec": stamp}, "frame_id": "camera"},
        "format": fmt,
        "data": payload,
    }
    return Message(channel_id=1, sequence=0, log_time=stamp, publish_time=stamp, data=encode(value))


def test_cdr_jpeg_keeps_original_compressed_bytes() -> None:
    out = io.BytesIO()
    Image.new("RGB", (32, 24), (60, 100, 200)).save(out, "JPEG")
    content = out.getvalue()
    decoder = McapCameraDecoder(McapRos2DecoderProbe())
    frames = decoder.decode(SCHEMA, CHANNEL, message(content, "rgb8; jpeg compressed bgr8"))
    assert len(frames) == 1
    assert frames[0].image == content
    assert frames[0].observation.timestamp_ns == 123
    assert not frames[0].observation.corrupt


def test_cdr_png_depth_preserves_all_16_bit_values() -> None:
    values = np.arange(32 * 24, dtype=np.uint16).reshape(24, 32) * 70
    out = io.BytesIO()
    Image.fromarray(values).save(out, "PNG")
    content = out.getvalue()
    decoder = McapCameraDecoder(McapRos2DecoderProbe())
    frame = decoder.decode(SCHEMA, CHANNEL, message(content, "16UC1; png"))[0]
    assert not frame.observation.corrupt
    assert frame.image == content
    with Image.open(io.BytesIO(frame.image)) as image:
        assert np.array_equal(np.asarray(image), values)


def hevc_packets(count: int = 8, *, bframes: int = 2) -> list[tuple[int, bytes]]:
    codec = av.CodecContext.create("libx265", "w")
    codec.width, codec.height = 32, 24
    codec.pix_fmt = "yuv420p"
    codec.time_base = Fraction(1, 30)
    codec.framerate = Fraction(30, 1)
    codec.thread_count = 1
    codec.options = {
        "preset": "ultrafast",
        "x265-params": (
            f"bframes={bframes}:keyint=30:repeat-headers=1:"
            "pools=none:frame-threads=1:log-level=error"
        ),
    }
    packets = []
    for i in range(count):
        array = np.full((24, 32, 3), 20 + (i % 11) * 20, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(array, format="rgb24")
        frame.pts = i
        packets.extend(codec.encode(frame))
    packets.extend(codec.encode(None))
    return [(int(packet.pts), bytes(packet)) for packet in packets]


@pytest.mark.parametrize("fmt", ["h265", "hevc", "H.265"])
def test_cdr_h265_decodes_interframe_dependencies_and_flushes_delayed_frames(fmt: str) -> None:
    decoder = McapCameraDecoder(McapRos2DecoderProbe())
    frames = []
    for index, packet in hevc_packets():
        frames.extend(decoder.decode(SCHEMA, CHANNEL, message(packet, fmt, 1000 + index)))
    frames.extend(decoder.finish())
    assert len(frames) == 8
    assert all(not f.observation.corrupt for f in frames), [f.error for f in frames]
    assert sorted(f.observation.timestamp_ns for f in frames) == list(range(1000, 1008))
    for frame in frames:
        with Image.open(io.BytesIO(frame.image)) as image:
            expected = 20 + (frame.observation.timestamp_ns - 1000) * 20
            assert np.asarray(image).mean() == pytest.approx(expected, abs=5)


def test_h265_projection_sorts_delayed_frames_with_other_topics(tmp_path) -> None:
    signal_schema = Schema(id=2, name="signal", encoding="jsonschema", data=b"{}")
    signal_channel = Channel(id=2, schema_id=2, topic="state", message_encoding="json", metadata={})
    from hc_data_platform.verification import CompositeDecoderProbe, RegisteredDecoderProbe

    decoder = CompositeDecoderProbe(
        (
            McapRos2DecoderProbe(),
            RegisteredDecoderProbe({("json", "jsonschema"): lambda schema, data: json.loads(data)}),
        )
    )
    records = []
    for i, packet in hevc_packets():
        records.append((SCHEMA, CHANNEL, message(packet, "h265", 1000 + i * 2)))
        records.append(
            (
                signal_schema,
                signal_channel,
                Message(
                    channel_id=2,
                    sequence=0,
                    log_time=1001 + i * 2,
                    publish_time=1001 + i * 2,
                    data=b"[1,2]",
                ),
            )
        )
    path = tmp_path / "decoded.sqlite3"
    build_projection(
        path,
        records,
        camera_topics={CHANNEL.topic},
        expected_topics={CHANNEL.topic, signal_channel.topic},
        decoder=decoder,
        normalize_value=lambda value: value,
    )
    rows = list(read_projection(path))
    assert len(rows) == 16
    assert [row.timestamp_ns for row in rows] == list(range(1000, 1016))
    assert not any(row.corrupt for row in rows)
    # Exercise the SQLite -> Arrow boundary used to recover legacy selections.
    from dataclasses import asdict

    import pyarrow.ipc as ipc

    from hc_data_platform.workflow.ingest_plan import _ArrowProjectionWriter

    arrow_path = tmp_path / "projection.arrow"
    writer = _ArrowProjectionWriter(arrow_path, source_sha256="a" * 64)
    for row in rows:
        writer.append(**asdict(row), alignment_accepted=True)
    writer.close()
    table = ipc.open_file(arrow_path).read_all()
    assert table.num_rows == 16
    assert table["is_camera"].to_pylist().count(True) == 8
    assert table["corrupt"].to_pylist() == [False] * 16


def test_broken_cdr_image_is_visible_as_corrupt() -> None:
    decoder = McapCameraDecoder(McapRos2DecoderProbe())
    frame = decoder.decode(SCHEMA, CHANNEL, message(b"broken", "jpeg"))[0]
    assert frame.observation.corrupt and frame.image is None
    assert frame.error


def test_incomplete_h265_does_not_report_success() -> None:
    decoder = McapCameraDecoder(McapRos2DecoderProbe())
    frames = decoder.decode(SCHEMA, CHANNEL, message(b"\x00\x00\x01\x02\x01\xff", "h265"))
    frames.extend(decoder.finish())
    assert frames and all(f.observation.corrupt for f in frames)
