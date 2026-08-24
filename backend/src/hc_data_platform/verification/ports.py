"""Ports owned by BE-05 and dependency-free fakes for contract tests."""

from __future__ import annotations

import importlib
import io
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Protocol, cast, runtime_checkable

from .models import RawVerificationReportV1


@runtime_checkable
class ReadableBinaryStream(Protocol):
    """Minimum reader contract supported by local files and cloud streaming bodies."""

    def read(self, size: int = -1) -> bytes: ...

    def close(self) -> None: ...


@runtime_checkable
class ReadableObjectStorage(Protocol):
    def open_reader(self, object_key: str) -> ReadableBinaryStream:
        """Return a fresh binary stream. The caller closes it."""


@runtime_checkable
class ChunkReadableObjectStorage(Protocol):
    def read_chunks(self, key: str, chunk_size: int) -> Iterable[bytes]: ...


@runtime_checkable
class DecoderProbe(Protocol):
    def supports(self, message_encoding: str, schema_encoding: str) -> bool: ...

    def probe(
        self,
        *,
        message_encoding: str,
        schema_encoding: str,
        schema_name: str = "",
        schema_data: bytes,
        message_data: bytes,
    ) -> object:
        """Return the decoded sample or raise ``ValueError`` when it is invalid."""


@runtime_checkable
class ChunkDecompressor(Protocol):
    def decompress(
        self,
        *,
        compression: str,
        data: bytes,
        uncompressed_size: int,
    ) -> bytes: ...


@runtime_checkable
class RawVerificationPort(Protocol):
    def verify(
        self,
        *,
        rollout_id: str,
        object_key: str,
        source_sha256: str,
        required_topics: set[str],
        known_optional_topics: set[str] | None = None,
    ) -> RawVerificationReportV1: ...


class FakeObjectStorage:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.open_count = 0

    def open_reader(self, object_key: str) -> ReadableBinaryStream:
        self.open_count += 1
        return io.BytesIO(self.objects[object_key])


class FakeDecoderProbe:
    def __init__(
        self, supported: set[tuple[str, str]] | None = None, reject_prefix: bytes = b""
    ) -> None:
        self.supported = (
            {("cdr", "ros2msg"), ("json", "jsonschema")} if supported is None else supported
        )
        self.reject_prefix = reject_prefix
        self.calls: list[tuple[str, str]] = []

    def supports(self, message_encoding: str, schema_encoding: str) -> bool:
        return (message_encoding, schema_encoding) in self.supported

    def probe(
        self,
        *,
        message_encoding: str,
        schema_encoding: str,
        schema_name: str = "",
        schema_data: bytes,
        message_data: bytes,
    ) -> object:
        del schema_name
        self.calls.append((message_encoding, schema_encoding))
        if self.reject_prefix and message_data.startswith(self.reject_prefix):
            raise ValueError("sample rejected by decoder")
        return message_data


SampleDecoder = Callable[[bytes, bytes], object]


class RegisteredDecoderProbe:
    """Production probe adapter for application-supplied schema decoders."""

    def __init__(self, decoders: Mapping[tuple[str, str], SampleDecoder]) -> None:
        self._decoders = dict(decoders)

    def supports(self, message_encoding: str, schema_encoding: str) -> bool:
        return (message_encoding, schema_encoding) in self._decoders

    def probe(
        self,
        *,
        message_encoding: str,
        schema_encoding: str,
        schema_name: str = "",
        schema_data: bytes,
        message_data: bytes,
    ) -> object:
        del schema_name
        try:
            decoder = self._decoders[(message_encoding, schema_encoding)]
        except KeyError as exc:
            raise ValueError("no registered decoder supports the schema pair") from exc
        try:
            return decoder(schema_data, message_data)
        except (TypeError, ValueError):
            raise
        except Exception as exc:
            raise ValueError(f"decoder raised {type(exc).__name__}: {exc}") from exc


class CompositeDecoderProbe:
    """Route a schema pair to the first configured production decoder."""

    def __init__(self, decoders: Sequence[DecoderProbe]) -> None:
        if not decoders:
            raise ValueError("at least one decoder probe is required")
        self._decoders = tuple(decoders)

    def supports(self, message_encoding: str, schema_encoding: str) -> bool:
        return any(
            decoder.supports(message_encoding, schema_encoding) for decoder in self._decoders
        )

    def probe(
        self,
        *,
        message_encoding: str,
        schema_encoding: str,
        schema_name: str = "",
        schema_data: bytes,
        message_data: bytes,
    ) -> object:
        decoder = next(
            (item for item in self._decoders if item.supports(message_encoding, schema_encoding)),
            None,
        )
        if decoder is None:
            raise ValueError("no registered decoder supports the schema pair")
        return decoder.probe(
            message_encoding=message_encoding,
            schema_encoding=schema_encoding,
            schema_name=schema_name,
            schema_data=schema_data,
            message_data=message_data,
        )


