"""Production adapters kept behind BE-05's small streaming contracts."""

from __future__ import annotations

from typing import Any


class McapSdkChunkDecompressor:
    """Use the official MCAP SDK only for registered chunk compression codecs.

    Container framing and index validation intentionally remain in ``McapVerifier``.
    Imports are lazy so uncompressed MCAP verification does not require the data extra.
    """

    def decompress(
        self,
        *,
        compression: str,
        data: bytes,
        uncompressed_size: int,
    ) -> bytes:
        try:
            from mcap.records import Chunk
            from mcap.stream_reader import get_chunk_data_stream
        except ImportError as exc:  # pragma: no cover - exercised without the data extra
            raise RuntimeError("install the 'data' extra to read compressed MCAP chunks") from exc

        chunk: Any = Chunk(
            compression=compression,
            data=data,
            message_end_time=0,
            message_start_time=0,
            uncompressed_crc=0,
            uncompressed_size=uncompressed_size,
        )
        stream, decoded_size = get_chunk_data_stream(chunk, validate_crc=False)
        return bytes(stream.read(decoded_size))
