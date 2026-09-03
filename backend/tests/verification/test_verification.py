from __future__ import annotations

import hashlib
import importlib
import io
import json
import struct
import tracemalloc
import zlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal, cast

import pytest
import yaml
from pydantic import ValidationError

from hc_data_platform.verification import (
    ChunkedObjectStorageReader,
    CompositeDecoderProbe,
    DecoderProbe,
    FakeDecoderProbe,
    McapRos2DecoderProbe,
    McapVerifier,
    RawVerificationReportV1,
    ReadableObjectStorage,
    RegisteredDecoderProbe,
    VerificationCode,
    VerificationSeverity,
    VerificationStatus,
)
from hc_data_platform.verification.engine import MCAP_MAGIC
from hc_data_platform.verification.ports import FakeObjectStorage


def _string(value: str) -> bytes:
    data = value.encode()
    return struct.pack("<I", len(data)) + data


def _bytes(value: bytes) -> bytes:
    return struct.pack("<I", len(value)) + value


def _record(opcode: int, payload: bytes) -> bytes:
    return bytes([opcode]) + struct.pack("<Q", len(payload)) + payload


def _message(channel_id: int, timestamp_ns: int, data: bytes = b"ok") -> bytes:
    payload = struct.pack("<HIQQ", channel_id, 1, timestamp_ns, timestamp_ns) + data
    return _record(0x05, payload)


def _mcap(
    *,
    topics: tuple[tuple[str, bytes], ...] = (("/camera", b"ok"),),
    chunk: bool = False,
    bad_crc: bool = False,
    compression: str = "",
    uncompressed_size_delta: int = 0,
) -> bytes:
    records = [
        _record(0x01, _string("ros2") + _string("test-recorder")),
        _record(
            0x03,
            struct.pack("<H", 1) + _string("Image") + _string("ros2msg") + _bytes(b"uint8[] data"),
        ),
    ]
    messages: list[bytes] = []
    for channel_id, (topic, data) in enumerate(topics, start=1):
        channel = (
            struct.pack("<HH", channel_id, 1)
            + _string(topic)
            + _string("cdr")
            + struct.pack("<I", 0)
        )
        records.append(_record(0x04, channel))
        messages.append(_message(channel_id, channel_id * 1_000_000_000, data))
    if chunk:
        nested = b"".join(messages)
        crc = (zlib.crc32(nested) + int(bad_crc)) & 0xFFFFFFFF
        chunk_payload = (
            struct.pack(
                "<QQQI",
                1_000_000_000,
                len(topics) * 1_000_000_000,
                len(nested) + uncompressed_size_delta,
                crc,
            )
            + _string(compression)
            + struct.pack("<Q", len(nested))
            + nested
        )
        records.append(_record(0x06, chunk_payload))
    else:
        records.extend(messages)
    records.extend(
        [
            _record(0x0F, struct.pack("<I", 0)),
            _record(0x02, struct.pack("<QQI", 0, 0, 0)),
        ]
    )
    return MCAP_MAGIC + b"".join(records) + MCAP_MAGIC


Compression = Literal["none", "zstd", "lz4"]


def _golden_mcap(compression: Compression = "zstd") -> bytes:
    writer_module: Any = importlib.import_module("mcap.writer")
    output = io.BytesIO()
    writer = writer_module.Writer(
        output,
        compression=getattr(writer_module.CompressionType, compression.upper()),
        enable_crcs=True,
        enable_data_crcs=True,
    )
    writer.start(profile="ros2", library="be-05-golden")
    schema_id = writer.register_schema(
        name="sensor_msgs/msg/Image",
        encoding="ros2msg",
        data=b"uint8[] data",
    )
    channel_id = writer.register_channel(
        topic="/camera",
        message_encoding="cdr",
        schema_id=schema_id,
    )
    writer.add_message(
        channel_id=channel_id,
        log_time=1_000_000_000,
        publish_time=1_000_000_010,
        data=b"first",
    )
    writer.add_message(
        channel_id=channel_id,
        log_time=2_000_000_000,
        publish_time=2_000_000_010,
        data=b"second",
    )
    writer.finish()
    return output.getvalue()


def _summaryless_golden_mcap() -> bytes:
    writer_module: Any = importlib.import_module("mcap.writer")
    output = io.BytesIO()
    writer = writer_module.Writer(
        output,
        compression=writer_module.CompressionType.NONE,
        index_types=writer_module.IndexType.NONE,
        repeat_channels=False,
        repeat_schemas=False,
        use_statistics=False,
        use_summary_offsets=False,
        enable_crcs=True,
    )
    writer.start(profile="ros2", library="be-05-summaryless-golden")
    schema_id = writer.register_schema("Image", "ros2msg", b"uint8[] data")
    channel_id = writer.register_channel("/camera", "cdr", schema_id)
    writer.add_message(channel_id, 1, b"ok", 1)
    writer.finish()
    return output.getvalue()


