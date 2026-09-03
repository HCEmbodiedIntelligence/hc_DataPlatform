"""Streaming MCAP container verification (BE-05)."""

from .adapters import McapSdkChunkDecompressor
from .engine import McapVerifier
from .models import (
    ChannelInventoryV1,
    RawVerificationReportV1,
    RawVerifiedV1,
    SchemaInventoryV1,
    VerificationCode,
    VerificationFinding,
    VerificationSeverity,
    VerificationStatus,
)
from .ports import (
    ChunkDecompressor,
    ChunkedObjectStorageReader,
    ChunkReadableObjectStorage,
    CompositeDecoderProbe,
    DecoderProbe,
    FakeDecoderProbe,
    FakeObjectStorage,
    McapRos2DecoderProbe,
    RawVerificationPort,
    ReadableBinaryStream,
    ReadableObjectStorage,
    RegisteredDecoderProbe,
)

__all__ = [
    "ChannelInventoryV1",
    "ChunkDecompressor",
    "ChunkReadableObjectStorage",
    "ChunkedObjectStorageReader",
    "CompositeDecoderProbe",
    "DecoderProbe",
    "FakeDecoderProbe",
    "FakeObjectStorage",
    "McapRos2DecoderProbe",
    "McapVerifier",
    "McapSdkChunkDecompressor",
    "RawVerificationPort",
    "RawVerificationReportV1",
    "RawVerifiedV1",
    "ReadableBinaryStream",
    "ReadableObjectStorage",
    "RegisteredDecoderProbe",
    "SchemaInventoryV1",
    "VerificationCode",
    "VerificationFinding",
    "VerificationSeverity",
    "VerificationStatus",
]
