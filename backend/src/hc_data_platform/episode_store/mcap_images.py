"""Bounded original MCAP camera decoding; no quantization or visualization.

Physical depth requires hc-depth-source/1 in the message's source_profile or
channel metadata hc.depth_source. Dtype/topic names never supply missing units.
The alignment clock is explicitly MCAP log_time; a ROS header stamp is retained
separately without assuming that it shares that clock.
"""

import base64
import hashlib
import io
import json
from dataclasses import dataclass

import numpy as np
from PIL import Image

from hc_data_platform.publishing.depth_source import ENCODINGS, profile_hash, validate_profile

VERSION = "hc-mcap-camera-frame/1"
MAX_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 16 * 1024 * 1024


def dimensions(width, height):
    if (
        type(width) is not int
        or type(height) is not int
        or not 0 < width <= 16384
        or not 0 < height <= 16384
        or width * height > MAX_PIXELS
    ):
        raise ValueError("MCAP camera dimensions exceed the bounded image contract")
    return height, width


def bounded_bytes(value):
    if not isinstance(value, (bytes, bytearray, memoryview, list, tuple)):
        raise ValueError("MCAP image data must be a byte sequence")
    if not 0 < len(value) <= MAX_BYTES:
        raise ValueError("MCAP image payload exceeds the bounded image contract")
    return bytes(value)


def profile_for(channel, payload=None):
    metadata = getattr(channel, "metadata", {}) or {}
    encoded = metadata.get("hc.depth_source")
    if encoded is not None and (not isinstance(encoded, str) or len(encoded) > 16 * 1024):
        raise ValueError("MCAP depth profile metadata exceeds the document limit")
    declared = None if encoded is None else json.loads(encoded)
    local = payload.get("source_profile") if isinstance(payload, dict) else None
    if declared is not None and local is not None and declared != local:
        raise ValueError("MCAP depth message profile differs from its channel declaration")
    value = declared if local is None else local
    return None if value is None else validate_profile(value)


def depth_profile(profile, encoding, pixels):
    if profile is None:
        raise ValueError("DEPTH_SOURCE_CONTRACT_REQUIRED: MCAP source depth units are undeclared")
    if (
        profile["encoding"] != encoding
        or (pixels.dtype.kind, pixels.dtype.itemsize) != ENCODINGS[encoding]
    ):
        raise ValueError("MCAP source depth encoding/dtype differs from its declared profile")
    return profile


def get_field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def header_stamp(value):
    stamp = get_field(get_field(value, "header"), "stamp")
    if stamp is None:
        return None
    sec, nano = get_field(stamp, "sec"), get_field(stamp, "nanosec")
    if type(sec) is not int or type(nano) is not int or sec < 0 or not 0 <= nano < 1_000_000_000:
        raise ValueError("MCAP image header requires an exact nonnegative ROS timestamp")
    return sec * 1_000_000_000 + nano


@dataclass(frozen=True)
class CameraFrame:
    pixels: np.ndarray
    source: dict
    profile: dict | None = None

    @property
    def kind(self):
        return "depth" if self.profile is not None else "rgb"

    def projection_value(self):
        dtype = (
            "<u2"
            if self.pixels.dtype.kind == "u" and self.pixels.dtype.itemsize == 2
            else ("<f4" if self.pixels.dtype.kind == "f" else "|u1")
        )
        pixels = np.ascontiguousarray(self.pixels, dtype=dtype)
        return {
            "format": VERSION,
            "kind": self.kind,
            "source": self.source,
            "shape": list(pixels.shape),
            "dtype": dtype,
            "pixels": pixels.tobytes(),
            "source_profile": self.profile,
            "source_profile_sha256": None if self.profile is None else profile_hash(self.profile),
        }

    @classmethod
    def from_projection(cls, value):
        if not isinstance(value, dict) or value.get("format") != VERSION:
            raise ValueError("aligned MCAP image has no versioned frame contract")
        dtype, shape, kind = value.get("dtype"), value.get("shape"), value.get("kind")
        if dtype not in {"|u1", "<u2", "<f4"} or not isinstance(shape, list):
            raise ValueError("aligned MCAP image has an invalid pixel layout")
        if kind not in {"depth", "rgb"} or len(shape) != (2 if kind == "depth" else 3):
            raise ValueError("aligned MCAP image shape differs from its modality")
        dimensions(shape[1], shape[0])
        if kind == "rgb" and (
            shape[2] != 3 or dtype != "|u1" or value["source_profile"] is not None
        ):
            raise ValueError("aligned MCAP RGB must contain exactly three uint8 channels")
        body = bounded_bytes(value.get("pixels"))
        if len(body) != int(np.prod(shape)) * np.dtype(dtype).itemsize:
            raise ValueError("aligned MCAP pixel buffer differs from its shape")
        pixels = np.frombuffer(body, dtype=dtype).reshape(shape)
        profile = None
        if kind == "depth":
            profile = validate_profile(value["source_profile"])
            depth_profile(profile, profile["encoding"], pixels)
            if value["source_profile_sha256"] != profile_hash(profile):
                raise ValueError("aligned MCAP depth profile hash differs from its declaration")
        return cls(pixels, value["source"], profile)


