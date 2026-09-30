"""Verified HEVC depth windows without another physical-depth quantization.

Callers supply an internally authorized PreparedVideo and a frozen frame window.
Whole streams keep their original MP4 bytes. Cuts/resampling encode the selected
12-bit codes losslessly, retain pre-quantization masks and original sensor times,
and record the parent video/evidence hashes. This module does not colorize depth.
"""

import hashlib
import shutil
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path

import av
import pyarrow as pa
import pyarrow.parquet as pq

from hc_data_platform.data_formats.depth_evidence import (
    file_hash,
    missing_measurement_status,
    write_json,
)
from hc_data_platform.data_formats.holobrain_depth import DepthProfile, DepthVideo

from .frame_window import DepthWindow
from .models import FileEntry, digest
from .prepared_assets import PreparedVideo
from .validation import document, package_file, parquet_rows, validate_depth

VERSION = "hc-depth-window/2"
PARENT_TIME_VERSION = "hc-depth-parent-window/1"
REQUIRED = {"profile.json", "frames.parquet", "report.json", "validity/flags.parquet"}


def _verify_source(media):
    if media.evidence_root is None or not media.feature.get("info", {}).get("is_depth_map"):
        raise ValueError("depth window requires an encoded depth evidence contract")
    entries = {entry.path: entry for entry in media.evidence_files}
    if len(entries) != len(media.evidence_files) or not entries.keys() >= REQUIRED:
        raise ValueError("depth window evidence inventory is incomplete")
    package_file(media.path.parent, media.path.name)
    if media.path.stat().st_size != media.size or file_hash(media.path) != media.sha256:
        raise ValueError("depth video differs from authorized source hash")
    for relative, entry in entries.items():
        path = package_file(media.evidence_root, relative)
        if path.stat().st_size != entry.size or file_hash(path) != entry.sha256:
            raise ValueError("depth evidence differs from authorized source hash")
    report = document(media.evidence_root, "report.json")
    fps = Fraction(str(media.feature["info"]["video.fps"]))
    if fps <= 0:
        raise ValueError("source depth frame rate must be positive")
    validate_depth(
        media.evidence_root.parent,
        media.evidence_root.name,
        media.path,
        media.feature,
        report["frames"],
        float(fps),
        target_rows=(
            {"hc.timestamp_ns": row["target_timestamp_ns"]}
            for row in parquet_rows(media.evidence_root / "frames.parquet")
        ),
        feature_key="depth",
    )
    return entries, report, fps


