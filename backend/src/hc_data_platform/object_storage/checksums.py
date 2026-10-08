"""Stable incremental object checksums and ETag normalization."""

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


def crc64_ecma(data: bytes, crc: int = 0) -> int:
    """Return Alibaba OSS-compatible CRC-64/XZ, supporting incremental calls.

    The public ``crc`` is the finalized value from a previous call. Un-finalizing it here
    makes ``crc64_ecma(chunk_b, crc64_ecma(chunk_a))`` equal the one-shot checksum.
    """

    accumulator = crc ^ _CRC64_MASK
    for byte in data:
        accumulator = _CRC64_TABLE[(accumulator ^ byte) & 0xFF] ^ (accumulator >> 8)
    return (accumulator ^ _CRC64_MASK) & _CRC64_MASK


def normalize_etag(etag: str) -> str:
    return etag.strip().strip('"')
