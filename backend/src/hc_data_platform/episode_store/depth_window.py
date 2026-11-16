"""Verified HEVC depth windows without another physical-depth quantization.

Callers supply an internally authorized PreparedVideo and a frozen frame window.
Whole streams keep their original MP4 bytes. Cuts/resampling copy the selected
all-intra HEVC pictures, or encode the 12-bit codes losslessly otherwise. Both
retain pre-quantization masks and original sensor times,
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

from hc_data_platform.core.ingest_timing import timed
from hc_data_platform.data_formats.depth_evidence import (
    file_hash,
    missing_measurement_status,
    write_json,
)
from hc_data_platform.data_formats.hevc_remux import NotIntraCoded, remux_hevc_frames
from hc_data_platform.data_formats.holobrain_depth import DepthProfile, DepthVideo

from .frame_window import DepthWindow
from .models import FileEntry, digest
from .prepared_assets import PreparedVideo
from .validation import document, package_file, parquet_rows, validate_depth

VERSION = "hc-depth-window/3"
PARENT_TIME_VERSION = "hc-depth-parent-window/1"
REQUIRED = {"profile.json", "frames.parquet", "report.json", "validity/flags.parquet"}


def _verify_source(media, select_codes=None):
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
        allow_missing_reference=True,
        select_codes=select_codes,
    )
    return entries, report, fps


def _remux_selected(media, indices, video, fps):
    """Reuse already-encoded HEVC pictures when every selected frame is a
    keyframe. Returns False when the source needs a fresh encode instead."""
    try:
        remux_hevc_frames(media.path, video, indices, fps=fps)
    except NotIntraCoded:
        return False
    return True


@timed("DEPTH_WINDOW")
def prepare_depth_window(
    media: PreparedVideo, window: DepthWindow, destination: Path, *, preserve_parent_times=False, preserve_association_reference=False
) -> PreparedVideo:
    source_fps = Fraction(str(media.feature["info"]["video.fps"]))
    if preserve_parent_times and (
        Fraction(str(window.frequency_hz)) != source_fps
        or (window.source_frames is None
            and window.frame_count != window.stop_source_frame - window.first_source_frame)
        or window.source_start_offset_ns is not None
    ):
        raise ValueError("parent timestamps require explicit frame selection without resampling")
    # One source decode both verifies the evidence and hashes the codes the
    # output must contain, in selection order including repeated frames.
    selected_frames = [window.source_frame(i, source_fps) for i in range(window.frame_count)]
    expected_codes = hashlib.sha256()
    entries, original_report, source_fps = _verify_source(
        media, select_codes=(selected_frames, expected_codes)
    )
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
    if preserve_association_reference and "association_target_timestamp_ns" not in schema.names:
        schema = schema.append(pa.field("association_target_timestamp_ns", pa.int64()))
    mask_schema = pq.read_schema(media.evidence_root / "validity/flags.parquet")
    source_rows = parquet_rows(media.evidence_root / "frames.parquet")
    source_masks = parquet_rows(media.evidence_root / "validity/flags.parquet")
    count, source_index, previous = 0, -1, None
    totals = dict.fromkeys(original_report["totals"], 0)
    max_error = None
    # All-intra sources are copied picture by picture; anything else is encoded
    # again. The output decode below rejects either path on any code mismatch.
    reused = whole or _remux_selected(media, selected_frames, video, window.frequency_hz)
    encoder = (
        None if reused else DepthVideo(video, window.frequency_hz, media.feature["shape"], profile)
    )
    pending_rows, pending_masks = [], []
    try:
        with (
            av.open(str(media.path)) as container,
            pq.ParquetWriter(root / "frames.parquet", schema) as rows_out,
            pq.ParquetWriter(root / "validity/flags.parquet", mask_schema) as masks_out,
        ):
            decoded = container.decode(video=0) if encoder is not None else None
            for index in range(window.frame_count):
                selected = selected_frames[index]
                while source_index < selected:
                    try:
                        if decoded is not None:
                            frame = next(decoded)
                        row, mask = next(source_rows), next(source_masks)
                    except StopIteration as exc:
                        raise ValueError("verified depth source changed during selection") from exc
                    source_index += 1
                if encoder is not None:
                    encoder.write_codes(frame.to_ndarray(format="gray12le"))
                target = (
                    row["target_timestamp_ns"]
                    if preserve_parent_times
                    else window.target_time(index)
                )
                missing = row["missing"]
                identity = None if missing else (row["source_id"], row["source_seq"])
                error = None if missing else row["source_timestamp_ns"] - target
                threshold = row["max_time_error_ns"]
                association_target = row.get('association_target_timestamp_ns', row['target_timestamp_ns'])
                threshold_error = (None if missing else row['source_timestamp_ns'] - association_target) if preserve_association_reference else error
                output = {
                    **row,
                    "frame_index": index,
                    "parent_frame_index": selected,
                    "parent_target_timestamp_ns": row["target_timestamp_ns"],
                    "target_timestamp_ns": target,
                    "time_error_ns": error,
                    "time_threshold_exceeded": threshold_error is not None
                    and threshold is not None
                    and abs(threshold_error) > threshold,
                    **({'association_target_timestamp_ns': association_target} if preserve_association_reference else {}),
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
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        actual_profile = stream.codec_context.profile
        if (stream.codec_context.name, stream.codec_context.format.name) != ("hevc", "gray12le"):
            raise ValueError("depth window changed codec or monochrome precision")
    # The report records the codes the selection must contain. The final
    # validate_depth decodes the output once and rejects any code, count or PTS
    # difference, so a separate output decode here would repeat that work.
    mapping = {
        "processor_version": PARENT_TIME_VERSION if preserve_parent_times else VERSION,
        "window": asdict(window),
        "source_fps": str(source_fps),
        "source_frames": source_count,
        "source_video_sha256": media.sha256,
        "source_evidence": {key: entry.sha256 for key, entry in sorted(entries.items())},
        "operation": "byte_copy" if whole
        else "lossless_picture_copy" if reused else "lossless_code_selection",
        "requantized": False,
        "encoder_configuration_sha256": original_report["video_encoding"].get(
            "encoder_configuration_sha256"
        )
        if reused
        else encoder.encoding_metadata()["encoder_configuration_sha256"],
    }
    if preserve_association_reference:
        mapping["association_policy"] = "preserve_source_rgb_reference"
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
            "codes_sha256": expected_codes.hexdigest(),
            "video_sha256": file_hash(video),
            "codec_profile": actual_profile,
            # Byte and picture copies retain the *source encoder's* versions and
            # options. The current process only verified them; it encoded nothing.
            **({} if reused else encoder.encoding_metadata()),
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
