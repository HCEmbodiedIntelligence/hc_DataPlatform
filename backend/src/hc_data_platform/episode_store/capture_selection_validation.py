"""Read-only capture selection checks, independent of package writing and media edits."""

import pyarrow.parquet as pq

from hc_data_platform.data_formats.lerobot_layout import DATA_PATH

from .frame_mapping import SOURCE_ARCHIVE, SOURCE_MAP
from .frame_policy import refresh_repetition, source_stamp, usable_row

ARCHIVE_PATH = "hc/recording/aligned_rows.parquet"


def validate_capture_selection(root, info):
    """Prove selection removed only invalid rows and never shifted state/action."""
    from itertools import zip_longest

    archive = pq.ParquetFile(root / ARCHIVE_PATH)
    data = pq.ParquetFile(root / DATA_PATH)
    compacted = info.get("hc.media_selection") == "selected-rows-cfr/v1"
    if archive.metadata.num_rows != info.get("hc.source_frame_count", info["hc.media_frame_count"]):
        raise ValueError("capture archive count differs from declared media grid")
    source_rows = (
        row
        for batch in archive.iter_batches(batch_size=1024)
        for row in batch.to_pylist()
        if usable_row(row)
    )
    selected_rows = (
        row for batch in data.iter_batches(batch_size=1024) for row in batch.to_pylist()
    )
    source_key = info.get(
        "hc.capture_timestamp_column",
        info["hc.feature_mapping"].get("source.timestamp_ns", "hc.timestamp_ns"),
    )
    mapped = original_maps = None
    if info.get("hc.frame_map_version") == 2:
        mapped = pq.read_table(root / SOURCE_MAP).to_pylist()
        original_maps = pq.read_table(root / SOURCE_ARCHIVE).to_pylist()
        if len(mapped) != data.metadata.num_rows or len(original_maps) != archive.metadata.num_rows:
            raise ValueError("capture source-map row counts differ from data grids")
    previous = {}
    for index, (before, after) in enumerate(zip_longest(source_rows, selected_rows)):
        if before is None or after is None:
            raise ValueError("capture selection omitted or invented usable rows")
        refresh_repetition(before, previous)
        if (
            after["hc.video_frame_index"] != (index if compacted else before["frame_index"])
            or compacted
            and after["hc.source_frame_index"]
            != before.get("hc.source_frame_index", before["frame_index"])
            or after["hc.alignment_timestamp_ns"] != before["hc.timestamp_ns"]
            or after["hc.timestamp_ns"] != source_stamp(before, source_key)
        ):
            raise ValueError("capture selection changed source time or media address")
        for key, value in before.items():
            if (
                key
                not in {
                    "index",
                    "frame_index",
                    "timestamp",
                    "hc.timestamp_ns",
                    "hc.video_frame_index",
                    "hc.video_timestamp",
                    "hc.alignment_timestamp_ns",
                }
                and after[key] != value
            ):
                raise ValueError(f"capture selection shifted or changed a source value: {key}")
        if mapped is not None:
            trace, parent = mapped[index], original_maps[before["frame_index"]]
            if (
                trace["frame_index"] != index
                or trace["parent_frame_index"] != before["frame_index"]
                or trace["timestamp_ns"] != after["hc.timestamp_ns"]
            ):
                raise ValueError("capture source frame map has shifted indices or timestamps")
            if any(
                trace[k] != v
                for k, v in parent.items()
                if k not in {"frame_index", "parent_frame_index", "timestamp_ns", "repeated"}
            ):
                raise ValueError("capture source frame map changed modality evidence")
            import json

            if json.loads(trace["repeated"]) != {
                topic: after.get("hc.repeated." + feature, False)
                for topic, feature in info["hc.feature_mapping"].items()
            }:
                raise ValueError("capture source map repetition differs from retained rows")
