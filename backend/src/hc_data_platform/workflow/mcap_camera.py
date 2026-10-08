"""Decode MCAP camera messages without changing the immutable source recording.

HEVC messages contain one Annex-B access unit each. A decoder is retained per
channel, including delayed frames, until EOF. Parameter-only messages are kept
with the following access unit. Packet PTS is retained through frame reordering.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from io import BytesIO
from typing import Any

from PIL import Image, ImageStat

from hc_data_platform.quality.models import ImageObservation

MAX_IMAGE_BYTES = 64 * 1024**2
MAX_IMAGE_PIXELS = 16 * 1024**2
_TIME_BASE = Fraction(1, 1_000_000_000)
_START_CODE = re.compile(b"\x00\x00(?:\x00)?\x01")


@dataclass(frozen=True)
class CameraFrame:
    topic: str
    observation: ImageObservation
    image: bytes | None
    perceptual_hash: str | None = None
    error: str | None = None


def image_frame(topic: str, timestamp_ns: int, content: bytes) -> CameraFrame:
    """Validate pixels, preserving JPEG/PNG bytes and 16-bit depth precision."""
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("camera image exceeds the byte limit or is empty")
    with Image.open(BytesIO(content)) as image:
        if image.format not in {"JPEG", "PNG"}:
            raise ValueError("camera image must contain JPEG or PNG pixels")
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ValueError("camera image exceeds the pixel limit")
        image.load()
        luma = image.convert("L")
        mean = float(ImageStat.Stat(luma).mean[0])
        pixels = list(luma.resize((9, 8), Image.Resampling.BILINEAR).tobytes())
        bits = 0
        for row in range(8):
            for column in range(8):
                offset = row * 9 + column
                bits = (bits << 1) | int(pixels[offset] > pixels[offset + 1])
    return CameraFrame(
        topic,
        ImageObservation(
            timestamp_ns=timestamp_ns,
            luma_mean=mean,
            fingerprint=hashlib.sha256(content).hexdigest(),
        ),
        content,
        f"{bits:016x}",
    )


def _corrupt(topic: str, timestamp_ns: int, detail: str) -> CameraFrame:
    return CameraFrame(
        topic, ImageObservation(timestamp_ns=timestamp_ns, corrupt=True), None, error=detail
    )


class _HevcStream:
    def __init__(self, topic: str) -> None:
        import av

        self.topic = topic
        self.codec = av.CodecContext.create("hevc", "r")
        self.codec.thread_count = 1
        self.pending: Counter[int] = Counter()
        self.parameters = b""

    def decode(self, content: bytes, timestamp_ns: int) -> list[CameraFrame]:
        import av

        starts = list(_START_CODE.finditer(content))
        if not starts or any(content[: starts[0].start()]):
            raise ValueError("H.265 requires Annex-B access units with start codes")
        kinds = []
        for start in starts:
            if start.end() + 2 > len(content):
                raise ValueError("truncated H.265 NAL header")
            first, second = content[start.end() : start.end() + 2]
            if first & 0x80 or not second & 7:
                raise ValueError("invalid H.265 NAL header")
            kinds.append((first >> 1) & 0x3F)
        if not any(kind < 32 for kind in kinds):
            if len(self.parameters) + len(content) > MAX_IMAGE_BYTES:
                raise ValueError("H.265 parameter data exceeds the byte limit")
            self.parameters += content
            return []
        if sum(self.pending.values()) >= 64:
            raise ValueError("H.265 decoder exceeded the bounded frame delay")
        packet = av.Packet(self.parameters + content)
        self.parameters = b""
        packet.pts = timestamp_ns
        packet.time_base = _TIME_BASE
        # Arrival/DTS and presentation order can differ. Do not invent DTS from PTS.
        self.pending[timestamp_ns] += 1
        try:
            decoded = self.codec.decode(packet)
        except Exception:
            self._consume(timestamp_ns)
            raise
        return self._frames(decoded)

    def _consume(self, timestamp: int) -> None:
        self.pending[timestamp] -= 1
        if self.pending[timestamp] <= 0:
            del self.pending[timestamp]

    def _frames(self, frames: list[Any]) -> list[CameraFrame]:
        import numpy as np

        result = []
        for frame in frames:
            if frame.pts is None:
                raise ValueError("H.265 decoder omitted the source frame timestamp")
            timestamp = int(Fraction(frame.pts) * (frame.time_base or _TIME_BASE) / _TIME_BASE)
            if self.pending[timestamp] < 1:
                raise ValueError("H.265 frame has no matching source access unit")
            self._consume(timestamp)
            if frame.width * frame.height > MAX_IMAGE_PIXELS:
                raise ValueError("H.265 frame exceeds the pixel limit")
            if frame.format.name in {"gray10le", "gray12le", "gray16le"}:
                plane = frame.planes[0]
                pixels = np.frombuffer(plane, dtype="<u2").reshape(
                    frame.height, plane.line_size // 2
                )
                image = Image.fromarray(pixels[:, : frame.width].copy())
            else:
                image = frame.to_image()
            with image, BytesIO() as output:
                image.save(output, format="PNG")
                result.append(image_frame(self.topic, timestamp, output.getvalue()))
        return result

    def finish(self) -> list[CameraFrame]:
        result = []
        try:
            result.extend(self._frames(self.codec.decode(None)))
        except Exception as exc:
            detail = f"H.265 flush failed: {type(exc).__name__}: {exc}"
        else:
            detail = "H.265 access unit produced no frame (missing keyframe or truncated stream)"
        for stamp, count in sorted(self.pending.items()):
            result.extend(_corrupt(self.topic, stamp, detail) for _ in range(count))
        self.pending.clear()
        return result


class McapCameraDecoder:
    """Per-recording decoder for JSON images and ROS2 CDR compressed cameras."""

    def __init__(self, decoder: Any) -> None:
        self._decoder = decoder
        self._hevc: dict[tuple[str, int], _HevcStream] = {}
        self._formats: dict[tuple[str, int], str] = {}

    def decode(self, schema: Any, channel: Any, message: Any) -> list[CameraFrame]:
        topic, timestamp = str(channel.topic), int(message.log_time)
        try:
            encoding, content, dimensions = self._payload(schema, channel, message.data)
            key = (topic, int(channel.id))
            prior = self._formats.setdefault(key, encoding)
            if prior != encoding:
                raise ValueError("camera encoding changed within an MCAP channel")
            if encoding == "hevc":
                if key not in self._hevc:
                    self._hevc[key] = _HevcStream(topic)
                stream = self._hevc[key]
                return stream.decode(content, timestamp)
            result = image_frame(topic, timestamp, content)
            with Image.open(BytesIO(content)) as image:
                if image.format != encoding.upper():
                    raise ValueError("camera pixels differ from the declared encoding")
                if dimensions is not None and image.size != dimensions:
                    raise ValueError("camera dimensions differ from the declared envelope")
            return [result]
        except Exception as exc:
            return [_corrupt(topic, timestamp, f"{type(exc).__name__}: {exc}")]

    def _payload(
        self, schema: Any, channel: Any, content: bytes
    ) -> tuple[str, bytes, tuple[int, int] | None]:
        if len(content) > MAX_IMAGE_BYTES:
            raise ValueError("camera message exceeds the byte limit")
        dimensions = None
        if channel.message_encoding == "json":
            value = json.loads(content)
            fmt = value.get("encoding", value.get("codec", ""))
            content = base64.b64decode(value["data_base64"], validate=True)
            if fmt == "jpeg" and value.get("jpeg_sha256") != hashlib.sha256(content).hexdigest():
                raise ValueError("camera JPEG hash differs from its envelope")
            if "width" in value and "height" in value:
                dimensions = (value["width"], value["height"])
        elif (
            channel.message_encoding == "cdr"
            and schema is not None
            and schema.encoding == "ros2msg"
        ):
            if self._decoder is None:
                raise ValueError("ROS2 camera decoder is not configured")
            value = self._decoder.probe(
                message_encoding=channel.message_encoding,
                schema_encoding=schema.encoding,
                schema_name=schema.name,
                schema_data=schema.data,
                message_data=content,
            )
            fmt = getattr(value, "format", getattr(value, "codec", ""))
            content = bytes(value.data)
        else:
            raise ValueError(f"unsupported camera message encoding: {channel.message_encoding}")
        normalized = str(fmt).lower().strip()
        if "compresseddepth" in normalized:
            raise ValueError("compressedDepth transport headers require an explicit depth adapter")
        tokens = set(re.findall(r"[a-z0-9]+", normalized))
        if tokens & {"h265", "hevc"} or "h.265" in normalized:
            encoding = "hevc"
        elif tokens & {"jpeg", "jpg"}:
            encoding = "jpeg"
        elif "png" in tokens:
            encoding = "png"
        else:
            raise ValueError(f"unsupported compressed camera format: {fmt}")
        if not content or len(content) > MAX_IMAGE_BYTES:
            raise ValueError("empty or oversized camera payload")
        return encoding, content, dimensions

    def finish(self) -> list[CameraFrame]:
        return [frame for stream in self._hevc.values() for frame in stream.finish()]
