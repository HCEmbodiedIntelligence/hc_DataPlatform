"""Compatibility exports. New callers use owned models/ports and adapter modules.

Resolving a contract does not initialize unrelated services, SDKs or workers.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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

_EXPORTS = {
    "McapSdkChunkDecompressor": (".adapters", "McapSdkChunkDecompressor"),
    "McapVerifier": (".engine", "McapVerifier"),
    "ChannelInventoryV1": (".models", "ChannelInventoryV1"),
    "RawVerificationReportV1": (".models", "RawVerificationReportV1"),
    "RawVerifiedV1": (".models", "RawVerifiedV1"),
    "SchemaInventoryV1": (".models", "SchemaInventoryV1"),
    "VerificationCode": (".models", "VerificationCode"),
    "VerificationFinding": (".models", "VerificationFinding"),
    "VerificationSeverity": (".models", "VerificationSeverity"),
    "VerificationStatus": (".models", "VerificationStatus"),
    "ChunkDecompressor": (".ports", "ChunkDecompressor"),
    "ChunkedObjectStorageReader": (".ports", "ChunkedObjectStorageReader"),
    "ChunkReadableObjectStorage": (".ports", "ChunkReadableObjectStorage"),
    "CompositeDecoderProbe": (".ports", "CompositeDecoderProbe"),
    "DecoderProbe": (".ports", "DecoderProbe"),
    "FakeDecoderProbe": (".ports", "FakeDecoderProbe"),
    "FakeObjectStorage": (".ports", "FakeObjectStorage"),
    "McapRos2DecoderProbe": (".ports", "McapRos2DecoderProbe"),
    "RawVerificationPort": (".ports", "RawVerificationPort"),
    "ReadableBinaryStream": (".ports", "ReadableBinaryStream"),
    "ReadableObjectStorage": (".ports", "ReadableObjectStorage"),
    "RegisteredDecoderProbe": (".ports", "RegisteredDecoderProbe"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module, attribute = target
    value = getattr(import_module(module, __name__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