def decode_camera(schema, channel, message, decoder):
    if not isinstance(message.data, bytes) or not 0 < len(message.data) <= MAX_BYTES * 2:
        raise ValueError("MCAP image message exceeds the decode limit")
    if channel.message_encoding == "json":
        value = json.loads(message.data)
        if not isinstance(value, dict):
            raise ValueError("MCAP JSON image must be an object")
        profile = profile_for(channel, value)
        encoded = value.get("data_base64")
        if not isinstance(encoded, str) or len(encoded) > (MAX_BYTES + 2) // 3 * 4:
            raise ValueError("MCAP JSON image requires bounded data_base64")
        body = bounded_bytes(base64.b64decode(encoded, validate=True))
        encoding = value.get("encoding")
        if encoding == "jpeg":
            if profile is not None or value.get("jpeg_sha256") != hashlib.sha256(body).hexdigest():
                raise ValueError("MCAP JPEG identity or RGB declaration differs from its envelope")
            pixels = encoded_pixels(body, "JPEG", None)
        elif encoding in {"png", *ENCODINGS}:
            # Standard PNG carries integer depth; float depth must retain its
            # typed bytes, as in sensor_msgs/Image below.
            pixels = encoded_pixels(body, "PNG", profile)
            if pixels.ndim == 2:
                depth_profile(
                    profile, encoding if encoding in ENCODINGS else profile["encoding"], pixels
                )
            elif profile is not None or encoding in ENCODINGS:
                raise ValueError("MCAP depth envelope contains an RGB image")
        else:
            raise ValueError("unsupported MCAP JSON image encoding")
        if pixels.shape[:2] != dimensions(value.get("width"), value.get("height")):
            raise ValueError("MCAP image dimensions differ from its envelope")
        if profile is not None:
            if "depth_scale" in value and (
                isinstance(value["depth_scale"], bool)
                or value["depth_scale"] != profile["scale_m_per_unit"]
            ):
                raise ValueError("MCAP depth_scale differs from its source profile")
            if value.get("source_profile_sha256", profile_hash(profile)) != profile_hash(profile):
                raise ValueError("MCAP source profile hash differs from its envelope")
    elif (
        channel.message_encoding == "cdr"
        and schema is not None
        and schema.name
        in {
            "sensor_msgs/msg/Image",
            "sensor_msgs/msg/CompressedImage",
        }
    ):
        if decoder is None or not decoder.supports(channel.message_encoding, schema.encoding):
            raise ValueError("MCAP ROS image requires its registered schema decoder")
        value = decoder.probe(
            message_encoding=channel.message_encoding,
            schema_encoding=schema.encoding,
            schema_name=schema.name,
            schema_data=schema.data,
            message_data=message.data,
        )
        profile = profile_for(channel)
        if schema.name.endswith("/CompressedImage"):
            encoding = get_field(value, "format", "")
            if "compressedDepth" in encoding:
                raise ValueError("MCAP compressedDepth requires a validated transport decoder")
            if profile is not None:
                raise ValueError("MCAP compressed depth requires an explicit pixel-layout contract")
            expected = (
                "JPEG"
                if "jpeg" in encoding.lower()
                else "PNG"
                if "png" in encoding.lower()
                else None
            )
            if expected is None:
                raise ValueError("unsupported MCAP CompressedImage format")
            pixels = encoded_pixels(bounded_bytes(get_field(value, "data")), expected, None)
        else:
            pixels, encoding = raw_pixels(value)
            if pixels.ndim == 2:
                depth_profile(profile, encoding, pixels)
            elif profile is not None:
                raise ValueError("MCAP RGB camera cannot claim a depth profile")
    else:
        raise ValueError("unsupported MCAP camera message/schema encoding")
    source = {
        "topic": channel.topic,
        "channel_id": channel.id,
        "schema_id": channel.schema_id,
        "sequence": message.sequence,
        "log_time_ns": message.log_time,
        "publish_time_ns": message.publish_time,
        "sensor_timestamp_ns": header_stamp(value),
        "alignment_clock": "mcap_log_time",
        "message_sha256": hashlib.sha256(message.data).hexdigest(),
        "source_encoding": encoding,
        "source_dtype": pixels.dtype.str,
        "source_shape": list(pixels.shape),
        "row_step": get_field(value, "step"),
        "is_bigendian": get_field(value, "is_bigendian"),
    }
    return CameraFrame(pixels, source, profile)


