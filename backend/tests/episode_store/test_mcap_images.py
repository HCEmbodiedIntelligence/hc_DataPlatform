"""Typed source pixels/units only; this is not a complete MCAP ingestion test."""

import base64
import hashlib
import io
import json
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from mcap.records import Channel, Message, Schema
from mcap_ros2._dynamic import serialize_dynamic
from PIL import Image

from hc_data_platform.episode_store.mcap_images import CameraFrame, decode_camera
from hc_data_platform.episode_store.mcap_package import frame_for, mcap_steps
from hc_data_platform.episode_store.mcap_plan import McapCameraContract
from hc_data_platform.lance_catalog.models import StepRecord
from hc_data_platform.verification.ports import McapRos2DecoderProbe

ROS_IMAGE = """std_msgs/Header header
uint32 height
uint32 width
string encoding
uint8 is_bigendian
uint32 step
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


def profile(encoding="16UC1"):
    return {
        "schema_version": "hc-depth-source/1",
        "encoding": encoding,
        "unit": "source_unit",
        "scale_m_per_unit": 0.00025,
        "invalid_values": [0, 65535] if encoding != "32FC1" else [0],
        "invalid_nonfinite": True,
        "registered_to_rgb": False,
        "registration_evidence": "independent synthetic depth sensor grid",
        "evidence": {"kind": "synthetic_fixture", "reference": __name__, "version": "1"},
    }


def objects(body, *, encoding="json", contract=None, schema=None):
    channel = Channel(
        id=2,
        schema_id=1,
        topic="/declared_camera",
        message_encoding=encoding,
        metadata={} if contract is None else {"hc.depth_source": json.dumps(contract)},
    )
    message = Message(
        channel_id=2, sequence=37, log_time=1_500_000_007, publish_time=1_400_000_009, data=body
    )
    return schema, channel, message


def encoded(pixels, contract=None):
    stream = io.BytesIO()
    image_format = "PNG" if pixels.ndim == 2 else "JPEG"
    Image.fromarray(pixels).save(stream, format=image_format)
    body = stream.getvalue()
    value = {
        "encoding": "16UC1" if pixels.ndim == 2 else "jpeg",
        "width": pixels.shape[1],
        "height": pixels.shape[0],
        "data_base64": base64.b64encode(body).decode(),
    }
    if image_format == "JPEG":
        value["jpeg_sha256"] = hashlib.sha256(body).hexdigest()
    if contract is not None:
        value["source_profile"] = contract
    return value


def decode_json(value, channel_profile=None):
    return decode_camera(*objects(json.dumps(value).encode(), contract=channel_profile), None)


def test_json_depth_pixels_zero_saturation_and_units_survive_projection_without_quantization():
    pixels = np.array([[0, 65535, 5], [4000, 4001, 48000]], dtype=np.uint16)
    frame = decode_json(encoded(pixels, profile()))
    assert frame.kind == "depth" and frame.profile["scale_m_per_unit"] == 0.00025
    assert frame.source["sequence"] == 37
    assert frame.source["alignment_clock"] == "mcap_log_time"
    assert frame.source["log_time_ns"] == 1_500_000_007
    assert frame.source["publish_time_ns"] == 1_400_000_009
    assert frame.source["sensor_timestamp_ns"] is None
    restored = CameraFrame.from_projection(frame.projection_value())
    np.testing.assert_array_equal(restored.pixels, pixels)
    assert restored.source == frame.source and restored.profile == frame.profile
    channel_declared = decode_json(encoded(pixels), profile())
    np.testing.assert_array_equal(channel_declared.pixels, pixels)


@pytest.mark.parametrize("mutation", ["unknown", "different-channel", "scale", "hash", "size"])
def test_depth_contract_is_required_and_conflicts_are_rejected(mutation):
    value = encoded(np.full((2, 3), 4000, dtype=np.uint16), profile())
    channel = None
    if mutation == "unknown":
        del value["source_profile"]
    elif mutation == "different-channel":
        channel = {**profile(), "scale_m_per_unit": 0.001}
    elif mutation == "scale":
        value["depth_scale"] = 0.001
    elif mutation == "hash":
        value["source_profile_sha256"] = "0" * 64
    else:
        value["width"] = 4
    with pytest.raises(ValueError):
        decode_json(value, channel)


def test_rgb_jpeg_uses_envelope_hash_and_cannot_claim_depth():
    value = encoded(np.arange(48, dtype=np.uint8).reshape(4, 4, 3))
    frame = decode_json(value)
    assert frame.kind == "rgb" and frame.profile is None
    assert frame.pixels.shape == (4, 4, 3)
    restored = CameraFrame.from_projection(frame.projection_value())
    np.testing.assert_array_equal(restored.pixels, frame.pixels)
    with pytest.raises(ValueError):
        decode_json(value, profile())
    with pytest.raises(ValueError, match="identity"):
        decode_json({**value, "jpeg_sha256": "0" * 64})


@pytest.mark.parametrize("candidate", [(), (1_400_000_000,)])
def test_missing_or_out_of_tolerance_frame_retains_selection_reason(candidate):
    step = StepRecord(
        rollout_id="test",
        step_index=0,
        timestamp_ns=1_500_000_000,
        modalities={"/depth": None},
        source_timestamps_ns={"/depth": candidate},
        time_error_ns={"/depth": None if not candidate else 100_000_000},
        valid={"/depth": False},
        sample_valid=False,
    )
    plan = SimpleNamespace(feature_mapping={"/depth": "depth"}, cameras={"depth": None}, metadata={})
    result = next(mcap_steps(plan, [step]))
    ref = result.modalities["/depth"]
    assert ref["missing"] and ref["source"] is None and ref["source_profile_sha256"] is None
    assert ref["selection_status"] == (
        "NO_FRAME_WITHIN_TOLERANCE" if candidate else "NO_SOURCE_FRAME"
    )
    assert ref["candidate_source_timestamps_ns"] == list(candidate)
    assert result.sample_valid is False


@pytest.mark.parametrize(
    "field", ["source_frame_index", "topic", "source_time", "time_error", "valid"]
)
def test_present_frame_cannot_forge_its_aligned_source_reference(field):
    frame = decode_json(encoded(np.full((16, 16), 4000, np.uint16), profile()))
    frame.source["source_frame_index"] = 3
    value = frame.projection_value()
    contract = McapCameraContract(
        kind="depth",
        shape=(16, 16),
        source_profiles={value["source_profile_sha256"]: frame.profile},
    )
    step = StepRecord(
        rollout_id="test",
        step_index=0,
        timestamp_ns=1_500_000_000,
        modalities={"/declared_camera": value},
        source_timestamps_ns={"/declared_camera": (1_500_000_007,)},
        time_error_ns={"/declared_camera": 7},
        valid={"/declared_camera": True},
    )
    assert frame_for(step, "/declared_camera", contract) is not None
    if field in {"source_frame_index", "topic"}:
        value["source"][field] = -1 if field == "source_frame_index" else "/another"
    else:
        field_name, wrong = {
            "source_time": ("source_timestamps_ns", (0,)),
            "time_error": ("time_error_ns", 99),
            "valid": ("valid", False),
        }[field]
        step = step.model_copy(update={field_name: {"/declared_camera": wrong}})
    with pytest.raises(ValueError, match="frozen source contract"):
        frame_for(step, "/declared_camera", contract)


@pytest.mark.parametrize("name", ["sensor_msgs/Image", "sensor_msgs/msg/Image"])
@pytest.mark.parametrize(
    "encoding,big_endian", [("16UC1", False), ("16UC1", True), ("32FC1", True)]
)
def test_actual_ros_cdr_decoding_preserves_padding_byte_order_and_nonfinite(
    encoding, big_endian, name
):
    dtype = np.dtype((">" if big_endian else "<") + ("f4" if encoding == "32FC1" else "u2"))
    pixels = np.array([[0, 65535, 5], [4000, 4001, 48000]], dtype=dtype)
    if encoding == "32FC1":
        pixels[0, 1], pixels[1, 2] = np.nan, np.inf
    packed = b"".join(row.tobytes() + b"\xaa\xbb" for row in pixels)
    value = {
        "header": {"stamp": {"sec": 1, "nanosec": 123}, "frame_id": "test_sensor"},
        "height": 2,
        "width": 3,
        "encoding": encoding,
        "is_bigendian": int(big_endian),
        "step": 3 * dtype.itemsize + 2,
        "data": packed,
    }
    body = serialize_dynamic(name, ROS_IMAGE)[name](value)
    schema = Schema(id=1, name=name, encoding="ros2msg", data=ROS_IMAGE.encode())
    args = objects(body, encoding="cdr", contract=profile(encoding), schema=schema)
    frame = decode_camera(*args, McapRos2DecoderProbe())
    np.testing.assert_array_equal(frame.pixels, pixels)
    assert frame.source["sensor_timestamp_ns"] == 1_000_000_123
    assert frame.source["log_time_ns"] != frame.source["sensor_timestamp_ns"]
    assert frame.source["row_step"] == 3 * dtype.itemsize + 2
    assert frame.source["source_dtype"] == dtype.str
    restored = CameraFrame.from_projection(frame.projection_value())
    np.testing.assert_array_equal(restored.pixels, pixels)
    assert (
        restored.pixels.dtype.str == "<f4"
        if encoding == "32FC1"
        else restored.pixels.dtype.str == "<u2"
    )
    with pytest.raises(ValueError, match="DEPTH_SOURCE_CONTRACT_REQUIRED"):
        decode_camera(args[0], replace(args[1], metadata={}), args[2], McapRos2DecoderProbe())


ROS_COMPRESSED_IMAGE = (
    "std_msgs/Header header\nstring format\nuint8[] data\n" + ROS_IMAGE[ROS_IMAGE.index("=" * 80) :]
)


def compressed_cdr(pixels, name, *, contract=None, image_format=None):
    compressed = io.BytesIO()
    compression = "PNG" if pixels.ndim == 2 else "JPEG"
    Image.fromarray(pixels).save(compressed, format=compression)
    value = {
        "header": {"stamp": {"sec": 7, "nanosec": 123}, "frame_id": "source_sensor"},
        "format": image_format or ("16UC1; png" if pixels.ndim == 2 else "rgb8; jpeg"),
        "data": compressed.getvalue(),
    }
    body = serialize_dynamic(name, ROS_COMPRESSED_IMAGE)[name](value)
    schema = Schema(id=1, name=name, encoding="ros2msg", data=ROS_COMPRESSED_IMAGE.encode())
    return objects(body, encoding="cdr", contract=contract, schema=schema)


@pytest.mark.parametrize("name", ["sensor_msgs/CompressedImage", "sensor_msgs/msg/CompressedImage"])
def test_compressed_ros_depth_png_preserves_source_pixels_with_explicit_units(name):
    pixels = np.array([[0, 65535, 5], [4000, 4001, 48000]], np.uint16)
    args = compressed_cdr(pixels, name, contract=profile())
    frame = decode_camera(*args, McapRos2DecoderProbe())
    assert frame.kind == "depth" and frame.profile == profile()
    assert frame.source["source_encoding"] == "16UC1; png"
    assert frame.source["sensor_timestamp_ns"] == 7_000_000_123
    np.testing.assert_array_equal(frame.pixels, pixels)
    restored = CameraFrame.from_projection(frame.projection_value())
    np.testing.assert_array_equal(restored.pixels, pixels)
    with pytest.raises(ValueError, match="DEPTH_SOURCE_CONTRACT_REQUIRED"):
        decode_camera(args[0], replace(args[1], metadata={}), args[2], McapRos2DecoderProbe())


@pytest.mark.parametrize("name", ["sensor_msgs/CompressedImage", "sensor_msgs/msg/CompressedImage"])
def test_compressed_ros_rgb_alias_keeps_its_source_identity_and_pixels(name):
    args = compressed_cdr(np.arange(48, dtype=np.uint8).reshape(4, 4, 3), name)
    frame = decode_camera(*args, McapRos2DecoderProbe())
    assert frame.kind == "rgb" and frame.pixels.shape == (4, 4, 3)
    assert frame.source["message_sha256"] == hashlib.sha256(args[2].data).hexdigest()
    assert frame.source["source_encoding"] == "rgb8; jpeg"


@pytest.mark.parametrize(
    "image_format,contract",
    [
        ("16UC1; compressedDepth png", profile()),
        ("png", profile()),
        ("mono16; png", profile()),
        ("32FC1; png", profile("32FC1")),
        ("16UC1; jpeg", profile()),
    ],
)
def test_compressed_ros_depth_transport_and_layout_must_match_explicit_contract(
    image_format, contract
):
    args = compressed_cdr(
        np.full((2, 3), 4000, dtype=np.uint16),
        "sensor_msgs/CompressedImage",
        contract=contract,
        image_format=image_format,
    )
    with pytest.raises(ValueError):
        decode_camera(*args, McapRos2DecoderProbe())


@pytest.mark.parametrize(
    "field,new", [("dtype", "<f4"), ("pixels", b"bad"), ("source_profile_sha256", "0" * 64)]
)
def test_staged_image_layout_and_profile_hash_are_rechecked(field, new):
    frame = decode_json(encoded(np.full((2, 3), 4000, dtype=np.uint16), profile()))
    with pytest.raises(ValueError):
        CameraFrame.from_projection({**frame.projection_value(), field: new})
