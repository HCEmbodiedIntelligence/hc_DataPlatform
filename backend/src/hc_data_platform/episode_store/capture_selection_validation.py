"""Read-only capture selection checks, independent of package writing and media edits."""

import pyarrow.parquet as pq

from hc_data_platform.data_formats.lerobot_layout import DATA_PATH

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
        if row["hc.sample_valid"]
    )
    selected_rows = (
        row for batch in data.iter_batches(batch_size=1024) for row in batch.to_pylist()
    )
    source_key = info["hc.feature_mapping"]["source.timestamp_ns"]
    for index, (before, after) in enumerate(zip_longest(source_rows, selected_rows)):
        if before is None or after is None:
            raise ValueError("capture selection omitted or invented usable rows")
        if (
            after["hc.video_frame_index"] != (index if compacted else before["frame_index"])
            or compacted
            and after["hc.source_frame_index"] != before["frame_index"]
            or after["hc.alignment_timestamp_ns"] != before["hc.timestamp_ns"]
            or after["hc.timestamp_ns"] != before[source_key]
        ):
            raise ValueError("capture selection changed source time or media address")
        for key, value in before.items():
            if (
                key not in {"index", "frame_index", "timestamp", "hc.timestamp_ns"}
                and after[key] != value
            ):
                raise ValueError(f"capture selection shifted or changed a source value: {key}")
