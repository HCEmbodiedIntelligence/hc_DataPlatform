"""Bounded-memory MCAP integrity verification and deterministic inventory."""

from __future__ import annotations

import hashlib
import struct
import zlib
from contextlib import closing
from dataclasses import dataclass, field

from .adapters import McapSdkChunkDecompressor
from .models import (
    ChannelInventoryV1,
    RawVerificationReportV1,
    SchemaInventoryV1,
    TopicInventoryV1,
    VerificationCode,
    VerificationFinding,
    VerificationSeverity,
    VerificationStatus,
)
from .ports import ChunkDecompressor, DecoderProbe, ReadableBinaryStream, ReadableObjectStorage

MCAP_MAGIC = b"\x89MCAP0\r\n"

HEADER = 0x01
FOOTER = 0x02
SCHEMA = 0x03
CHANNEL = 0x04
MESSAGE = 0x05
CHUNK = 0x06
MESSAGE_INDEX = 0x07
CHUNK_INDEX = 0x08
ATTACHMENT = 0x09
ATTACHMENT_INDEX = 0x0A
STATISTICS = 0x0B
METADATA = 0x0C
METADATA_INDEX = 0x0D
SUMMARY_OFFSET = 0x0E
DATA_END = 0x0F

_SUMMARY_RECORDS = {SCHEMA, CHANNEL, CHUNK_INDEX, ATTACHMENT_INDEX, METADATA_INDEX, STATISTICS}
_INDEX_RECORDS = {MESSAGE_INDEX, CHUNK_INDEX, ATTACHMENT_INDEX, METADATA_INDEX, SUMMARY_OFFSET}
_KNOWN_COMPRESSIONS = {"zstd", "lz4"}


class _MalformedMcap(Exception):
    def __init__(self, code: VerificationCode, message: str, offset: int) -> None:
        super().__init__(message)
        self.code = code
        self.offset = offset


@dataclass(frozen=True, slots=True)
class _Schema:
    schema_id: int
    name: str
    encoding: str
    data: bytes


@dataclass(frozen=True, slots=True)
class _Channel:
    channel_id: int
    schema_id: int
    topic: str
    message_encoding: str


@dataclass(slots=True)
class _TopicState:
    channel_ids: set[int] = field(default_factory=set)
    message_count: int = 0
    log_time_min_ns: int | None = None
    log_time_max_ns: int | None = None
    sample: bytes | None = None
    sample_channel_id: int | None = None
    decode_checked: bool = False
    decodable: bool | None = None

    def observe(self, channel_id: int, log_time_ns: int, data: memoryview) -> None:
        self.message_count += 1
        self.log_time_min_ns = (
            log_time_ns if self.log_time_min_ns is None else min(self.log_time_min_ns, log_time_ns)
        )
        self.log_time_max_ns = (
            log_time_ns if self.log_time_max_ns is None else max(self.log_time_max_ns, log_time_ns)
        )
        if self.sample is None:
            self.sample = data.tobytes()
            self.sample_channel_id = channel_id


@dataclass(frozen=True, slots=True)
class _MessageIndexInfo:
    offset: int
    total_length: int
    chunk_offset: int
    channel_id: int
    entries: tuple[tuple[int, int], ...]


