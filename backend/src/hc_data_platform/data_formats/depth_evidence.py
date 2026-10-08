"""Stream depth evidence alongside HEVC, without retaining source-precision copies.

Validity PNGs are bit flags: bit 0 valid, bit 1 below range, bit 2 above range.
A missing frame has no source identity and an all-zero mask. Official LeRobot
readers do not apply this HC extension; consumers must do so explicitly.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any

import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from numpy.typing import ArrayLike, NDArray
from PIL import Image

from .depth_source import profile_hash, require_depth_source
from .holobrain_depth import DEFAULT_PROFILE, DepthProfile, DepthVideo, quantize


def file_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))


def missing_measurement_status(totals: Mapping[str, int], frames: int) -> dict[str, str]:
    """Absent observations have no quantization-error or physical-depth statistics."""
    if frames > 0 and totals["missing_frames"] == frames:
        return {"measurement_status": "NO_SOURCE_FRAMES"}
    if totals["valid_pixels"] == 0:
        return {"measurement_status": "NO_VALID_PIXELS"}
    return {}


class DepthEvidenceVideo:
    def __init__(
        self,
        path: str | Path,
        evidence_dir: str | Path,
        fps: float | Fraction,
        shape: Sequence[int],
        profile: DepthProfile = DEFAULT_PROFILE,
        *,
        require_source_profile: bool = False,
    ) -> None:
        self.path, self.root = Path(path), Path(evidence_dir)
        self.root.mkdir(parents=True, exist_ok=False)
        (self.root / "validity").mkdir()
        self.video = DepthVideo(self.path, fps, shape, profile)
        self.profile, self.fps, self.shape = profile, fps, tuple(shape[:2])
        self.expected = hashlib.sha256()
        self.frames = 0
        self.previous_source: tuple[str, int | None] | None = None
        self.table_writer: pq.ParquetWriter | None = None
        self.pending_rows: list[dict[str, Any]] = []
        self.pending_masks: list[dict[str, object]] = []
        self.mask_schema = pa.schema([("frame_index", pa.int64()), ("flags_png", pa.binary())])
        self.mask_writer = pq.ParquetWriter(self.root / "validity/flags.parquet", self.mask_schema)
        self.closed = False
        self.totals = dict.fromkeys(
            (
                "pixels",
                "valid_pixels",
                "invalid_pixels",
                "saturated_low_pixels",
                "saturated_high_pixels",
                "unclipped_pixels",
                "missing_frames",
                "repeated_frames",
            ),
            0,
        )
        self.max_error_m = 0.0
        self.require_source_profile = require_source_profile
        self.source_profiles: dict[str, dict[str, Any]] = {}

    def write(
        self,
        array: ArrayLike,
        scale: float | None,
        *,
        source_id: str,
        source_seq: int | None,
        source_timestamp_ns: int | None,
        target_timestamp_ns: int,
        missing: bool = False,
        valid_mask: ArrayLike | None = None,
        invalid_values: Sequence[float] = (),
        max_time_error_ns: int | None = None,
        source_profile: dict[str, Any] | None = None,
    ) -> NDArray[np.uint16]:
        if self.closed:
            raise ValueError("depth evidence writer is closed")
        source_profile_sha256 = None
        if missing:
            if any(v is not None for v in (source_seq, source_timestamp_ns, scale, source_profile)):
                raise ValueError("missing depth cannot claim a source frame or scale")
            array = np.zeros(self.shape, dtype=np.uint16)
            result = quantize(array, 1, self.profile)
            source_dtype = None
        else:
            if source_seq is None or source_timestamp_ns is None:
                raise ValueError("present depth requires source frame identity and time")
            if source_profile is not None or self.require_source_profile:
                pixels = np.asarray(array)
                source_profile = require_depth_source(
                    {
                        "source_profile": source_profile,
                        "encoding": source_profile.get("encoding")
                        if isinstance(source_profile, dict)
                        else None,
                        "depth_scale": scale,
                        "shape": list(pixels.shape),
                        "dtype": pixels.dtype.str,
                        "invalid_value": invalid_values[0] if len(invalid_values) else None,
                    },
                    pixels,
                )
                if list(invalid_values) != source_profile["invalid_values"]:
                    raise ValueError("depth invalid values differ from declared source profile")
                source_profile_sha256 = profile_hash(source_profile)
                self.source_profiles[source_profile_sha256] = json.loads(json.dumps(source_profile))
                if len(self.source_profiles) > 64:
                    raise ValueError("too many depth source profile versions")
            result = quantize(
                array, scale, self.profile, valid_mask=valid_mask, invalid_values=invalid_values
            )
            source_dtype = np.asarray(array).dtype.str
        if result.codes.shape != self.shape:
            raise ValueError("depth resolution differs from declared stream")
        report = result.error_report(self.profile)
        error_ns = (
            None if source_timestamp_ns is None else source_timestamp_ns - target_timestamp_ns
        )
        exceeded = (
            error_ns is not None
            and max_time_error_ns is not None
            and (abs(error_ns) > max_time_error_ns)
        )
        identity = None if missing else (source_id, source_seq)
        repeated = identity is not None and identity == self.previous_source
        self.previous_source = identity
        mask_path = "validity/flags.parquet"
        with io.BytesIO() as buffer:
            Image.fromarray(result.flags).save(buffer, format="PNG")
            mask_bytes = buffer.getvalue()
        self.pending_masks.append({"frame_index": self.frames, "flags_png": mask_bytes})
        self.video.write_codes(result.codes)
        self.expected.update(result.codes.astype("<u2").tobytes())
        record = {
            "frame_index": self.frames,
            "target_timestamp_ns": target_timestamp_ns,
            "source_id": source_id,
            "source_seq": source_seq,
            "source_timestamp_ns": source_timestamp_ns,
            "time_error_ns": error_ns,
            "max_time_error_ns": max_time_error_ns,
            "time_threshold_exceeded": exceeded,
            "missing": missing,
            "repeated": repeated,
            "source_dtype": source_dtype,
            "depth_scale_m_per_unit": scale,
            "source_profile_sha256": source_profile_sha256,
            "profile_sha256": self.profile.sha256,
            "mask_path": mask_path,
            "mask_sha256": hashlib.sha256(mask_bytes).hexdigest(),
            **report,
        }
        # Explicit schema keeps null first/missing frames from fixing a null type.
        integer_keys = {
            "frame_index",
            "target_timestamp_ns",
            "source_seq",
            "source_timestamp_ns",
            "time_error_ns",
            "max_time_error_ns",
            "pixels",
            "valid_pixels",
            "invalid_pixels",
            "saturated_low_pixels",
            "saturated_high_pixels",
            "unclipped_pixels",
        }
        bool_keys = {"missing", "repeated", "time_threshold_exceeded", "half_cell_bound_passed"}
        float_keys = {
            "depth_scale_m_per_unit",
            "unclipped_max_error_m",
            "unclipped_p50_error_m",
            "unclipped_p95_error_m",
            "unclipped_p99_error_m",
            "clipped_max_error_m",
            "float32_tolerance_m",
            "mm_reader_additional_rounding_bound_m",
        }
        schema = pa.schema(
            [
                (
                    key,
                    pa.int64()
                    if key in integer_keys
                    else pa.bool_()
                    if key in bool_keys
                    else pa.float64()
                    if key in float_keys
                    else pa.string(),
                )
                for key in record
            ]
        )
        if self.table_writer is None:
            self.table_writer = pq.ParquetWriter(self.root / "frames.parquet", schema)
        self.pending_rows.append(record)
        if len(self.pending_rows) >= 128:
            self.table_writer.write_table(pa.Table.from_pylist(self.pending_rows, schema=schema))
            self.pending_rows.clear()
            self.mask_writer.write_table(
                pa.Table.from_pylist(self.pending_masks, schema=self.mask_schema)
            )
            self.pending_masks.clear()
        for key in self.totals.keys() & report.keys():
            self.totals[key] += report[key]
        self.totals["missing_frames"] += int(missing)
        self.totals["repeated_frames"] += int(repeated)
        self.max_error_m = max(self.max_error_m, report["unclipped_max_error_m"] or 0)
        self.frames += 1
        return result.codes

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.video.close()
        finally:
            if self.table_writer is not None:
                if self.pending_rows:
                    self.table_writer.write_table(
                        pa.Table.from_pylist(self.pending_rows, schema=self.table_writer.schema)
                    )
                    self.pending_rows.clear()
                self.table_writer.close()
            if self.pending_masks:
                self.mask_writer.write_table(
                    pa.Table.from_pylist(self.pending_masks, schema=self.mask_schema)
                )
                self.pending_masks.clear()
            self.mask_writer.close()
        if not self.frames:
            raise ValueError("cannot finalize an empty depth stream")
        actual, count = hashlib.sha256(), 0
        with av.open(str(self.path)) as container:
            stream = container.streams.video[0]
            if (
                stream.codec_context.name != "hevc"
                or stream.codec_context.format is None
                or stream.codec_context.format.name != "gray12le"
            ):
                raise ValueError("depth codec/pixel format validation failed")
            codec_profile = stream.codec_context.profile
            for frame in container.decode(stream):
                if (
                    frame.pts is None
                    or frame.time_base is None
                    or abs(float(frame.pts * frame.time_base) - count / self.fps) > 1e-6
                ):
                    raise ValueError("depth video PTS does not match episode grid")
                array = frame.to_ndarray(format="gray12le")
                if array.shape != self.shape:
                    raise ValueError("depth decoded resolution mismatch")
                actual.update(array.astype("<u2").tobytes())
                count += 1
        if count != self.frames or actual.digest() != self.expected.digest():
            raise ValueError("HEVC code losslessness validation failed")
        write_json(
            self.root / "profile.json",
            {
                **self.profile.document(),
                "profile_sha256": self.profile.sha256,
                "feature_info": {**self.profile.metadata(), "video.fps": self.fps},
            },
        )
        write_json(
            self.root / "report.json",
            {
                "format": "hc-depth-evidence/1",
                "profile_sha256": self.profile.sha256,
                "frames": self.frames,
                "shape": list(self.shape),
                "source_contract_required": self.require_source_profile,
                "source_profiles": self.source_profiles,
                "totals": self.totals,
                **missing_measurement_status(self.totals, self.frames),
                "quantization": {
                    "unclipped_max_error_m": self.max_error_m
                    if self.totals["unclipped_pixels"]
                    else None,
                    "per_frame_percentiles": "frames.parquet",
                    "half_cell_bound_passed": True,
                    "float32_tolerance_m": 2e-5,
                    "mm_reader_additional_rounding_bound_m": 0.0005,
                    "saturated_pixels_reported_separately": True,
                },
                "video_encoding": {
                    "lossless_codes": True,
                    "codes_sha256": actual.hexdigest(),
                    "video_sha256": file_hash(self.path),
                    "codec_profile": codec_profile,
                    **self.video.encoding_metadata(),
                },
                "validity": {
                    "encoding": "parquet-png-u8-flags",
                    "path": "validity/flags.parquet",
                    "row_group_frames": 128,
                    "valid_bit": 0,
                    "saturated_low_bit": 1,
                    "saturated_high_bit": 2,
                    "official_reader_applies_mask": False,
                    "code_zero_is_not_an_invalid_indicator": True,
                },
            },
        )


def read_validity(evidence_dir: str | Path, frame_index: int) -> dict[str, NDArray[np.bool_]]:
    """Independent offline HC mask application; no database/platform dependency."""
    if frame_index < 0:
        raise ValueError("depth frame index must be nonnegative")
    path = Path(evidence_dir) / "validity/flags.parquet"
    parquet = pq.ParquetFile(path)
    if frame_index >= parquet.metadata.num_rows:
        raise IndexError(frame_index)
    rows = parquet.read_row_group(frame_index // 128).to_pylist()
    row = rows[frame_index % 128]
    if row["frame_index"] != frame_index:
        raise ValueError("depth validity frame mapping mismatch")
    with Image.open(io.BytesIO(row["flags_png"])) as image:
        flags = np.asarray(image)
    return {
        "valid": flags & 1 != 0,
        "saturated_low": flags & 2 != 0,
        "saturated_high": flags & 4 != 0,
    }