def prepare_depth_window(
    media: PreparedVideo, window: DepthWindow, destination: Path, *, preserve_parent_times=False
) -> PreparedVideo:
    entries, original_report, source_fps = _verify_source(media)
    if preserve_parent_times and (
        Fraction(str(window.frequency_hz)) != source_fps
        or window.frame_count != window.stop_source_frame - window.first_source_frame
        or window.source_start_offset_ns is not None
    ):
        raise ValueError("parent timestamps require consecutive frames without resampling")
    source_count = original_report["frames"]
    if (
        window.stop_source_frame > source_count
        or window.source_frame(0, source_fps) != window.first_source_frame
        or window.source_frame(window.frame_count - 1, source_fps) >= window.stop_source_frame
    ):
        raise ValueError("depth output requests a frame outside the frozen source window")
    whole = (
        window.first_source_frame == 0
        and window.stop_source_frame == source_count
        and window.frame_count == source_count
        and Fraction(str(window.frequency_hz)) == source_fps
        and window.source_start_offset_ns in (None, 0)
    )
    if whole and (
        preserve_parent_times
        or all(
            row["target_timestamp_ns"] == window.target_time(index)
            for index, row in enumerate(parquet_rows(media.evidence_root / "frames.parquet"))
        )
    ):
        # The existing verified media/evidence already satisfies the entire contract.
        # No codec instance or regenerated evidence is needed.
        return media
    # Attempt output must be new and separate from the immutable input tree.
    destination = Path(destination)
    if (
        destination.resolve() == media.evidence_root.resolve()
        or media.evidence_root.resolve() in destination.resolve().parents
    ):
        raise ValueError("depth output must not modify the source evidence tree")
    destination.mkdir(parents=True, exist_ok=False)
    root = destination / "evidence"
    (root / "validity").mkdir(parents=True)
    video = destination / "depth.mp4"
    declaration = document(media.evidence_root, "profile.json")
    profile = DepthProfile(
        **{
            key: declaration[key]
            for key in ("depth_min", "depth_max", "shift", "use_log", "version")
        }
    )
    declaration["feature_info"]["video.fps"] = window.frequency_hz
    write_json(root / "profile.json", declaration)
    original_schema = pq.read_schema(media.evidence_root / "frames.parquet")
    # Parent metadata is explicit even when this is a window of an earlier window.
    fields = [
        field
        for field in original_schema
        if field.name not in {"parent_frame_index", "parent_target_timestamp_ns"}
    ]
    schema = pa.schema(
        [*fields, ("parent_frame_index", pa.int64()), ("parent_target_timestamp_ns", pa.int64())]
    )
    mask_schema = pq.read_schema(media.evidence_root / "validity/flags.parquet")
    source_rows = parquet_rows(media.evidence_root / "frames.parquet")
    source_masks = parquet_rows(media.evidence_root / "validity/flags.parquet")
    count, source_index, previous = 0, -1, None
    expected_codes = hashlib.sha256()
    totals = dict.fromkeys(original_report["totals"], 0)
    max_error = None
    encoder = (
        None if whole else DepthVideo(video, window.frequency_hz, media.feature["shape"], profile)
    )
    pending_rows, pending_masks = [], []
    try:
        with (
            av.open(str(media.path)) as container,
            pq.ParquetWriter(root / "frames.parquet", schema) as rows_out,
            pq.ParquetWriter(root / "validity/flags.parquet", mask_schema) as masks_out,
        ):
            decoded = container.decode(video=0)
            for index in range(window.frame_count):
                selected = window.source_frame(index, source_fps)
                while source_index < selected:
                    try:
                        frame, row, mask = next(decoded), next(source_rows), next(source_masks)
                    except StopIteration as exc:
                        raise ValueError("verified depth source changed during selection") from exc
                    source_index += 1
                pixels = frame.to_ndarray(format="gray12le")
                expected_codes.update(pixels.astype("<u2").tobytes())
                if encoder is not None:
                    encoder.write_codes(pixels)
                target = (
                    row["target_timestamp_ns"]
                    if preserve_parent_times
                    else window.target_time(index)
                )
                missing = row["missing"]
                identity = None if missing else (row["source_id"], row["source_seq"])
                error = None if missing else row["source_timestamp_ns"] - target
                threshold = row["max_time_error_ns"]
                output = {
                    **row,
                    "frame_index": index,
                    "parent_frame_index": selected,
                    "parent_target_timestamp_ns": row["target_timestamp_ns"],
                    "target_timestamp_ns": target,
                    "time_error_ns": error,
                    "time_threshold_exceeded": error is not None
                    and threshold is not None
                    and abs(error) > threshold,
                    "repeated": identity is not None and identity == previous,
                }
                previous = identity
                pending_rows.append(output)
                pending_masks.append({**mask, "frame_index": index})
                for key in totals.keys() & row.keys():
                    totals[key] += row[key]
                totals["missing_frames"] += int(missing)
                totals["repeated_frames"] += int(output["repeated"])
                value = row["unclipped_max_error_m"]
                if value is not None:
                    max_error = value if max_error is None else max(max_error, value)
                count += 1
                if len(pending_rows) == 128:
                    rows_out.write_table(pa.Table.from_pylist(pending_rows, schema=schema))
                    masks_out.write_table(pa.Table.from_pylist(pending_masks, schema=mask_schema))
                    pending_rows.clear()
                    pending_masks.clear()
            if pending_rows:
                rows_out.write_table(pa.Table.from_pylist(pending_rows, schema=schema))
                masks_out.write_table(pa.Table.from_pylist(pending_masks, schema=mask_schema))
    finally:
        if encoder is not None:
            encoder.close()
    if whole:
        shutil.copyfile(media.path, video)
    observed, output_count = hashlib.sha256(), 0
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        actual_profile = stream.codec_context.profile
        if (stream.codec_context.name, stream.codec_context.format.name) != ("hevc", "gray12le"):
            raise ValueError("depth window changed codec or monochrome precision")
        for frame in container.decode(stream):
            observed.update(frame.to_ndarray(format="gray12le").astype("<u2").tobytes())
            output_count += 1
    if output_count != count or observed.digest() != expected_codes.digest():
        raise ValueError("depth window HEVC encoding changed selected 12-bit codes")
    mapping = {
        "processor_version": PARENT_TIME_VERSION if preserve_parent_times else VERSION,
        "window": asdict(window),
        "source_fps": str(source_fps),
        "source_frames": source_count,
        "source_video_sha256": media.sha256,
        "source_evidence": {key: entry.sha256 for key, entry in sorted(entries.items())},
        "operation": "byte_copy" if whole else "lossless_code_selection",
        "requantized": False,
        "encoder_configuration_sha256": original_report["video_encoding"].get(
            "encoder_configuration_sha256"
        )
        if whole
        else encoder.encoding_metadata()["encoder_configuration_sha256"],
    }
    if preserve_parent_times:
        mapping["timestamp_policy"] = "exact_parent_targets"
    report = {
        **original_report,
        "frames": count,
        "totals": totals,
        **missing_measurement_status(totals, count),
        "quantization": {
            **original_report["quantization"],
            "unclipped_max_error_m": max_error,
            "source_error_carried_forward": True,
        },
        "video_encoding": {
            **original_report["video_encoding"],
            "codes_sha256": observed.hexdigest(),
            "video_sha256": file_hash(video),
            "codec_profile": actual_profile,
            # A byte copy retains the *source encoder's* versions/options. The
            # current process only decoded/verified it; it did not encode it.
            **({} if whole else encoder.encoding_metadata()),
        },
        "window": {**mapping, "configuration_sha256": digest(mapping)},
    }
    write_json(root / "report.json", report)
    feature = {**media.feature, "info": {**media.feature["info"], "video.fps": window.frequency_hz}}
    validate_depth(
        root.parent,
        root.name,
        video,
        feature,
        count,
        window.frequency_hz,
        target_rows=(
            {"hc.timestamp_ns": row["target_timestamp_ns"]}
            for row in parquet_rows(root / "frames.parquet")
        )
        if preserve_parent_times
        else ({"hc.timestamp_ns": window.target_time(i)} for i in range(count)),
        feature_key="depth",
    )
    return PreparedVideo(
        video,
        file_hash(video),
        video.stat().st_size,
        feature,
        root,
        tuple(
            FileEntry(
                path=path.relative_to(root).as_posix(),
                size=path.stat().st_size,
                sha256=file_hash(path),
                media_type="application/octet-stream",
            )
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ),
    )
