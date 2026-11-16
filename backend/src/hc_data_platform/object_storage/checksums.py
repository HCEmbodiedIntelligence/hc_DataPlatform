"""Stable incremental object checksums and ETag normalization."""

import ctypes
import ctypes.util
from functools import lru_cache
from typing import Any

_CRC64_MASK = 0xFFFFFFFFFFFFFFFF
_CRC64_XZ_POLYNOMIAL = 0xC96C5795D7870F42


def _build_crc64_table() -> tuple[int, ...]:
    values: list[int] = []
    for index in range(256):
        value = index
        for _ in range(8):
            value = (value >> 1) ^ _CRC64_XZ_POLYNOMIAL if value & 1 else value >> 1
        values.append(value & _CRC64_MASK)
    return tuple(values)


_CRC64_TABLE = _build_crc64_table()


@lru_cache(maxsize=1)
def _native_crc64() -> Any:
    """Use liblzma's CRC-64/XZ when present; portable installations fall back."""
    try:
        library = ctypes.util.find_library("lzma")
        if library is None:
            return None
        function = ctypes.CDLL(library).lzma_crc64
        function.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint64]
        function.restype = ctypes.c_uint64
        return function
    except (AttributeError, OSError):
        return None


def crc64_ecma(data: bytes, crc: int = 0) -> int:
    """Return Alibaba OSS-compatible CRC-64/XZ, supporting incremental calls.

    The public ``crc`` is the finalized value from a previous call. Un-finalizing it here
    makes ``crc64_ecma(chunk_b, crc64_ecma(chunk_a))`` equal the one-shot checksum.
    """

    native = _native_crc64()
    if native is not None and isinstance(data, bytes):
        return int(native(data, len(data), crc))
    accumulator = crc ^ _CRC64_MASK
    for byte in data:
        accumulator = _CRC64_TABLE[(accumulator ^ byte) & 0xFF] ^ (accumulator >> 8)
    return (accumulator ^ _CRC64_MASK) & _CRC64_MASK


def normalize_etag(etag: str) -> str:
    return etag.strip().strip('"')