def _record_locations(payload: bytes | bytearray) -> list[tuple[int, int, int]]:
    result: list[tuple[int, int, int]] = []
    offset = len(MCAP_MAGIC)
    while offset + 9 <= len(payload):
        opcode = payload[offset]
        length = struct.unpack_from("<Q", payload, offset + 1)[0]
        result.append((opcode, offset, length))
        offset += 9 + length
        if opcode == 0x02:
            break
    return result


def _location(payload: bytes | bytearray, opcode: int) -> tuple[int, int]:
    return next(
        (offset, length) for item, offset, length in _record_locations(payload) if item == opcode
    )


def _verify(
    payload: bytes,
    *,
    required: set[str] | None = None,
    known_optional: set[str] | None = None,
    decoder: DecoderProbe | None = None,
    configure_decoder: bool = True,
    storage: ReadableObjectStorage | None = None,
    max_record_size: int = 128 * 1024 * 1024,
    max_chunk_size: int = 256 * 1024 * 1024,
    read_block_size: int = 1024 * 1024,
) -> RawVerificationReportV1:
    object_key = "raw/test.mcap"
    selected_storage = storage or FakeObjectStorage({object_key: payload})
    selected_decoder = decoder
    if selected_decoder is None and configure_decoder:
        selected_decoder = FakeDecoderProbe()
    return McapVerifier(
        selected_storage,
        selected_decoder,
        max_record_size=max_record_size,
        max_chunk_size=max_chunk_size,
        read_block_size=read_block_size,
    ).verify(
        rollout_id="rollout-1",
        object_key=object_key,
        source_sha256="a" * 64,
        required_topics=required if required is not None else {"/camera"},
        known_optional_topics=known_optional,
    )


@pytest.mark.parametrize(
    "compression",
    ["none", "zstd", "lz4"],
)
def test_official_sdk_golden_mcap_is_verified(compression: Compression) -> None:
    payload = _golden_mcap(compression)
    report = _verify(payload)

    assert report.status == VerificationStatus.VERIFIED
    assert report.object_size == len(payload)
    assert report.message_count == 2
    assert report.schemas == report.channels == report.chunks == 1
    assert report.schema_inventory[0].name == "sensor_msgs/msg/Image"
    assert report.channel_inventory[0].topic == "/camera"
    assert report.topics[0].message_count == 2
    assert report.topics[0].log_time_min_ns == 1_000_000_000
    assert report.topics[0].log_time_max_ns == 2_000_000_000
    assert report.topics[0].decodable is True
    assert report.findings == ()


def test_official_summaryless_golden_with_footer_crc_is_verified() -> None:
    report = _verify(_summaryless_golden_mcap())

    assert report.status == VerificationStatus.VERIFIED
    assert report.findings == ()


