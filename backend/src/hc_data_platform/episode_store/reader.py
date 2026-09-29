"""Fixed-version logical Parquet windows and physical depth, without Lance IDs."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import av
import numpy as np
import pyarrow.parquet as pq
from PIL import Image
from pydantic import Field

from hc_data_platform.lance_catalog.models import StepRecord
from hc_data_platform.publishing.holobrain_depth import DepthProfile, dequantize
from hc_data_platform.publishing.lerobot_export import DATA_PATH, VIDEO_PATH

from .models import DataVersionRef, EpisodeIdentity, FrozenModel
from .realsense_colorizer import RealSenseColorizer, display_profile


class PackageStepWindow(FrozenModel):
    data: DataVersionRef
    start_step: int = Field(ge=0)
    end_step: int = Field(ge=0)
    steps: tuple[StepRecord, ...]


class PackageStepReaderPort(Protocol):
    def read_steps(
        self,
        identity: EpisodeIdentity,
        data_version_id: str,
        start_step: int,
        end_step: int,
        *,
        columns=None,
    ) -> PackageStepWindow: ...


class DepthPixelValue(FrozenModel):
    data: DataVersionRef
    feature: str
    frame_index: int = Field(ge=0)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    shape: tuple[int, int]
    value: float | None = Field(allow_inf_nan=False)
    unit: Literal["m", "mm"]
    valid: bool
    saturated_low: bool
    saturated_high: bool
    flags: int
    quantized: Literal[True] = True
    coordinate_space: Literal["depth_native"] = "depth_native"
    profile: dict[str, Any]
    source: dict[str, Any]


def window_rows(path, start: int, end: int, columns=None):
    """Skip irrelevant row groups and read only requested columns in small batches."""
    parquet = pq.ParquetFile(path)
    offset = 0
    for group in range(parquet.metadata.num_row_groups):
        count = parquet.metadata.row_group(group).num_rows
        if offset >= end:
            break
        if offset + count > start:
            position = offset
            for batch in parquet.iter_batches(batch_size=128, row_groups=[group], columns=columns):
                lo, hi = max(0, start - position), min(batch.num_rows, end - position)
                if hi > lo:
                    yield from batch.slice(lo, hi - lo).to_pylist()
                position += batch.num_rows
                if position >= end:
                    break
        offset += count


def restore_value(value, target, info):
    if info["hc.source_shapes"].get(target) == [1]:
        value = [value]
    encoding = info.get("hc.structured_features", {}).get(target)
    if not encoding:
        return value
    if encoding["encoding"] == "json":
        return json.loads(value)
    if encoding["encoding"] == "vector":
        return {
            encoding["value_key"]: value,
            **{key: encoding[key] for key in ("names", "units") if key in encoding},
        }
    if encoding["encoding"] == "pose":
        return {"position_xyz": value[:3], "orientation_wxyz": value[3:]}
    raise ValueError("unsupported committed structured feature encoding")


class ParquetStepReader:
    def __init__(self, files, *, max_window=4096):
        self.files, self.max_window = files, max_window

    def read_steps(
        self,
        identity: EpisodeIdentity,
        data_version_id: str,
        start_step: int,
        end_step: int,
        *,
        columns=None,
    ) -> PackageStepWindow:
        if not 0 <= start_step <= end_step or end_step - start_step > self.max_window:
            raise ValueError("invalid or oversized half-open step window")
        package = self.files.open(identity, data_version_id)
        data = package.receipt.data
        if end_step > data.frame_count:
            raise ValueError("step window extends beyond the committed Episode")
        info = package.document("meta/info.json")
        mapping = info["hc.feature_mapping"]
        selected = tuple(mapping) if columns is None else tuple(dict.fromkeys(columns))
        if set(selected) - set(mapping):
            raise ValueError("requested logical modality is absent from this data version")
        projections = ["frame_index", "hc.timestamp_ns", "hc.sample_valid"]
        media = set()
        for name in selected:
            target = mapping[name]
            if info["features"][target]["dtype"] == "video":
                media.add(target)
                projections.append("hc.source_ref." + target)
            else:
                projections.append(target)
            projections.extend(
                f"hc.{kind}.{target}"
                for kind in ("source_timestamps_ns", "time_error_ns", "valid", "repeated")
            )
        result = []
        with package.file(DATA_PATH) as path:
            for row in window_rows(path, start_step, end_step, projections):
                values = {}
                source_times, errors, valid, repeated = {}, {}, {}, {}
                for name in selected:
                    target = mapping[name]
                    if target in media:
                        # A logical reference only: no per-step image arrays or signed URLs.
                        values[name] = {
                            "kind": "episode_video_frame",
                            "feature": target,
                            "data_version_id": data.data_version_id,
                            "frame_index": row["frame_index"],
                            "source": json.loads(row["hc.source_ref." + target]),
                        }
                    else:
                        values[name] = restore_value(row[target], target, info)
                    source_times[name] = tuple(json.loads(row[f"hc.source_timestamps_ns.{target}"]))
                    errors[name] = json.loads(row[f"hc.time_error_ns.{target}"])
                    valid[name], repeated[name] = (
                        row[f"hc.valid.{target}"],
                        row[f"hc.repeated.{target}"],
                    )
                result.append(
                    StepRecord(
                        rollout_id=identity.rollout_id,
                        step_index=row["frame_index"],
                        timestamp_ns=row["hc.timestamp_ns"],
                        sample_valid=row["hc.sample_valid"],
                        modalities=values,
                        source_timestamps_ns=source_times,
                        time_error_ns=errors,
                        valid=valid,
                        repeated=repeated,
                    )
                )
        if len(result) != end_step - start_step:
            raise ValueError("committed step window is incomplete")
        return PackageStepWindow(
            data=data, start_step=start_step, end_step=end_step, steps=tuple(result)
        )


@dataclass(frozen=True)
class DepthFrame:
    data: DataVersionRef
    feature: str
    frame_index: int
    unit: str
    values: np.ndarray
    flags: np.ndarray
    profile: dict
    source: dict

    def pixel(self, x: int, y: int) -> DepthPixelValue:
        height, width = self.values.shape
        if not 0 <= x < width or not 0 <= y < height:
            raise ValueError("pixel lies outside the native depth image")
        flags = int(self.flags[y, x])
        valid = bool(flags & 1)
        return DepthPixelValue(
            data=self.data,
            feature=self.feature,
            frame_index=self.frame_index,
            x=x,
            y=y,
            shape=(height, width),
            value=float(self.values[y, x]) if valid else None,
            unit=self.unit,
            valid=valid,
            saturated_low=bool(flags & 2),
            saturated_high=bool(flags & 4),
            flags=flags,
            profile=self.profile,
            source=self.metadata()["source"],
        )

    def metadata(self):
        return {
            "data": self.data.model_dump(mode="json"),
            "feature": self.feature,
            "frame_index": self.frame_index,
            "unit": self.unit,
            "dtype": str(self.values.dtype),
            "shape": list(self.values.shape),
            "profile": self.profile,
            "source": {
                key: str(value) if key.endswith("_ns") and value is not None else value
                for key, value in self.source.items()
            },
            "invalid": "NaN",
            "flags": {"valid": 1, "saturated_low": 2, "saturated_high": 4},
            "quantized": True,
            "preview": display_profile(self.profile["depth_max"]),
            "model_depth_training_support": "not_evaluated",
        }

    def binary(self) -> bytes:
        output = io.BytesIO()
        # Numeric arrays only; consumers use np.load(..., allow_pickle=False).
        np.savez_compressed(output, values=self.values, flags=self.flags)
        return output.getvalue()

    def preview_png(self) -> bytes:
        """Display derivative only; measurements always use values + flags."""
        physical_m = self.values / 1000 if self.unit == "mm" else self.values
        output = io.BytesIO()
        with RealSenseColorizer(physical_m.shape, depth_max=self.profile["depth_max"]) as colorizer:
            Image.fromarray(colorizer.colorize(physical_m, self.flags)).save(output, format="PNG")
        return output.getvalue()


class PhysicalDepthReader:
    def __init__(self, files):
        self.files = files

    def read(
        self,
        identity: EpisodeIdentity,
        data_version_id: str,
        feature: str,
        frame_index: int,
        *,
        unit: Literal["mm", "m"] = "mm",
    ) -> DepthFrame:
        if unit not in {"mm", "m"}:
            raise ValueError("depth output unit must be mm or m")
        package = self.files.open(identity, data_version_id)
        if not 0 <= frame_index < package.receipt.data.frame_count:
            raise ValueError("depth frame index is outside the committed Episode")
        info = package.document("meta/info.json")
        if not info["features"].get(feature, {}).get("info", {}).get("is_depth_map"):
            raise KeyError("selected feature is not a depth camera in this version")
        modalities = package.document("hc/modalities.json")
        evidence = modalities["depth"][feature]["evidence_path"]
        declaration = package.document(f"{evidence}/profile.json")
        profile = DepthProfile(
            **{
                key: declaration[key]
                for key in ("depth_min", "depth_max", "shift", "use_log", "version")
            }
        )
        if profile.sha256 != declaration["profile_sha256"]:
            raise ValueError("depth profile checksum mismatch")
        with package.file(f"{evidence}/frames.parquet") as path:
            source = next(window_rows(path, frame_index, frame_index + 1))
        with package.file(f"{evidence}/validity/flags.parquet") as path:
            mask = next(window_rows(path, frame_index, frame_index + 1))
        with Image.open(io.BytesIO(mask["flags_png"])) as image:
            flags = np.asarray(image).copy()
        video = VIDEO_PATH.format(video_key=feature, chunk_index=0, file_index=0)
        codes = None
        with package.file(video) as path, av.open(str(path)) as container:
            stream = container.streams.video[0]
            target = frame_index / info["fps"]
            container.seek(int(target / stream.time_base), stream=stream, backward=True)
            for frame in container.decode(stream):
                timestamp = float(frame.pts * frame.time_base)
                if abs(timestamp - target) <= 1e-6:
                    codes = frame.to_ndarray(format="gray12le")
                    break
                if timestamp > target + 1e-6:
                    break
        if codes is None or codes.shape != flags.shape:
            raise ValueError("committed depth frame missing or mask dimensions differ")
        physical = dequantize(codes, profile, output_unit=unit)
        physical[flags & 1 == 0] = np.nan
        return DepthFrame(
            package.receipt.data, feature, frame_index, unit, physical, flags, declaration, source
        )
