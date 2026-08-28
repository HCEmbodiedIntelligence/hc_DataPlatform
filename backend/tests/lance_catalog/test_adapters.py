from __future__ import annotations

import pyarrow as pa

from hc_data_platform.lance_catalog.adapters import _arrow_table_hash


def test_logical_arrow_hash_is_independent_of_record_batch_boundaries() -> None:
    schema = pa.schema(
        [
            pa.field("step_index", pa.int64(), nullable=False),
            pa.field("payload", pa.binary(), nullable=False),
            pa.field("score", pa.float64(), nullable=False),
        ]
    )
    rows = [
        {"step_index": index, "payload": f"frame-{index}".encode(), "score": index / 10}
        for index in range(10_000)
    ]
    one_chunk = pa.Table.from_pylist(rows, schema=schema)
    many_chunks = pa.Table.from_batches(
        [
            pa.RecordBatch.from_pylist(rows[offset : offset + 257], schema=schema)
            for offset in range(0, len(rows), 257)
        ]
    )

    assert one_chunk.num_rows == many_chunks.num_rows
    assert one_chunk.num_columns == many_chunks.num_columns
    assert _arrow_table_hash(one_chunk) == _arrow_table_hash(many_chunks)
