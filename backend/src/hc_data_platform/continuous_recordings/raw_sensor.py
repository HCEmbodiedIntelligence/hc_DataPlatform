from __future__ import annotations

import io
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from hc_data_platform.verification.ports import DecoderProbe


@runtime_checkable
class RangeReadableObjectStorage(Protocol):
    """Minimal random-access object-store contract required by indexed MCAP reads."""

    def read_range(self, key: str, start: int, end: int) -> bytes:
        """Read the half-open byte range ``[start, end)``."""


class ObjectStorageRangeReader(io.BufferedIOBase):
    """Expose object-store range GETs as a seekable stream for the MCAP reader."""

    def __init__(
        self,
        storage: RangeReadableObjectStorage,
        *,
        object_key: str,
        size: int,
    ) -> None:
        super().__init__()
        if size < 0:
            raise ValueError("object size must not be negative")
        self._storage = storage
        self._object_key = object_key
        self._size = size
        self._position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed object")
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self._position + offset
        elif whence == io.SEEK_END:
            position = self._size + offset
        else:
            raise ValueError(f"unsupported whence: {whence}")
        if position < 0:
            raise ValueError("negative seek position")
        self._position = position
        return position

    def read(self, size: int | None = -1) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed object")
        if self._position >= self._size:
            return b""
        end = self._size if size is None or size < 0 else min(self._size, self._position + size)
        body = self._storage.read_range(self._object_key, self._position, end)
        expected = end - self._position
        if len(body) != expected:
            raise OSError(f"object store returned {len(body)} bytes for a {expected}-byte range")
        self._position = end
        return body


class RawSensorReadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DecodedSensorSample:
    offset_ns: int
    source_timestamp_ns: int
    value: object


@dataclass(frozen=True, slots=True)
class DecodedSensorWindow:
    topic_found: bool
    samples: tuple[DecodedSensorSample, ...]


def read_sensor_asset_window(
    *,
    storage: RangeReadableObjectStorage,
    object_key: str,
    object_size: int,
    topic: str,
    source_start_ns: int,
    source_end_ns: int,
    capture_start_ns: int,
    timestamps_are_offsets: bool,
    decoder: DecoderProbe,
    maximum_samples: int,
) -> DecodedSensorWindow:
    """Read one configured raw MCAP topic without Episode or Lance materialization."""

    try:
        from mcap.reader import make_reader
    except ImportError as exc:
        raise RawSensorReadError("MCAP data dependencies are unavailable") from exc

    samples: list[DecodedSensorSample] = []
    topic_found = False
    try:
        with ObjectStorageRangeReader(
            storage,
            object_key=object_key,
            size=object_size,
        ) as stream:
            reader = make_reader(stream)
            summary = reader.get_summary()
            if summary is not None:
                topic_found = any(channel.topic == topic for channel in summary.channels.values())
            for schema, channel, message in reader.iter_messages(
                topics=(topic,),
                start_time=source_start_ns,
                end_time=source_end_ns,
            ):
                topic_found = True
                if schema is None or not decoder.supports(
                    channel.message_encoding, schema.encoding
                ):
                    raise RawSensorReadError(
                        f"raw sensor topic {topic!r} has no configured decoder"
                    )
                source_timestamp_ns = int(message.log_time)
                offset_ns = (
                    source_timestamp_ns
                    if timestamps_are_offsets
                    else source_timestamp_ns - capture_start_ns
                )
                value = _bounded_json_value(
                    decoder.probe(
                        message_encoding=channel.message_encoding,
                        schema_encoding=schema.encoding,
                        schema_name=schema.name,
                        schema_data=schema.data,
                        message_data=message.data,
                    )
                )
                samples.append(
                    DecodedSensorSample(
                        offset_ns=offset_ns,
                        source_timestamp_ns=source_timestamp_ns,
                        value=value,
                    )
                )
                if len(samples) > maximum_samples:
                    break
    except RawSensorReadError:
        raise
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise RawSensorReadError(f"raw sensor MCAP could not be read: {exc}") from exc
    return DecodedSensorWindow(topic_found=topic_found, samples=tuple(samples))


def _bounded_json_value(value: object) -> object:
    remaining = [10_000]

    def normalize(candidate: object, depth: int = 0) -> object:
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 12:
            raise RawSensorReadError("decoded sensor value is too complex")
        if candidate is None or isinstance(candidate, (bool, int, str)):
            return candidate
        if isinstance(candidate, float):
            if not math.isfinite(candidate):
                raise RawSensorReadError("decoded sensor value is non-finite")
            return candidate
        if isinstance(candidate, Mapping):
            return {str(key): normalize(item, depth + 1) for key, item in candidate.items()}
        if isinstance(candidate, Sequence) and not isinstance(
            candidate, (str, bytes, bytearray, memoryview)
        ):
            return [normalize(item, depth + 1) for item in candidate]
        dump = getattr(candidate, "model_dump", None)
        if callable(dump):
            return normalize(dump(mode="json"), depth + 1)
        attributes = getattr(candidate, "__dict__", None)
        if isinstance(attributes, Mapping):
            return normalize(
                {key: item for key, item in attributes.items() if not str(key).startswith("_")},
                depth + 1,
            )
        slots = getattr(type(candidate), "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        if isinstance(slots, Sequence):
            values = {
                name: getattr(candidate, name)
                for name in slots
                if isinstance(name, str) and not name.startswith("_") and hasattr(candidate, name)
            }
            if len(values) == 1:
                return normalize(next(iter(values.values())), depth + 1)
            if values:
                return normalize(values, depth + 1)
        raise RawSensorReadError(
            f"decoded sensor value type {type(candidate).__name__!r} is unsupported"
        )

    result = normalize(value)
    if len(json.dumps(result, separators=(",", ":")).encode()) > 1024 * 1024:
        raise RawSensorReadError("decoded sensor value exceeds one MiB")
    return result