def encoded_pixels(body, expected_format, profile):
    with Image.open(io.BytesIO(body)) as image:
        dimensions(image.width, image.height)
        if image.format != expected_format:
            raise ValueError("MCAP image compression differs from its declaration")
        if image.mode in {"I", "I;16", "I;16B", "I;16L"}:
            if profile is None:
                raise ValueError("DEPTH_SOURCE_CONTRACT_REQUIRED: MCAP integer PNG has no units")
            # PNG's IHDR bit depth/color type proves a 16-bit grayscale buffer;
            # Pillow versions may expose its values as either uint16 or int32.
            if expected_format != "PNG" or len(body) < 26 or body[24:26] != b"\x10\x00":
                raise ValueError("MCAP depth PNG must be true 16-bit single-channel data")
            pixels = np.asarray(image, dtype=np.uint16)
            depth_profile(profile, profile["encoding"], pixels)
        elif image.mode == "RGB" and profile is None:
            pixels = np.asarray(image, dtype=np.uint8)
        else:
            raise ValueError("MCAP RGB/depth channel layout is ambiguous or unsupported")
        return pixels.copy()


def raw_pixels(value):
    height, width = dimensions(get_field(value, "width"), get_field(value, "height"))
    encoding, endian, step = (
        get_field(value, "encoding"),
        get_field(value, "is_bigendian"),
        get_field(value, "step"),
    )
    if endian not in (0, 1) or type(endian) not in (bool, int):
        raise ValueError("MCAP image byte order is invalid")
    channels = 3 if encoding in {"rgb8", "bgr8"} else 1
    kind = (
        "u1"
        if channels == 3
        else "u2"
        if encoding in {"16UC1", "mono16"}
        else "f4"
        if encoding == "32FC1"
        else None
    )
    if kind is None:
        raise ValueError("unsupported MCAP raw image encoding")
    dtype = np.dtype((">" if endian else "<") + kind)
    row_bytes = width * channels * dtype.itemsize
    if type(step) is not int or step < row_bytes or step * height > MAX_BYTES:
        raise ValueError("MCAP image row stride is invalid")
    body = bounded_bytes(get_field(value, "data"))
    if len(body) != step * height:
        raise ValueError("MCAP image buffer differs from its padded row stride")
    # Strip only padding; retain all source numeric values and endianness facts.
    packed = np.frombuffer(body, dtype=np.uint8).reshape(height, step)[:, :row_bytes].copy()
    shape = (height, width, channels) if channels == 3 else (height, width)
    pixels = packed.reshape(-1).view(dtype).reshape(shape)
    if encoding == "bgr8":
        pixels = pixels[:, :, ::-1]
    return pixels, encoding
