"""Compact every modality and evidence map with one ordered retained-row list."""

import hashlib
import json
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from hc_data_platform.data_formats.lerobot_layout import DATA_PATH, EPISODES_PATH

from .capture_selection_validation import ARCHIVE_PATH as ARCHIVE_PATH
from .capture_selection_validation import validate_capture_selection as validate_capture_selection
from .frame_mapping import SOURCE_ARCHIVE, SOURCE_MAP, aligned_mapping, replace_column
from .frame_policy import POLICY as POLICY
from .frame_policy import PROCESSING_POLICY, refresh_repetition, source_stamp, usable_row
from .models import canonical
from .statistics import refresh_numeric_statistics


def archive_current(root, source, destination):
    """A second derivation retains the previous evidence inside its own attempt."""
    target = root / destination
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        ancestor = root / "hc/recording/ancestors" / (digest + "-" + target.name)
        ancestor.parent.mkdir(parents=True, exist_ok=True)
        target.replace(ancestor)
    (root / source).replace(target)


def compact_capture_rows(root: Path):
    """Only used in unpublished attempts; original files and clocks stay intact."""
    root = Path(root)
    info = json.loads((root / "meta/info.json").read_bytes())
    source_key = info["hc.feature_mapping"].get("source.timestamp_ns", "hc.timestamp_ns")
    original = pq.ParquetFile(root / DATA_PATH)
    map_columns = [
        n for n in ("frame_index", "hc.source_frame_index") if n in original.schema_arrow.names
    ]
    source_map = aligned_mapping(
        pq.read_table(root / SOURCE_MAP), original.read(columns=map_columns), info
    )
    original_count = original.metadata.num_rows
    selected, stamps, pending = [], [], []
    invalid_modalities = Counter()
    previous, repetitions = {}, []
    schema = original.schema_arrow
    for name, dtype in (
        ("timestamp", pa.float64()),
        ("hc.video_frame_index", pa.int64()),
        ("hc.video_timestamp", pa.float64()),
        ("hc.alignment_timestamp_ns", pa.int64()),
    ):
        index = schema.get_field_index(name)
        field = pa.field(name, dtype)
        schema = schema.append(field) if index < 0 else schema.set(index, field)
    temporary_data = (root / DATA_PATH).with_suffix(".compact.parquet")
    rows = (r for batch in original.iter_batches(batch_size=1024) for r in batch.to_pylist())
    with pq.ParquetWriter(temporary_data, schema) as output:
        for parent_index, row in enumerate(rows):
            if row["frame_index"] != parent_index:
                raise ValueError("input frame indices must be dense before selection")
            if not usable_row(row):
                invalid_modalities.update(
                    k for k, v in row.items() if k.startswith("hc.valid.") and not v
                )
                continue
            stamp = source_stamp(row, source_key)
            if stamps and stamp <= stamps[-1]:
                raise ValueError("retained source timestamps must strictly increase")
            index = len(selected)
            refresh_repetition(row, previous)
            repetitions.append(
                {
                    name: row.get("hc.repeated." + target, False)
                    for name, target in info["hc.feature_mapping"].items()
                }
            )
            selected.append(parent_index)
            stamps.append(stamp)
            row.update(
                {
                    "index": index,
                    "frame_index": index,
                    "timestamp": (stamp - stamps[0]) / 1e9,
                    "hc.video_frame_index": parent_index,
                    "hc.video_timestamp": parent_index / info["fps"],
                    "hc.alignment_timestamp_ns": row["hc.timestamp_ns"],
                    "hc.timestamp_ns": stamp,
                }
            )
            pending.append(row)
            if len(pending) == 1024:
                output.write_table(pa.Table.from_pylist(pending, schema=schema))
                pending.clear()
        if pending:
            output.write_table(pa.Table.from_pylist(pending, schema=schema))
    if not selected:
        raise ValueError(
            f"capture selection contains no usable observations: {dict(invalid_modalities)}"
        )
    archive_current(root, DATA_PATH, ARCHIVE_PATH)
    temporary_data.replace(root / DATA_PATH)
    # Archive a map in the same input grid as aligned_rows, repairing historical
    # sparse maps, then create exactly one map row per retained observation.
    if (root / SOURCE_ARCHIVE).exists():
        archive_current(root, SOURCE_ARCHIVE, "hc/recording/previous_source_frames.parquet")
    pq.write_table(source_map, root / SOURCE_ARCHIVE)
    mapped = source_map.take(pa.array(selected, type=pa.int64()))
    mapped = replace_column(mapped, "parent_frame_index", selected, pa.int64())
    mapped = replace_column(mapped, "frame_index", range(len(selected)), pa.int64())
    mapped = replace_column(mapped, "timestamp_ns", stamps, pa.int64())
    mapped = replace_column(
        mapped, "repeated", [canonical(r).decode() for r in repetitions], pa.string()
    )
    pq.write_table(mapped, root / SOURCE_MAP)
    # Parent/source coordinates remain unchanged; only output indices move.
    for relative, index_column in (
        ("hc/annotation_source_frames.parquet", "output_frame_index"),
        ("hc/derivation/frames.parquet", "frame_index"),
    ):
        path = root / relative
        if path.is_file():
            table = pq.read_table(path)
            if table.num_rows != original_count:
                raise ValueError(f"source mapping does not cover input rows: {relative}")
            table = table.take(pa.array(selected, type=pa.int64()))
            table = replace_column(table, index_column, range(len(selected)), pa.int64())
            pq.write_table(table, path)
    count = len(selected)
    info.update(
        {
            "total_frames": count,
            "hc.media_frame_count": original_count,
            "hc.time_origin_ns": stamps[0],
            "hc.duration_seconds": (stamps[-1] - stamps[0]) / 1e9 + 1 / info["fps"],
            "hc.training_policy": POLICY,
            "hc.frame_selection_policy": PROCESSING_POLICY,
            "hc.frame_map_version": 2,
            "hc.capture_timestamp_column": source_key,
        }
    )
    info.pop("hc.media_selection", None)
    info["features"]["timestamp"]["dtype"] = "float64"
    for key, dtype in (
        ("hc.video_frame_index", "int64"),
        ("hc.video_timestamp", "float64"),
        ("hc.alignment_timestamp_ns", "int64"),
    ):
        info["features"][key] = {"dtype": dtype, "shape": [1], "names": None}
    episode = pq.read_table(root / EPISODES_PATH).to_pylist()[0]
    episode.update(length=count, dataset_from_index=0, dataset_to_index=count)
    pq.write_table(pa.Table.from_pylist([episode]), root / EPISODES_PATH)
    selection = {
        "schema_version": "hc-training-selection/v2",
        "policy": POLICY,
        "source_row_count": original_count,
        "row_count": count,
        "removed_row_count": original_count - count,
        "selected_frame_indices": selected,
        "source_time_origin_ns": stamps[0],
        "invalid_modalities": dict(invalid_modalities),
        "media_timestamp_column": "hc.video_timestamp",
    }
    (root / "meta/info.json").write_bytes(canonical(info))
    (root / "hc/training_selection.json").write_bytes(canonical(selection))
    from .capture_media import compact_capture_media

    compact_capture_media(root)
    refresh_numeric_statistics(root)