def test_report_is_canonical_deterministic_and_frozen() -> None:
    payload = _golden_mcap()
    original = bytes(payload)
    first = _verify(payload)
    second = _verify(payload)
    canonical = json.dumps(
        first.model_dump(mode="json", exclude={"content_sha256"}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()

    assert payload == original
    assert first.content_sha256 == second.content_sha256
    assert first.content_sha256 == hashlib.sha256(canonical).hexdigest()
    with pytest.raises(ValidationError):
        first.status = VerificationStatus.REJECTED  # type: ignore[misc]


def test_truncated_record_has_stable_code_and_actual_object_size() -> None:
    payload = _mcap()
    message_offset, _ = _location(payload, 0x05)
    truncated = payload[: message_offset + 14]
    report = _verify(truncated)

    assert report.status == VerificationStatus.REJECTED
    assert report.object_size == len(truncated)
    assert VerificationCode.TRUNCATED in {finding.code for finding in report.findings}


def test_leading_magic_is_required() -> None:
    payload = b"not-mcap" + _mcap()[len(MCAP_MAGIC) :]
    report = _verify(payload)

    assert VerificationCode.MISSING_MAGIC in {finding.code for finding in report.findings}


def test_header_and_data_end_are_required_in_their_structural_positions() -> None:
    payload = _mcap()
    header_offset, header_length = _location(payload, 0x01)
    without_header = payload[:header_offset] + payload[header_offset + 9 + header_length :]

    data_end_offset, data_end_length = _location(payload, 0x0F)
    without_data_end = payload[:data_end_offset] + payload[data_end_offset + 9 + data_end_length :]

    header_report = _verify(without_header)
    data_end_report = _verify(without_data_end)
    assert VerificationCode.HEADER_MISSING in {finding.code for finding in header_report.findings}
    assert VerificationCode.DATA_END_MISSING in {
        finding.code for finding in data_end_report.findings
    }


def test_clean_eof_without_footer_has_footer_missing_code() -> None:
    payload = _mcap()
    footer_offset, _ = _location(payload, 0x02)
    report = _verify(payload[:footer_offset])

    assert VerificationCode.FOOTER_MISSING in {finding.code for finding in report.findings}


def test_footer_requires_final_magic_and_eof() -> None:
    report = _verify(_mcap() + b"unexpected")

    assert VerificationCode.TRAILING_MAGIC_MISSING in {finding.code for finding in report.findings}


def test_corrupt_chunk_index_has_index_error_code() -> None:
    payload = bytearray(_golden_mcap())
    index_offset, _ = _location(payload, 0x08)
    struct.pack_into("<Q", payload, index_offset + 9 + 16, 1)
    report = _verify(bytes(payload))
    codes = {finding.code for finding in report.findings}

    assert VerificationCode.INDEX_INVALID in codes
    assert VerificationCode.SUMMARY_CRC_MISMATCH in codes
    assert report.status == VerificationStatus.REJECTED


def test_corrupt_message_index_has_index_error_code() -> None:
    payload = bytearray(_golden_mcap())
    index_offset, _ = _location(payload, 0x07)
    payload[index_offset + 9 + 6] ^= 0x01
    report = _verify(bytes(payload))

    assert VerificationCode.INDEX_INVALID in {finding.code for finding in report.findings}


def test_chunk_crc_and_size_are_checked() -> None:
    bad_crc = _verify(_mcap(chunk=True, bad_crc=True))
    bad_size = _verify(_mcap(chunk=True, uncompressed_size_delta=1))

    assert VerificationCode.CHUNK_CRC_MISMATCH in {finding.code for finding in bad_crc.findings}
    assert VerificationCode.CHUNK_SIZE_MISMATCH in {finding.code for finding in bad_size.findings}


def test_data_and_summary_crc_are_checked() -> None:
    data_crc_payload = bytearray(_golden_mcap())
    data_end_offset, _ = _location(data_crc_payload, 0x0F)
    data_crc_payload[data_end_offset + 9] ^= 0x01
    data_report = _verify(bytes(data_crc_payload))

    summary_crc_payload = bytearray(_golden_mcap())
    footer_offset, _ = _location(summary_crc_payload, 0x02)
    summary_crc_payload[footer_offset + 9 + 16] ^= 0x01
    summary_report = _verify(bytes(summary_crc_payload))

    assert VerificationCode.DATA_SECTION_CRC_MISMATCH in {
        finding.code for finding in data_report.findings
    }
    assert VerificationCode.SUMMARY_CRC_MISMATCH in {
        finding.code for finding in summary_report.findings
    }


def test_unknown_compression_is_an_explicit_error() -> None:
    report = _verify(_mcap(chunk=True, compression="brotli"))

    assert VerificationCode.UNSUPPORTED_COMPRESSION in {finding.code for finding in report.findings}
    assert report.status == VerificationStatus.REJECTED


def test_chunk_expansion_is_rejected_before_decompression() -> None:
    payload = _mcap(chunk=True, compression="zstd", uncompressed_size_delta=1024)
    report = _verify(payload, max_chunk_size=100)

    assert VerificationCode.CHUNK_TOO_LARGE in {finding.code for finding in report.findings}


def test_missing_required_topic_and_unknown_optional_topic_are_distinct() -> None:
    report = _verify(_mcap(topics=(("/optional", b"ok"),)), required={"/camera"})
    by_code = {finding.code: finding for finding in report.findings}

    assert by_code[VerificationCode.REQUIRED_TOPIC_MISSING].severity == VerificationSeverity.ERROR
    assert by_code[VerificationCode.UNKNOWN_OPTIONAL_TOPIC].severity == VerificationSeverity.WARNING
    assert report.status == VerificationStatus.REJECTED


def test_channel_schema_must_exist() -> None:
    payload = _mcap()
    schema_offset, schema_length = _location(payload, 0x03)
    without_schema = payload[:schema_offset] + payload[schema_offset + 9 + schema_length :]
    report = _verify(without_schema)

    assert VerificationCode.SCHEMA_MISSING in {finding.code for finding in report.findings}


def test_unknown_optional_topic_alone_is_only_a_warning() -> None:
    report = _verify(_mcap(topics=(("/extra", b"ok"),)), required=set())

    assert report.status == VerificationStatus.VERIFIED
    assert [finding.code for finding in report.findings] == [
        VerificationCode.UNKNOWN_OPTIONAL_TOPIC
    ]


def test_decoder_probes_one_message_for_every_topic() -> None:
    decoder = FakeDecoderProbe()
    report = _verify(
        _mcap(topics=(("/camera", b"first"), ("/joints", b"second"))),
        required={"/camera", "/joints"},
        decoder=decoder,
    )

    assert report.status == VerificationStatus.VERIFIED
    assert len(decoder.calls) == 2
    assert all(topic.decode_checked and topic.decodable for topic in report.topics)


def test_decoder_failure_and_missing_decoder_reject() -> None:
    failed = _verify(
        _mcap(topics=(("/camera", b"bad-payload"),)),
        decoder=FakeDecoderProbe(reject_prefix=b"bad"),
    )
    missing = _verify(_mcap(), configure_decoder=False)

    assert VerificationCode.DECODE_FAILED in {finding.code for finding in failed.findings}
    assert failed.topics[0].decodable is False
    assert VerificationCode.DECODE_FAILED in {finding.code for finding in missing.findings}


class _ChunkStore:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.requests: list[int] = []

    def read_chunks(self, key: str, chunk_size: int) -> Iterable[bytes]:
        assert key == "raw/test.mcap"
        self.requests.append(chunk_size)
        for offset in range(0, len(self.payload), chunk_size):
            yield self.payload[offset : offset + chunk_size]


@pytest.mark.parametrize("production", [False, True])
def test_fake_and_production_reader_contract(production: bool) -> None:
    payload = _mcap()
    if production:
        chunk_store = _ChunkStore(payload)
        storage: ReadableObjectStorage = ChunkedObjectStorageReader(
            chunk_store,
            read_chunk_size=17,
        )
    else:
        storage = FakeObjectStorage({"raw/test.mcap": payload})

    assert isinstance(storage, ReadableObjectStorage)
    reader = storage.open_reader("raw/test.mcap")
    assert reader.read(3) + reader.read() == payload
    reader.close()
    with pytest.raises(ValueError):
        reader.read(1)


@pytest.mark.parametrize("production", [False, True])
def test_fake_and_production_decoder_contract(production: bool) -> None:
    calls: list[tuple[bytes, bytes]] = []

    def decode(schema_data: bytes, message_data: bytes) -> object:
        calls.append((schema_data, message_data))
        return json.loads(message_data)

    decoder: DecoderProbe
    if production:
        decoder = RegisteredDecoderProbe({("json", "jsonschema"): decode})
    else:
        decoder = FakeDecoderProbe(supported={("json", "jsonschema")})

    assert isinstance(decoder, DecoderProbe)
    assert decoder.supports("json", "jsonschema")
    decoded_value = decoder.probe(
        message_encoding="json",
        schema_encoding="jsonschema",
        schema_data=b"{}",
        message_data=b'{"ok":true}',
    )
    if production:
        assert calls == [(b"{}", b'{"ok":true}')]
        assert decoded_value == {"ok": True}
    else:
        assert decoded_value == b'{"ok":true}'


def test_decoder_adapters_preserve_empty_support_and_normalize_failures() -> None:
    fake = FakeDecoderProbe(supported=set())

    def fail(schema_data: bytes, message_data: bytes) -> object:
        del schema_data, message_data
        raise RuntimeError("invalid payload")

    production = RegisteredDecoderProbe({("cdr", "ros2msg"): fail})

    assert not fake.supports("cdr", "ros2msg")
    with pytest.raises(ValueError, match="RuntimeError: invalid payload"):
        production.probe(
            message_encoding="cdr",
            schema_encoding="ros2msg",
            schema_data=b"schema",
            message_data=b"bad",
        )


def test_ros2_decoder_receives_schema_name_and_composes_with_json_decoder() -> None:
    decoded: list[tuple[str, bytes, bytes]] = []

    class Factory:
        def decoder_for(self, message_encoding: str, schema: Any):  # type: ignore[no-untyped-def]
            assert message_encoding == "cdr"

            def decode(message_data: bytes) -> object:
                decoded.append((schema.name, schema.data, message_data))
                return object()

            return decode

    ros2 = McapRos2DecoderProbe(Factory)
    composite = CompositeDecoderProbe(
        (
            RegisteredDecoderProbe({("json", "jsonschema"): lambda _schema, data: data}),
            ros2,
        )
    )

    assert composite.supports("json", "jsonschema")
    assert composite.supports("cdr", "ros2msg")
    decoded_value = composite.probe(
        message_encoding="cdr",
        schema_encoding="ros2msg",
        schema_name="sensor_msgs/msg/Image",
        schema_data=b"uint8[] data",
        message_data=b"\x00\x01\x00\x00payload",
    )

    assert decoded == [("sensor_msgs/msg/Image", b"uint8[] data", b"\x00\x01\x00\x00payload")]
    assert decoded_value is not None


class _CloseTrackingReader:
    def __init__(self, payload: bytes) -> None:
        self._reader = io.BytesIO(payload)
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self._reader.read(size)

    def close(self) -> None:
        self.closed = True
        self._reader.close()


class _CloseTrackingStorage:
    def __init__(self, reader: _CloseTrackingReader) -> None:
        self.reader = reader

    def open_reader(self, object_key: str) -> _CloseTrackingReader:
        assert object_key == "raw/test.mcap"
        return self.reader


def test_reader_without_context_manager_is_always_closed() -> None:
    reader = _CloseTrackingReader(_mcap())
    report = _verify(b"", storage=_CloseTrackingStorage(reader))

    assert report.status == VerificationStatus.VERIFIED
    assert reader.closed


class _LargeStreamingStore:
    padding_count = 10_000
    padding_record = _record(0x80, b"x" * 1024)

    def __init__(self) -> None:
        payload = _mcap()
        locations = _record_locations(payload)
        data_end_offset, _ = _location(payload, 0x0F)
        footer_offset, footer_length = _location(payload, 0x02)
        self.prefix = payload[:data_end_offset]
        self.suffix = payload[data_end_offset : footer_offset + 9 + footer_length] + MCAP_MAGIC
        self.expected_size = (
            len(self.prefix) + self.padding_count * len(self.padding_record) + len(self.suffix)
        )
        assert locations
        self.requested_chunk_size: int | None = None

    def read_chunks(self, key: str, chunk_size: int) -> Iterable[bytes]:
        assert key == "raw/test.mcap"
        self.requested_chunk_size = chunk_size
        logical_parts = (self.prefix,)
        for part in logical_parts:
            yield from self._split(part, chunk_size)
        for _ in range(self.padding_count):
            yield from self._split(self.padding_record, chunk_size)
        yield from self._split(self.suffix, chunk_size)

    @staticmethod
    def _split(part: bytes, chunk_size: int) -> Iterable[bytes]:
        for offset in range(0, len(part), chunk_size):
            yield part[offset : offset + chunk_size]


def test_large_object_peak_memory_is_bounded_by_configured_limits() -> None:
    source = _LargeStreamingStore()
    storage = ChunkedObjectStorageReader(source, read_chunk_size=2048)

    tracemalloc.start()
    report = _verify(
        b"",
        storage=cast(ReadableObjectStorage, storage),
        max_record_size=2048,
        max_chunk_size=4096,
        read_block_size=512,
    )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert report.status == VerificationStatus.VERIFIED
    assert report.object_size == source.expected_size
    assert source.requested_chunk_size == 2048
    assert peak < 4 * 1024 * 1024


def test_openapi_matches_report_and_error_code_contracts() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    document = yaml.safe_load((backend_root / "openapi" / "verification.yaml").read_text())
    schemas = document["components"]["schemas"]
    report_schema = schemas["RawVerificationReportV1"]

    assert set(report_schema["required"]) == set(RawVerificationReportV1.model_fields)
    assert set(report_schema["properties"]) == set(RawVerificationReportV1.model_fields)
    assert set(schemas["VerificationCode"]["enum"]) == {code.value for code in VerificationCode}


def test_migration_enforces_canonical_immutable_reports() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    migration = (
        backend_root / "migrations" / "verification" / "0001_verification_reports.sql"
    ).read_text()

    assert "report_json ->> 'content_sha256' = report_sha256" in migration
    assert "BEFORE UPDATE OR DELETE ON raw_verification_reports" in migration

    tenant_identity = (
        backend_root / "migrations" / "verification" / "0003_tenant_report_identity.sql"
    ).read_text()
    assert "PRIMARY KEY (project_id, region_code, report_sha256)" in tenant_identity
    assert "UNIQUE (project_id, region_code, rollout_id, source_sha256)" in tenant_identity