@dataclass(slots=True)
class _ChunkInfo:
    offset: int
    total_length: int
    message_start_time: int
    message_end_time: int
    compression: str
    compressed_size: int
    uncompressed_size: int
    message_offsets: dict[int, dict[int, int]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _ChunkIndexInfo:
    offset: int
    message_start_time: int
    message_end_time: int
    chunk_start_offset: int
    chunk_length: int
    message_index_offsets: dict[int, int]
    message_index_length: int
    compression: str
    compressed_size: int
    uncompressed_size: int


@dataclass(frozen=True, slots=True)
class _AttachmentInfo:
    total_length: int
    log_time: int
    create_time: int
    data_size: int
    name: str
    media_type: str


@dataclass(frozen=True, slots=True)
class _AttachmentIndexInfo:
    offset: int
    target_offset: int
    target_length: int
    log_time: int
    create_time: int
    data_size: int
    name: str
    media_type: str


@dataclass(frozen=True, slots=True)
class _MetadataInfo:
    total_length: int
    name: str


@dataclass(frozen=True, slots=True)
class _MetadataIndexInfo:
    offset: int
    target_offset: int
    target_length: int
    name: str


@dataclass(frozen=True, slots=True)
class _StatisticsInfo:
    offset: int
    message_count: int
    schema_count: int
    channel_count: int
    attachment_count: int
    metadata_count: int
    chunk_count: int
    message_start_time: int
    message_end_time: int
    channel_message_counts: dict[int, int]


@dataclass(slots=True)
class _SummaryGroup:
    opcode: int
    start: int
    length: int


@dataclass(frozen=True, slots=True)
class _SummaryOffsetInfo:
    record_offset: int
    group_opcode: int
    group_start: int
    group_length: int


@dataclass(frozen=True, slots=True)
class _FooterInfo:
    offset: int
    summary_start: int
    summary_offset_start: int
    summary_crc: int
    calculated_summary_crc: int


@dataclass(slots=True)
class _ParseState:
    schemas: dict[int, _Schema] = field(default_factory=dict)
    channels: dict[int, _Channel] = field(default_factory=dict)
    topics: dict[str, _TopicState] = field(default_factory=dict)
    findings: list[VerificationFinding] = field(default_factory=list)
    profile: str = ""
    library: str = ""
    record_count: int = 0
    message_count: int = 0
    header_seen: bool = False
    data_end_seen: bool = False
    footer_seen: bool = False
    data_crc: int = 0
    summary_crc: int = 0
    data_schema_ids: set[int] = field(default_factory=set)
    data_channel_ids: set[int] = field(default_factory=set)
    channel_message_counts: dict[int, int] = field(default_factory=dict)
    message_time_min: int | None = None
    message_time_max: int | None = None
    chunks: dict[int, _ChunkInfo] = field(default_factory=dict)
    chunk_indexes: list[_ChunkIndexInfo] = field(default_factory=list)
    message_indexes: dict[int, _MessageIndexInfo] = field(default_factory=dict)
    attachments: dict[int, _AttachmentInfo] = field(default_factory=dict)
    attachment_indexes: list[_AttachmentIndexInfo] = field(default_factory=list)
    metadata_records: dict[int, _MetadataInfo] = field(default_factory=dict)
    metadata_indexes: list[_MetadataIndexInfo] = field(default_factory=list)
    statistics: list[_StatisticsInfo] = field(default_factory=list)
    summary_groups: list[_SummaryGroup] = field(default_factory=list)
    summary_group_opcodes: set[int] = field(default_factory=set)
    summary_offsets: list[_SummaryOffsetInfo] = field(default_factory=list)
    summary_offset_phase: bool = False
    footer: _FooterInfo | None = None
    current_chunk_offset: int | None = None


class _Cursor:
    def __init__(self, data: bytes | bytearray | memoryview) -> None:
        self.data = memoryview(data)
        self.pos = 0

    def take(self, size: int) -> memoryview:
        if size < 0:
            raise ValueError("negative field size")
        end = self.pos + size
        if end > len(self.data):
            raise ValueError("record payload is truncated")
        value = self.data[self.pos : end]
        self.pos = end
        return value

    def u8(self) -> int:
        return int(struct.unpack("<B", self.take(1))[0])

    def u16(self) -> int:
        return int(struct.unpack("<H", self.take(2))[0])

    def u32(self) -> int:
        return int(struct.unpack("<I", self.take(4))[0])

    def u64(self) -> int:
        return int(struct.unpack("<Q", self.take(8))[0])

    def string(self) -> str:
        return self.take(self.u32()).tobytes().decode("utf-8")

    def byte_array(self) -> bytes:
        return self.take(self.u32()).tobytes()

    def string_map(self) -> dict[str, str]:
        length = self.u32()
        end = self.pos + length
        if end > len(self.data):
            raise ValueError("map payload is truncated")
        result: dict[str, str] = {}
        while self.pos < end:
            key = self.string()
            value = self.string()
            if self.pos > end:
                raise ValueError("map entry exceeds declared map length")
            if key in result:
                raise ValueError(f"map contains duplicate key {key!r}")
            result[key] = value
        if self.pos != end:
            raise ValueError("map length does not end on an entry boundary")
        return result

    def rest(self) -> memoryview:
        return self.take(len(self.data) - self.pos)

    def finish(self) -> None:
        if self.pos != len(self.data):
            raise ValueError(f"record has {len(self.data) - self.pos} trailing bytes")


class _CountingReader:
    def __init__(self, source: ReadableBinaryStream) -> None:
        self._source = source
        self.count = 0

    def read(self, size: int = -1) -> bytes:
        data = self._source.read(size)
        if not isinstance(data, bytes):
            raise TypeError("object storage reader must return bytes")
        self.count += len(data)
        return data

    def close(self) -> None:
        self._source.close()


class McapVerifier:
    """Verify MCAP framing, indexes, CRCs, inventory, and one sample per topic."""

    def __init__(
        self,
        storage: ReadableObjectStorage,
        decoder: DecoderProbe | None = None,
        *,
        chunk_decompressor: ChunkDecompressor | None = None,
        max_record_size: int = 128 * 1024 * 1024,
        max_chunk_size: int = 256 * 1024 * 1024,
        read_block_size: int = 1024 * 1024,
    ) -> None:
        if max_record_size < 1 or max_chunk_size < 1 or read_block_size < 1:
            raise ValueError("verification size limits must be positive")
        self._storage = storage
        self._decoder = decoder
        self._chunk_decompressor = chunk_decompressor or McapSdkChunkDecompressor()
        self._max_record_size = max_record_size
        self._max_chunk_size = max_chunk_size
        self._read_block_size = read_block_size

    def verify(
        self,
        *,
        rollout_id: str,
        object_key: str,
        source_sha256: str,
        required_topics: set[str],
        known_optional_topics: set[str] | None = None,
    ) -> RawVerificationReportV1:
        return self.verify_stream(
            rollout_id=rollout_id,
            object_key=object_key,
            source_sha256=source_sha256,
            required_topics=required_topics,
            known_optional_topics=known_optional_topics,
            stream=self._storage.open_reader(object_key),
        )

    def verify_stream(
        self,
        *,
        rollout_id: str,
        object_key: str,
        source_sha256: str,
        required_topics: set[str],
        known_optional_topics: set[str] | None = None,
        stream: ReadableBinaryStream,
    ) -> RawVerificationReportV1:
        """Verify an already-open stream without performing another object-store GET."""

        state = _ParseState()
        with closing(_CountingReader(stream)) as counted:
            try:
                self._parse_stream(counted, state)
            except _MalformedMcap as exc:
                self._add_finding(state, exc.code, str(exc), offset=exc.offset)
                self._drain(counted)
            object_size = counted.count

        self._finalize_findings(state, required_topics, known_optional_topics or set())
        findings = tuple(sorted(state.findings, key=self._finding_key))
        status = (
            VerificationStatus.REJECTED
            if any(item.severity == VerificationSeverity.ERROR for item in findings)
            else VerificationStatus.VERIFIED
        )
        return RawVerificationReportV1.build(
            rollout_id=rollout_id,
            object_key=object_key,
            source_sha256=source_sha256,
            object_size=object_size,
            profile=state.profile,
            library=state.library,
            record_count=state.record_count,
            message_count=state.message_count,
            schemas=len(state.schemas),
            channels=len(state.channels),
            chunks=len(state.chunks),
            schema_inventory=tuple(self._schema_inventory(state)),
            channel_inventory=tuple(self._channel_inventory(state)),
            topics=tuple(self._topic_inventory(state)),
            findings=findings,
            status=status,
        )

    def _parse_stream(self, stream: _CountingReader, state: _ParseState) -> None:
        magic = self._read_exact(stream, len(MCAP_MAGIC), 0, VerificationCode.MISSING_MAGIC)
        if magic != MCAP_MAGIC:
            raise _MalformedMcap(VerificationCode.MISSING_MAGIC, "leading MCAP magic is missing", 0)
        state.data_crc = zlib.crc32(magic)

        while True:
            record_offset = stream.count
            opcode_raw = stream.read(1)
            if not opcode_raw:
                code = (
                    VerificationCode.FOOTER_MISSING
                    if state.data_end_seen
                    else VerificationCode.TRUNCATED
                )
                message = (
                    "footer record is missing"
                    if state.data_end_seen
                    else "unexpected EOF before DataEnd"
                )
                raise _MalformedMcap(code, message, record_offset)
            opcode = opcode_raw[0]
            length_raw = self._read_exact(stream, 8, stream.count, VerificationCode.TRUNCATED)
            length = struct.unpack("<Q", length_raw)[0]
            if length > self._max_record_size:
                raise _MalformedMcap(
                    VerificationCode.RECORD_TOO_LARGE,
                    f"record length {length} exceeds configured limit {self._max_record_size}",
                    record_offset,
                )
            payload = self._read_exact(stream, length, stream.count, VerificationCode.TRUNCATED)
            total_length = 9 + length
            state.record_count += 1

            if not state.data_end_seen:
                if opcode != DATA_END:
                    state.data_crc = zlib.crc32(opcode_raw, state.data_crc)
                    state.data_crc = zlib.crc32(length_raw, state.data_crc)
                    state.data_crc = zlib.crc32(payload, state.data_crc)
            elif opcode != FOOTER:
                state.summary_crc = zlib.crc32(opcode_raw, state.summary_crc)
                state.summary_crc = zlib.crc32(length_raw, state.summary_crc)
                state.summary_crc = zlib.crc32(payload, state.summary_crc)

            try:
                self._consume_outer_record(opcode, payload, record_offset, total_length, state)
            except (UnicodeDecodeError, ValueError, struct.error) as exc:
                if opcode in _INDEX_RECORDS:
                    code = VerificationCode.INDEX_INVALID
                elif state.data_end_seen and opcode != FOOTER:
                    code = VerificationCode.SUMMARY_INVALID
                else:
                    code = VerificationCode.RECORD_INVALID
                raise _MalformedMcap(code, str(exc), record_offset) from exc

            if opcode == FOOTER:
                trailing = self._read_exact(
                    stream,
                    len(MCAP_MAGIC),
                    stream.count,
                    VerificationCode.TRAILING_MAGIC_MISSING,
                )
                if trailing != MCAP_MAGIC:
                    raise _MalformedMcap(
                        VerificationCode.TRAILING_MAGIC_MISSING,
                        "footer is not followed by the final MCAP magic",
                        stream.count - len(MCAP_MAGIC),
                    )
                if stream.read(1):
                    raise _MalformedMcap(
                        VerificationCode.TRAILING_MAGIC_MISSING,
                        "trailing bytes follow the final MCAP magic",
                        stream.count - 1,
                    )
                return

    def _consume_outer_record(
        self,
        opcode: int,
        payload: bytearray,
        offset: int,
        total_length: int,
        state: _ParseState,
    ) -> None:
        if state.record_count == 1 and opcode != HEADER:
            raise _MalformedMcap(
                VerificationCode.HEADER_MISSING,
                "Header must be the first record after leading magic",
                offset,
            )
        if opcode == HEADER:
            if state.header_seen or state.record_count != 1:
                raise ValueError("Header must occur exactly once as the first record")
            cursor = _Cursor(payload)
            state.profile = cursor.string()
            state.library = cursor.string()
            cursor.finish()
            state.header_seen = True
            return
        if opcode == FOOTER:
            self._consume_footer(payload, offset, state)
            return
        if not state.data_end_seen:
            self._consume_data_record(opcode, payload, offset, total_length, state)
            return

        if opcode == SUMMARY_OFFSET:
            state.summary_offset_phase = True
            self._consume_summary_offset(payload, offset, state)
            return
        if state.summary_offset_phase:
            raise ValueError("summary record occurs after the Summary Offset section began")
        if opcode in _SUMMARY_RECORDS or opcode >= 0x80:
            self._track_summary_group(opcode, offset, total_length, state)
            self._consume_summary_record(opcode, payload, offset, state)
            return
        raise ValueError(f"opcode 0x{opcode:02x} is not allowed after DataEnd")

    def _consume_data_record(
        self,
        opcode: int,
        payload: bytearray,
        offset: int,
        total_length: int,
        state: _ParseState,
    ) -> None:
        if opcode == SCHEMA:
            state.current_chunk_offset = None
            self._consume_schema(payload, state, in_data=True)
        elif opcode == CHANNEL:
            state.current_chunk_offset = None
            self._consume_channel(payload, state, in_data=True)
        elif opcode == MESSAGE:
            state.current_chunk_offset = None
            self._consume_message(payload, state)
        elif opcode == CHUNK:
            self._consume_chunk(payload, offset, total_length, state)
            state.current_chunk_offset = offset
        elif opcode == MESSAGE_INDEX:
            if state.current_chunk_offset is None:
                raise ValueError("Message Index does not immediately follow a Chunk")
            self._consume_message_index(
                payload,
                offset,
                total_length,
                state.current_chunk_offset,
                state,
            )
        elif opcode == ATTACHMENT:
            state.current_chunk_offset = None
            self._consume_attachment(payload, offset, total_length, state)
        elif opcode == METADATA:
            state.current_chunk_offset = None
            self._consume_metadata(payload, offset, total_length, state)
        elif opcode == DATA_END:
            state.current_chunk_offset = None
            cursor = _Cursor(payload)
            expected_crc = cursor.u32()
            cursor.finish()
            if expected_crc and expected_crc != state.data_crc:
                self._add_finding(
                    state,
                    VerificationCode.DATA_SECTION_CRC_MISMATCH,
                    "data section CRC32 does not match",
                    offset=offset,
                )
            state.data_end_seen = True
        elif opcode in _SUMMARY_RECORDS or opcode == SUMMARY_OFFSET:
            raise ValueError("summary or index record occurs before DataEnd")
        elif opcode < 0x80:
            state.current_chunk_offset = None
            # Unknown standard opcodes are skipped for binary forward compatibility.
            return
        else:
            state.current_chunk_offset = None

    def _consume_summary_record(
        self, opcode: int, payload: bytearray, offset: int, state: _ParseState
    ) -> None:
        if opcode == SCHEMA:
            self._consume_schema(payload, state, in_data=False)
        elif opcode == CHANNEL:
            self._consume_channel(payload, state, in_data=False)
        elif opcode == CHUNK_INDEX:
            self._consume_chunk_index(payload, offset, state)
        elif opcode == ATTACHMENT_INDEX:
            self._consume_attachment_index(payload, offset, state)
        elif opcode == METADATA_INDEX:
            self._consume_metadata_index(payload, offset, state)
        elif opcode == STATISTICS:
            self._consume_statistics(payload, offset, state)

    def _consume_schema(
        self, payload: bytearray | memoryview, state: _ParseState, *, in_data: bool
    ) -> None:
        cursor = _Cursor(payload)
        schema_id = cursor.u16()
        if schema_id == 0:
            raise ValueError("Schema id 0 is reserved")
        schema = _Schema(schema_id, cursor.string(), cursor.string(), cursor.byte_array())
        cursor.finish()
        existing = state.schemas.get(schema_id)
        if existing is not None and existing != schema:
            raise ValueError(f"Schema id {schema_id} has conflicting definitions")
        state.schemas[schema_id] = schema
        if in_data:
            state.data_schema_ids.add(schema_id)

    def _consume_channel(
        self, payload: bytearray | memoryview, state: _ParseState, *, in_data: bool
    ) -> None:
        cursor = _Cursor(payload)
        channel_id = cursor.u16()
        schema_id = cursor.u16()
        topic = cursor.string()
        message_encoding = cursor.string()
        cursor.string_map()
        cursor.finish()
        channel = _Channel(channel_id, schema_id, topic, message_encoding)
        existing = state.channels.get(channel_id)
        if existing is not None and existing != channel:
            raise ValueError(f"Channel id {channel_id} has conflicting definitions")
        state.channels[channel_id] = channel
        topic_state = state.topics.setdefault(topic, _TopicState())
        topic_state.channel_ids.add(channel_id)
        if in_data:
            state.data_channel_ids.add(channel_id)

    def _consume_message(
        self, payload: bytearray | memoryview, state: _ParseState
    ) -> tuple[int, int]:
        cursor = _Cursor(payload)
        channel_id = cursor.u16()
        cursor.u32()  # sequence
        log_time = cursor.u64()
        cursor.u64()  # publish time
        data = cursor.rest()
        channel = state.channels.get(channel_id)
        if channel is None or channel_id not in state.data_channel_ids:
            raise ValueError(f"Message references Channel {channel_id} before its data definition")
        topic = state.topics[channel.topic]
        topic.observe(channel_id, log_time, data)
        state.message_count += 1
        state.channel_message_counts[channel_id] = (
            state.channel_message_counts.get(channel_id, 0) + 1
        )
        state.message_time_min = (
            log_time if state.message_time_min is None else min(state.message_time_min, log_time)
        )
        state.message_time_max = (
            log_time if state.message_time_max is None else max(state.message_time_max, log_time)
        )
        return channel_id, log_time

    def _consume_chunk(
        self,
        payload: bytearray,
        offset: int,
        total_length: int,
        state: _ParseState,
    ) -> None:
        cursor = _Cursor(payload)
        message_start_time = cursor.u64()
        message_end_time = cursor.u64()
        uncompressed_size = cursor.u64()
        expected_crc = cursor.u32()
        compression = cursor.string()
        compressed_size = cursor.u64()
        records = cursor.take(compressed_size)
        cursor.finish()
        chunk = _ChunkInfo(
            offset=offset,
            total_length=total_length,
            message_start_time=message_start_time,
            message_end_time=message_end_time,
            compression=compression,
            compressed_size=compressed_size,
            uncompressed_size=uncompressed_size,
        )
        if offset in state.chunks:
            raise ValueError(f"duplicate Chunk offset {offset}")
        state.chunks[offset] = chunk

        if uncompressed_size > self._max_chunk_size:
            self._add_finding(
                state,
                VerificationCode.CHUNK_TOO_LARGE,
                f"chunk expands to {uncompressed_size} bytes, above {self._max_chunk_size}",
                offset=offset,
            )
            return
        if not compression:
            decoded: bytes | memoryview = records
        elif compression not in _KNOWN_COMPRESSIONS:
            self._add_finding(
                state,
                VerificationCode.UNSUPPORTED_COMPRESSION,
                f"unsupported chunk compression {compression!r}",
                offset=offset,
            )
            return
        else:
            try:
                decoded = self._chunk_decompressor.decompress(
                    compression=compression,
                    data=records.tobytes(),
                    uncompressed_size=uncompressed_size,
                )
            except RuntimeError as exc:
                self._add_finding(
                    state,
                    VerificationCode.UNSUPPORTED_COMPRESSION,
                    str(exc),
                    offset=offset,
                )
                return
            except Exception as exc:  # codec libraries use implementation-specific errors
                self._add_finding(
                    state,
                    VerificationCode.CHUNK_DECOMPRESSION_FAILED,
                    f"failed to decompress {compression!r} chunk: {exc}",
                    offset=offset,
                )
                return

        if len(decoded) != uncompressed_size:
            message = (
                f"chunk declares {uncompressed_size} uncompressed bytes but produced {len(decoded)}"
            )
            self._add_finding(
                state,
                VerificationCode.CHUNK_SIZE_MISMATCH,
                message,
                offset=offset,
            )
            return
        if expected_crc and (zlib.crc32(decoded) & 0xFFFFFFFF) != expected_crc:
            self._add_finding(
                state,
                VerificationCode.CHUNK_CRC_MISMATCH,
                "uncompressed chunk CRC32 does not match",
                offset=offset,
            )
            return
        self._consume_chunk_records(memoryview(decoded), chunk, state)

    def _consume_chunk_records(
        self, records: memoryview, chunk: _ChunkInfo, state: _ParseState
    ) -> None:
        cursor = _Cursor(records)
        observed_min: int | None = None
        observed_max: int | None = None
        while cursor.pos < len(cursor.data):
            nested_offset = cursor.pos
            opcode = cursor.u8()
            length = cursor.u64()
            if length > self._max_record_size:
                raise ValueError(f"nested record length {length} exceeds configured limit")
            payload = cursor.take(length)
            state.record_count += 1
            if opcode == SCHEMA:
                self._consume_schema(payload, state, in_data=True)
            elif opcode == CHANNEL:
                self._consume_channel(payload, state, in_data=True)
            elif opcode == MESSAGE:
                channel_id, log_time = self._consume_message(payload, state)
                offsets = chunk.message_offsets.setdefault(channel_id, {})
                offsets[nested_offset] = log_time
                observed_min = log_time if observed_min is None else min(observed_min, log_time)
                observed_max = log_time if observed_max is None else max(observed_max, log_time)
            elif opcode < 0x80:
                # Future chunk-contained records are length-delimited and safe to skip.
                continue
        if observed_min is None:
            if chunk.message_start_time or chunk.message_end_time:
                raise ValueError("empty Chunk must declare a zero message time range")
        elif (chunk.message_start_time, chunk.message_end_time) != (observed_min, observed_max):
            raise ValueError("Chunk message time range does not match traversed messages")

    def _consume_message_index(
        self,
        payload: bytearray,
        offset: int,
        total_length: int,
        chunk_offset: int,
        state: _ParseState,
    ) -> None:
        cursor = _Cursor(payload)
        channel_id = cursor.u16()
        records_length = cursor.u32()
        if records_length % 16:
            raise ValueError("Message Index entries length must be divisible by 16")
        end = cursor.pos + records_length
        entries: list[tuple[int, int]] = []
        while cursor.pos < end:
            entries.append((cursor.u64(), cursor.u64()))
        cursor.finish()
        if offset in state.message_indexes:
            raise ValueError(f"duplicate Message Index offset {offset}")
        state.message_indexes[offset] = _MessageIndexInfo(
            offset=offset,
            total_length=total_length,
            chunk_offset=chunk_offset,
            channel_id=channel_id,
            entries=tuple(entries),
        )

    def _consume_chunk_index(self, payload: bytearray, offset: int, state: _ParseState) -> None:
        cursor = _Cursor(payload)
        message_start_time = cursor.u64()
        message_end_time = cursor.u64()
        chunk_start_offset = cursor.u64()
        chunk_length = cursor.u64()
        offsets_length = cursor.u32()
        if offsets_length % 10:
            raise ValueError("Chunk Index message offsets length must be divisible by 10")
        offsets_end = cursor.pos + offsets_length
        message_index_offsets: dict[int, int] = {}
        while cursor.pos < offsets_end:
            channel_id = cursor.u16()
            if channel_id in message_index_offsets:
                raise ValueError(f"Chunk Index repeats Channel {channel_id}")
            message_index_offsets[channel_id] = cursor.u64()
        message_index_length = cursor.u64()
        compression = cursor.string()
        compressed_size = cursor.u64()
        uncompressed_size = cursor.u64()
        cursor.finish()
        state.chunk_indexes.append(
            _ChunkIndexInfo(
                offset=offset,
                message_start_time=message_start_time,
                message_end_time=message_end_time,
                chunk_start_offset=chunk_start_offset,
                chunk_length=chunk_length,
                message_index_offsets=message_index_offsets,
                message_index_length=message_index_length,
                compression=compression,
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
            )
        )

    def _consume_attachment(
        self,
        payload: bytearray,
        offset: int,
        total_length: int,
        state: _ParseState,
    ) -> None:
        cursor = _Cursor(payload)
        log_time = cursor.u64()
        create_time = cursor.u64()
        name = cursor.string()
        media_type = cursor.string()
        data_size = cursor.u64()
        cursor.take(data_size)
        expected_crc = cursor.u32()
        cursor.finish()
        if expected_crc and zlib.crc32(memoryview(payload)[:-4]) != expected_crc:
            raise ValueError("Attachment CRC32 does not match")
        state.attachments[offset] = _AttachmentInfo(
            total_length, log_time, create_time, data_size, name, media_type
        )

    def _consume_attachment_index(
        self, payload: bytearray, offset: int, state: _ParseState
    ) -> None:
        cursor = _Cursor(payload)
        info = _AttachmentIndexInfo(
            offset=offset,
            target_offset=cursor.u64(),
            target_length=cursor.u64(),
            log_time=cursor.u64(),
            create_time=cursor.u64(),
            data_size=cursor.u64(),
            name=cursor.string(),
            media_type=cursor.string(),
        )
        cursor.finish()
        state.attachment_indexes.append(info)

    def _consume_metadata(
        self,
        payload: bytearray,
        offset: int,
        total_length: int,
        state: _ParseState,
    ) -> None:
        cursor = _Cursor(payload)
        name = cursor.string()
        cursor.string_map()
        cursor.finish()
        state.metadata_records[offset] = _MetadataInfo(total_length=total_length, name=name)

    def _consume_metadata_index(self, payload: bytearray, offset: int, state: _ParseState) -> None:
        cursor = _Cursor(payload)
        info = _MetadataIndexInfo(
            offset=offset,
            target_offset=cursor.u64(),
            target_length=cursor.u64(),
            name=cursor.string(),
        )
        cursor.finish()
        state.metadata_indexes.append(info)

    def _consume_statistics(self, payload: bytearray, offset: int, state: _ParseState) -> None:
        cursor = _Cursor(payload)
        message_count = cursor.u64()
        schema_count = cursor.u16()
        channel_count = cursor.u32()
        attachment_count = cursor.u32()
        metadata_count = cursor.u32()
        chunk_count = cursor.u32()
        message_start_time = cursor.u64()
        message_end_time = cursor.u64()
        counts_length = cursor.u32()
        if counts_length % 10:
            raise ValueError("Statistics channel counts length must be divisible by 10")
        counts_end = cursor.pos + counts_length
        channel_counts: dict[int, int] = {}
        while cursor.pos < counts_end:
            channel_id = cursor.u16()
            if channel_id in channel_counts:
                raise ValueError(f"Statistics repeats Channel {channel_id}")
            channel_counts[channel_id] = cursor.u64()
        cursor.finish()
        state.statistics.append(
            _StatisticsInfo(
                offset=offset,
                message_count=message_count,
                schema_count=schema_count,
                channel_count=channel_count,
                attachment_count=attachment_count,
                metadata_count=metadata_count,
                chunk_count=chunk_count,
                message_start_time=message_start_time,
                message_end_time=message_end_time,
                channel_message_counts=channel_counts,
            )
        )

    def _consume_summary_offset(self, payload: bytearray, offset: int, state: _ParseState) -> None:
        cursor = _Cursor(payload)
        info = _SummaryOffsetInfo(
            record_offset=offset,
            group_opcode=cursor.u8(),
            group_start=cursor.u64(),
            group_length=cursor.u64(),
        )
        cursor.finish()
        state.summary_offsets.append(info)

    def _consume_footer(self, payload: bytearray, offset: int, state: _ParseState) -> None:
        if state.footer_seen:
            raise ValueError("Footer occurs more than once")
        cursor = _Cursor(payload)
        summary_start = cursor.u64()
        summary_offset_start = cursor.u64()
        summary_crc = cursor.u32()
        cursor.finish()
        prefix = struct.pack("<BQQQ", FOOTER, 20, summary_start, summary_offset_start)
        calculated = zlib.crc32(prefix, state.summary_crc)
        state.footer = _FooterInfo(
            offset=offset,
            summary_start=summary_start,
            summary_offset_start=summary_offset_start,
            summary_crc=summary_crc,
            calculated_summary_crc=calculated,
        )
        state.footer_seen = True

    @staticmethod
    def _track_summary_group(
        opcode: int, offset: int, total_length: int, state: _ParseState
    ) -> None:
        if state.summary_groups and state.summary_groups[-1].opcode == opcode:
            state.summary_groups[-1].length += total_length
            return
        if opcode in state.summary_group_opcodes:
            raise ValueError(f"Summary opcode 0x{opcode:02x} is not grouped contiguously")
        state.summary_group_opcodes.add(opcode)
        state.summary_groups.append(_SummaryGroup(opcode, offset, total_length))

    def _finalize_findings(
        self,
        state: _ParseState,
        required_topics: set[str],
        known_optional_topics: set[str],
    ) -> None:
        if not state.header_seen and not self._has_code(state, VerificationCode.HEADER_MISSING):
            self._add_finding(state, VerificationCode.HEADER_MISSING, "Header record is missing")
        if not state.data_end_seen and not self._has_code(state, VerificationCode.DATA_END_MISSING):
            self._add_finding(state, VerificationCode.DATA_END_MISSING, "DataEnd record is missing")
        if not state.footer_seen and not self._has_code(state, VerificationCode.FOOTER_MISSING):
            self._add_finding(state, VerificationCode.FOOTER_MISSING, "Footer record is missing")
        if state.footer_seen:
            self._validate_summary(state)
            self._validate_indexes(state)
            self._validate_statistics(state)

        for topic in sorted(required_topics - state.topics.keys()):
            self._add_finding(
                state,
                VerificationCode.REQUIRED_TOPIC_MISSING,
                "required topic is absent",
                topic=topic,
            )
        for topic in sorted(state.topics):
            item = state.topics[topic]
            if topic not in required_topics and topic not in known_optional_topics:
                self._add_finding(
                    state,
                    VerificationCode.UNKNOWN_OPTIONAL_TOPIC,
                    "unknown optional topic retained in inventory",
                    severity=VerificationSeverity.WARNING,
                    topic=topic,
                )
            if item.message_count == 0 or item.sample_channel_id is None or item.sample is None:
                self._add_finding(
                    state,
                    VerificationCode.TOPIC_EMPTY,
                    "topic has no message to decode",
                    topic=topic,
                )
                continue
            channel = state.channels[item.sample_channel_id]
            schema = state.schemas.get(channel.schema_id)
            if schema is None:
                self._add_finding(
                    state,
                    VerificationCode.SCHEMA_MISSING,
                    "channel schema is absent",
                    topic=topic,
                )
                continue
            if self._decoder is None:
                self._add_finding(
                    state,
                    VerificationCode.DECODE_FAILED,
                    "decoder probe is not configured",
                    topic=topic,
                )
                continue
            item.decode_checked = True
            if not self._decoder.supports(channel.message_encoding, schema.encoding):
                item.decodable = False
                self._add_finding(
                    state,
                    VerificationCode.DECODE_FAILED,
                    "no decoder supports the schema pair",
                    topic=topic,
                )
                continue
            try:
                self._decoder.probe(
                    message_encoding=channel.message_encoding,
                    schema_encoding=schema.encoding,
                    schema_name=schema.name,
                    schema_data=schema.data,
                    message_data=item.sample,
                )
            except (TypeError, ValueError) as exc:
                item.decodable = False
                self._add_finding(state, VerificationCode.DECODE_FAILED, str(exc), topic=topic)
            else:
                item.decodable = True

    def _validate_summary(self, state: _ParseState) -> None:
        footer = state.footer
        if footer is None:
            return
        expected_summary_start = state.summary_groups[0].start if state.summary_groups else 0
        expected_offset_start = (
            state.summary_offsets[0].record_offset if state.summary_offsets else 0
        )
        if footer.summary_start != expected_summary_start:
            message = (
                f"Footer summary_start {footer.summary_start} "
                f"does not match {expected_summary_start}"
            )
            self._add_finding(
                state,
                VerificationCode.SUMMARY_INVALID,
                message,
                offset=footer.offset,
            )
        if footer.summary_offset_start != expected_offset_start:
            self._add_finding(
                state,
                VerificationCode.SUMMARY_INVALID,
                "Footer summary_offset_start does not match the Summary Offset section",
                offset=footer.offset,
            )
        if state.summary_offsets:
            entries: dict[int, _SummaryOffsetInfo] = {}
            for offset_entry in state.summary_offsets:
                if offset_entry.group_opcode in entries:
                    self._add_finding(
                        state,
                        VerificationCode.INDEX_INVALID,
                        f"duplicate Summary Offset for opcode 0x{offset_entry.group_opcode:02x}",
                        offset=offset_entry.record_offset,
                    )
                entries[offset_entry.group_opcode] = offset_entry
            for group in state.summary_groups:
                group_entry = entries.get(group.opcode)
                if group_entry is None or (group_entry.group_start, group_entry.group_length) != (
                    group.start,
                    group.length,
                ):
                    self._add_finding(
                        state,
                        VerificationCode.INDEX_INVALID,
                        f"Summary Offset does not describe opcode 0x{group.opcode:02x} group",
                        offset=footer.offset,
                    )
            actual_opcodes = {group.opcode for group in state.summary_groups}
            for opcode, offset_entry in entries.items():
                if opcode not in actual_opcodes and offset_entry.group_length != 0:
                    self._add_finding(
                        state,
                        VerificationCode.INDEX_INVALID,
                        f"Summary Offset targets absent opcode 0x{opcode:02x}",
                        offset=offset_entry.record_offset,
                    )
        if footer.summary_crc and footer.summary_crc != footer.calculated_summary_crc:
            self._add_finding(
                state,
                VerificationCode.SUMMARY_CRC_MISMATCH,
                "summary section CRC32 does not match",
                offset=footer.offset,
            )

    def _validate_indexes(self, state: _ParseState) -> None:
        for message_index in state.message_indexes.values():
            message_chunk = state.chunks[message_index.chunk_offset]
            self._validate_message_index_entries(message_index, message_chunk, state)

        referenced_chunks: set[int] = set()
        for chunk_index in state.chunk_indexes:
            chunk = state.chunks.get(chunk_index.chunk_start_offset)
            if chunk is None:
                self._add_finding(
                    state,
                    VerificationCode.INDEX_INVALID,
                    f"Chunk Index references missing Chunk at {chunk_index.chunk_start_offset}",
                    offset=chunk_index.offset,
                )
                continue
            if chunk.offset in referenced_chunks:
                self._add_finding(
                    state,
                    VerificationCode.INDEX_INVALID,
                    f"multiple Chunk Index records reference Chunk at {chunk.offset}",
                    offset=chunk_index.offset,
                )
            referenced_chunks.add(chunk.offset)
            expected = (
                chunk.message_start_time,
                chunk.message_end_time,
                chunk.total_length,
                chunk.compression,
                chunk.compressed_size,
                chunk.uncompressed_size,
            )
            observed = (
                chunk_index.message_start_time,
                chunk_index.message_end_time,
                chunk_index.chunk_length,
                chunk_index.compression,
                chunk_index.compressed_size,
                chunk_index.uncompressed_size,
            )
            if observed != expected:
                self._add_finding(
                    state,
                    VerificationCode.INDEX_INVALID,
                    f"Chunk Index fields do not match Chunk at {chunk.offset}",
                    offset=chunk_index.offset,
                )
            self._validate_message_indexes(chunk_index, chunk, state)
        if state.chunk_indexes and referenced_chunks != set(state.chunks):
            self._add_finding(
                state,
                VerificationCode.INDEX_INVALID,
                "Chunk Index summary does not cover every Chunk",
            )

        for attachment_index in state.attachment_indexes:
            attachment = state.attachments.get(attachment_index.target_offset)
            expected_attachment: tuple[int, int, int, int, str, str] | None = None
            if attachment is not None:
                expected_attachment = (
                    attachment.total_length,
                    attachment.log_time,
                    attachment.create_time,
                    attachment.data_size,
                    attachment.name,
                    attachment.media_type,
                )
            observed_attachment = (
                attachment_index.target_length,
                attachment_index.log_time,
                attachment_index.create_time,
                attachment_index.data_size,
                attachment_index.name,
                attachment_index.media_type,
            )
            if expected_attachment != observed_attachment:
                message = (
                    "Attachment Index references invalid record at "
                    f"{attachment_index.target_offset}"
                )
                self._add_finding(
                    state,
                    VerificationCode.INDEX_INVALID,
                    message,
                    offset=attachment_index.offset,
                )
        for metadata_index in state.metadata_indexes:
            metadata = state.metadata_records.get(metadata_index.target_offset)
            if metadata is None or (metadata_index.target_length, metadata_index.name) != (
                metadata.total_length,
                metadata.name,
            ):
                self._add_finding(
                    state,
                    VerificationCode.INDEX_INVALID,
                    f"Metadata Index references invalid record at {metadata_index.target_offset}",
                    offset=metadata_index.offset,
                )

    def _validate_message_indexes(
        self, index: _ChunkIndexInfo, chunk: _ChunkInfo, state: _ParseState
    ) -> None:
        message_indexes: list[_MessageIndexInfo] = []
        for channel_id, index_offset in sorted(index.message_index_offsets.items()):
            message_index = state.message_indexes.get(index_offset)
            if message_index is None or message_index.channel_id != channel_id:
                self._add_finding(
                    state,
                    VerificationCode.INDEX_INVALID,
                    f"Chunk Index references invalid Message Index at {index_offset}",
                    offset=index.offset,
                )
                continue
            message_indexes.append(message_index)
        message_indexes.sort(key=lambda item: item.offset)
        if message_indexes:
            expected_offset = chunk.offset + chunk.total_length
            for message_index in message_indexes:
                if message_index.offset != expected_offset:
                    self._add_finding(
                        state,
                        VerificationCode.INDEX_INVALID,
                        "Message Index records are not contiguous after their Chunk",
                        offset=index.offset,
                    )
                    break
                expected_offset += message_index.total_length
            actual_length = sum(item.total_length for item in message_indexes)
        else:
            actual_length = 0
        if index.message_index_length != actual_length:
            self._add_finding(
                state,
                VerificationCode.INDEX_INVALID,
                "Chunk Index message_index_length does not match referenced records",
                offset=index.offset,
            )

    def _validate_message_index_entries(
        self,
        message_index: _MessageIndexInfo,
        chunk: _ChunkInfo,
        state: _ParseState,
    ) -> None:
        expected_offsets = chunk.message_offsets.get(message_index.channel_id, {})
        for log_time, message_offset in message_index.entries:
            if expected_offsets.get(message_offset) != log_time:
                message = (
                    "Message Index entry does not reference a traversed "
                    f"Channel {message_index.channel_id} message"
                )
                self._add_finding(
                    state,
                    VerificationCode.INDEX_INVALID,
                    message,
                    offset=message_index.offset,
                )
                break

    def _validate_statistics(self, state: _ParseState) -> None:
        if len(state.statistics) > 1:
            self._add_finding(
                state,
                VerificationCode.SUMMARY_INVALID,
                "Summary contains more than one Statistics record",
            )
        if not state.statistics:
            return
        info = state.statistics[0]
        expected = (
            state.message_count,
            len(state.data_schema_ids),
            len(state.data_channel_ids),
            len(state.attachments),
            len(state.metadata_records),
            len(state.chunks),
            state.message_time_min or 0,
            state.message_time_max or 0,
        )
        observed = (
            info.message_count,
            info.schema_count,
            info.channel_count,
            info.attachment_count,
            info.metadata_count,
            info.chunk_count,
            info.message_start_time,
            info.message_end_time,
        )
        if observed != expected or (
            info.channel_message_counts
            and info.channel_message_counts != state.channel_message_counts
        ):
            self._add_finding(
                state,
                VerificationCode.SUMMARY_INVALID,
                "Statistics record does not match traversed data",
                offset=info.offset,
            )

    @staticmethod
    def _schema_inventory(state: _ParseState) -> list[SchemaInventoryV1]:
        return [
            SchemaInventoryV1(
                schema_id=schema.schema_id,
                name=schema.name,
                encoding=schema.encoding,
                data_size=len(schema.data),
                data_sha256=hashlib.sha256(schema.data).hexdigest(),
            )
            for _, schema in sorted(state.schemas.items())
        ]

    @staticmethod
    def _channel_inventory(state: _ParseState) -> list[ChannelInventoryV1]:
        return [
            ChannelInventoryV1(
                channel_id=channel.channel_id,
                schema_id=channel.schema_id,
                topic=channel.topic,
                message_encoding=channel.message_encoding,
            )
            for _, channel in sorted(state.channels.items())
        ]

    @staticmethod
    def _topic_inventory(state: _ParseState) -> list[TopicInventoryV1]:
        result: list[TopicInventoryV1] = []
        for topic, item in sorted(state.topics.items()):
            channel_id = item.sample_channel_id or min(item.channel_ids)
            channel = state.channels[channel_id]
            schema = state.schemas.get(channel.schema_id)
            result.append(
                TopicInventoryV1(
                    topic=topic,
                    channel_ids=tuple(sorted(item.channel_ids)),
                    message_encoding=channel.message_encoding,
                    schema_name=schema.name if schema else None,
                    schema_encoding=schema.encoding if schema else None,
                    message_count=item.message_count,
                    log_time_min_ns=item.log_time_min_ns,
                    log_time_max_ns=item.log_time_max_ns,
                    decode_checked=item.decode_checked,
                    decodable=item.decodable,
                )
            )
        return result

    def _read_exact(
        self,
        stream: _CountingReader,
        size: int,
        offset: int,
        code: VerificationCode,
    ) -> bytearray:
        result = bytearray()
        while len(result) < size:
            chunk = stream.read(min(size - len(result), self._read_block_size))
            if not chunk:
                raise _MalformedMcap(code, f"expected {size} bytes", offset)
            result.extend(chunk)
        return result

    def _drain(self, stream: _CountingReader) -> None:
        while stream.read(self._read_block_size):
            pass

    @staticmethod
    def _add_finding(
        state: _ParseState,
        code: VerificationCode,
        message: str,
        *,
        severity: VerificationSeverity = VerificationSeverity.ERROR,
        topic: str | None = None,
        offset: int | None = None,
    ) -> None:
        finding = VerificationFinding(
            code=code,
            severity=severity,
            message=message,
            topic=topic,
            record_offset=offset,
        )
        if finding not in state.findings:
            state.findings.append(finding)

    @staticmethod
    def _has_code(state: _ParseState, code: VerificationCode) -> bool:
        return any(finding.code == code for finding in state.findings)

    @staticmethod
    def _finding_key(item: VerificationFinding) -> tuple[str, str, int, str]:
        offset = item.record_offset if item.record_offset is not None else -1
        return (item.code.value, item.topic or "", offset, item.message)
