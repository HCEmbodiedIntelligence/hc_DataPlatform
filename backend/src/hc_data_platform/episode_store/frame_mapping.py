"""Resolve committed rows to source evidence, including historical sparse packages."""

import pyarrow as pa
import pyarrow.parquet as pq

SOURCE_MAP = "hc/source_frames.parquet"
SOURCE_ARCHIVE = "hc/recording/aligned_source_frames.parquet"


def aligned_mapping(mapping, data, info):
    if mapping.num_rows != data.num_rows:
        if (
            info.get("hc.frame_map_version") == 2
            or "hc.source_frame_index" not in data.column_names
        ):
            raise ValueError("source frame map does not cover committed rows")
        mapping = mapping.take(data["hc.source_frame_index"])
    if mapping.num_rows != data.num_rows:
        raise ValueError("source frame map row count mismatch")
    mapping = mapping.set_column(
        mapping.schema.get_field_index("frame_index"),
        "frame_index",
        pa.array(range(data.num_rows), type=pa.int64()),
    )
    return mapping


def package_mapping(package, data):
    with package.file(SOURCE_MAP) as path:
        return aligned_mapping(pq.read_table(path), data, package.document("meta/info.json"))


def replace_column(table, name, values, dtype):
    column = pa.array(values, type=dtype)
    index = table.schema.get_field_index(name)
    return table.append_column(name, column) if index < 0 else table.set_column(index, name, column)