class McapRos2DecoderProbe:
    """Decode one CDR/ros2msg sample with the official MCAP ROS 2 factory."""

    def __init__(self, decoder_factory: Callable[[], Any] | None = None) -> None:
        self._decoder_factory = decoder_factory

    def supports(self, message_encoding: str, schema_encoding: str) -> bool:
        return (message_encoding, schema_encoding) == ("cdr", "ros2msg")

    def probe(
        self,
        *,
        message_encoding: str,
        schema_encoding: str,
        schema_name: str = "",
        schema_data: bytes,
        message_data: bytes,
    ) -> object:
        if not self.supports(message_encoding, schema_encoding):
            raise ValueError("the ROS 2 decoder only supports cdr/ros2msg")
        if not schema_name:
            raise ValueError("the ROS 2 decoder requires the MCAP schema name")
        try:
            from mcap.records import Schema

            factory = self._factory()()
            schema = Schema(
                id=1,
                name=schema_name,
                encoding=schema_encoding,
                data=schema_data,
            )
            decoder = factory.decoder_for(message_encoding, schema)
            if decoder is None:
                raise ValueError("the ROS 2 factory rejected the schema pair")
            return decoder(message_data)
        except (TypeError, ValueError):
            raise
        except Exception as exc:
            raise ValueError(f"ROS 2 decoder raised {type(exc).__name__}: {exc}") from exc

    def _factory(self) -> Callable[[], Any]:
        if self._decoder_factory is not None:
            return self._decoder_factory
        try:
            module = importlib.import_module("mcap_ros2.decoder")
        except ImportError as exc:
            raise ValueError("mcap-ros2-support is required to decode cdr/ros2msg samples") from exc
        return cast(Callable[[], Any], module.DecoderFactory)


class _ChunkIteratorStream:
    def __init__(self, chunks: Iterable[bytes], max_chunk_size: int) -> None:
        self._chunks = iter(chunks)
        self._max_chunk_size = max_chunk_size
        self._pending = b""
        self._closed = False

    def read(self, size: int = -1) -> bytes:
        if self._closed:
            raise ValueError("I/O operation on closed object")
        if size == 0:
            return b""
        if size < 0:
            result = bytearray(self._pending)
            self._pending = b""
            for chunk in self._chunks:
                result.extend(self._validate_chunk(chunk))
            return bytes(result)

        result = bytearray()
        while len(result) < size:
            if self._pending:
                take = min(size - len(result), len(self._pending))
                result.extend(self._pending[:take])
                self._pending = self._pending[take:]
                continue
            try:
                chunk = self._validate_chunk(next(self._chunks))
            except StopIteration:
                break
            if chunk:
                self._pending = chunk
        return bytes(result)

    def seekable(self) -> bool:
        """Advertise the cloud body as streaming so MCAP selects its sequential reader."""

        return False

    def close(self) -> None:
        self._closed = True
        close = getattr(self._chunks, "close", None)
        if close is not None:
            close()

    def _validate_chunk(self, chunk: bytes) -> bytes:
        if not isinstance(chunk, bytes):
            raise TypeError("object storage read_chunks() must yield bytes")
        if len(chunk) > self._max_chunk_size:
            raise ValueError(
                f"object storage yielded {len(chunk)} bytes, above {self._max_chunk_size}"
            )
        return chunk


class ChunkedObjectStorageReader:
    """Adapt a production ``read_chunks`` object store to BE-05's reader port."""

    def __init__(
        self, storage: ChunkReadableObjectStorage, *, read_chunk_size: int = 1024 * 1024
    ) -> None:
        if read_chunk_size < 1:
            raise ValueError("read_chunk_size must be positive")
        self._storage = storage
        self._read_chunk_size = read_chunk_size

    def open_reader(self, object_key: str) -> ReadableBinaryStream:
        return _ChunkIteratorStream(
            self._storage.read_chunks(object_key, self._read_chunk_size),
            self._read_chunk_size,
        )
