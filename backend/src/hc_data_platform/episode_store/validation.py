"""Validate standalone v3 packages before publication, with bounded frame memory."""

from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path

import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image

from hc_data_platform.publishing.depth_evidence import file_hash, missing_measurement_status
from hc_data_platform.publishing.depth_source import ENCODINGS, profile_hash
from hc_data_platform.publishing.holobrain_depth import DepthProfile
from hc_data_platform.publishing.lerobot_export import DATA_PATH, EPISODES_PATH, VIDEO_PATH

from .models import CommitReceipt, FileEntry, PackageInput, PackageManifest


def package_file(root: Path, relative: str, *, receipt=False) -> Path:
    # Reuse the manifest's canonical-path validator without weakening it for reads.
    if receipt:
        if relative not in {"hc/manifest.json", "hc/commit.json"}:
            raise ValueError("unknown package receipt")
    else:
        FileEntry(path=relative, size=1, sha256="0" * 64, media_type="application/octet-stream")
    path = root / relative
    if not path.is_file() or path.is_symlink() or root.resolve() not in path.resolve().parents:
        raise ValueError(f"missing/unsafe package file: {relative}")
    if any(p.is_symlink() for p in path.parents if p != root.parent):
        raise ValueError(f"symlink in package path: {relative}")
    return path


def document(root: Path, relative: str) -> dict:
    path = package_file(root, relative)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError(f"package JSON exceeds limit: {relative}")
    value = json.loads(
        path.read_bytes(),
        parse_constant=lambda s: (_ for _ in ()).throw(ValueError(f"nonfinite JSON: {s}")),
    )
    if not isinstance(value, dict):
        raise ValueError(f"package JSON must be an object: {relative}")
    return value


def parquet_rows(path: Path):
    for batch in pq.ParquetFile(path).iter_batches(batch_size=128):
        yield from batch.to_pylist()


def validate_depth(
    root: Path,
    evidence_path: str,
    video: Path,
    feature: dict,
    frame_count: int,
    fps: float,
    *,
    target_rows,
    feature_key: str,
) -> dict[str, int]:
    declaration = document(root, f"{evidence_path}/profile.json")
    profile = DepthProfile(
        **{
            key: declaration[key]
            for key in ("depth_min", "depth_max", "shift", "use_log", "version")
        }
    )
    if (
        declaration.get("profile_sha256") != profile.sha256
        or {k: v for k, v in declaration.items() if k not in {"profile_sha256", "feature_info"}}
        != profile.document()
    ):
        raise ValueError("depth profile hash/parameters mismatch")
    expected_info = {**profile.metadata(), "video.fps": fps}
    if declaration["feature_info"] != expected_info or any(
        feature["info"].get(key) != value for key, value in expected_info.items()
    ):
        raise ValueError("official depth metadata does not match frozen profile")
    report = document(root, f"{evidence_path}/report.json")
    encoding = report["video_encoding"]
    if "encoder_profile" in encoding or "encoder_configuration_sha256" in encoding:
        configuration = {
            key: encoding.get(key)
            for key in ("encoder_profile", "pyav_version", "ffmpeg_libraries", "encoder_options")
        }
        actual = hashlib.sha256(
            json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if (
            not isinstance(configuration["encoder_profile"], str)
            or not configuration["encoder_profile"]
            or encoding.get("encoder_configuration_sha256") != actual
        ):
            raise ValueError("depth encoder configuration hash mismatch")
    source_profiles = report.get("source_profiles", {})
    if (
        not isinstance(source_profiles, dict)
        or len(source_profiles) > 64
        or type(report.get("source_contract_required", False)) is not bool
    ):
        raise ValueError("invalid depth source profile inventory")
    for digest, source_profile in source_profiles.items():
        if digest != profile_hash(source_profile):
            raise ValueError("depth source profile hash mismatch")
    if (
        report["profile_sha256"] != profile.sha256
        or report["frames"] != frame_count
        or report["video_encoding"]["video_sha256"] != file_hash(video)
        or report["video_encoding"]["lossless_codes"] is not True
        or report["quantization"]["half_cell_bound_passed"] is not True
    ):
        raise ValueError("depth encoding evidence mismatch")
    rows = parquet_rows(package_file(root, f"{evidence_path}/frames.parquet"))
    masks = parquet_rows(package_file(root, f"{evidence_path}/validity/flags.parquet"))
    observed, count, with_source = hashlib.sha256(), 0, False
    previous_source = None
    totals = dict.fromkeys(
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
    maximum_error = None
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        if (stream.codec_context.name, stream.codec_context.format.name) != ("hevc", "gray12le"):
            raise ValueError("depth requires HEVC gray12le")
        if report["video_encoding"]["codec_profile"] != stream.codec_context.profile:
            raise ValueError("depth codec profile differs from actual stream")
        for frame in container.decode(stream):
            row, mask = next(rows, None), next(masks, None)
            target = next(target_rows, None)
            if (
                row is None
                or mask is None
                or target is None
                or row["frame_index"] != count
                or mask["frame_index"] != count
                or row["profile_sha256"] != profile.sha256
                or hashlib.sha256(mask["flags_png"]).hexdigest() != row["mask_sha256"]
            ):
                raise ValueError("depth video/source/mask frame mapping mismatch")
            if row["target_timestamp_ns"] != target["hc.timestamp_ns"]:
                raise ValueError("depth source mapping targets a different Episode time grid")
            if type(row["missing"]) is not bool or type(row["target_timestamp_ns"]) is not int:
                raise ValueError("depth temporal status has an invalid timestamp/missing flag")
            if not row["missing"] and (
                type(row["source_timestamp_ns"]) is not int
                or type(row["source_seq"]) is not int
                or not isinstance(row["source_id"], str)
                or not row["source_id"]
            ):
                raise ValueError("depth source unit/identity missing")
            identity = None if row["missing"] else (row["source_id"], row["source_seq"])
            error = (
                None if row["missing"] else row["source_timestamp_ns"] - row["target_timestamp_ns"]
            )
            threshold = row["max_time_error_ns"]
            if (
                row["time_error_ns"] != error
                or threshold is not None
                and (type(threshold) is not int or threshold < 0)
                or row["time_threshold_exceeded"]
                != (error is not None and threshold is not None and abs(error) > threshold)
                or row["repeated"] != (identity is not None and identity == previous_source)
            ):
                raise ValueError("depth temporal status differs from original source identity/time")
            previous_source = identity
            expected_source = () if row["missing"] else (row["source_timestamp_ns"],)
            for kind, expected in (
                ("source_timestamps_ns", list(expected_source)),
                ("time_error_ns", row["time_error_ns"]),
                ("repeated", row["repeated"]),
            ):
                column = f"hc.{kind}.{feature_key}"
                if column in target:
                    actual = (
                        json.loads(target[column])
                        if isinstance(target[column], str)
                        else target[column]
                    )
                    if actual != expected:
                        raise ValueError("depth and aligned step source evidence differ")
            pixels = frame.to_ndarray(format="gray12le")
            with Image.open(io.BytesIO(mask["flags_png"])) as image:
                flags = np.asarray(image)
            if (
                flags.dtype != np.uint8
                or flags.shape != pixels.shape
                or np.any(flags > 7)
                or pixels.shape != tuple(feature["shape"][:2])
                or np.any((flags & 6 != 0) & (flags & 1 == 0))
                or np.any(flags & 6 == 6)
            ):
                raise ValueError("depth mask flags/shape invalid")
            if (
                np.any(pixels[flags & 1 == 0] != 0)
                or np.any(pixels[flags & 2 != 0] != 0)
                or np.any(pixels[flags & 4 != 0] != 4095)
            ):
                raise ValueError("depth invalid/saturated codes differ from mask flags")
            counts = dict(
                pixels=flags.size,
                valid_pixels=int((flags & 1 != 0).sum()),
                invalid_pixels=int((flags & 1 == 0).sum()),
                saturated_low_pixels=int((flags & 2 != 0).sum()),
                saturated_high_pixels=int((flags & 4 != 0).sum()),
                unclipped_pixels=int((flags == 1).sum()),
            )
            if (
                any(row[key] != value for key, value in counts.items())
                or row["half_cell_bound_passed"] is not True
            ):
                raise ValueError("depth per-frame validity/error report differs from stored masks")
            for key, value in counts.items():
                totals[key] += value
            totals["missing_frames"] += int(row["missing"])
            totals["repeated_frames"] += int(row["repeated"])
            value = row["unclipped_max_error_m"]
            if counts["unclipped_pixels"]:
                percentiles = [
                    row[f"unclipped_p{percentile}_error_m"] for percentile in (50, 95, 99)
                ]
                if any(
                    type(v) not in (int, float) or not math.isfinite(v) or v < 0
                    for v in [*percentiles, value]
                ) or [*percentiles, value] != sorted([*percentiles, value]):
                    raise ValueError("depth quantization error statistics are inconsistent")
                maximum_error = value if maximum_error is None else max(maximum_error, value)
            elif any(
                row[key] is not None
                for key in (
                    "unclipped_max_error_m",
                    "unclipped_p50_error_m",
                    "unclipped_p95_error_m",
                    "unclipped_p99_error_m",
                )
            ):
                raise ValueError("depth error report claims measurements for absent valid pixels")
            if row["missing"]:
                if (
                    flags.any()
                    or pixels.any()
                    or target.get(f"hc.valid.{feature_key}", False) is not False
                    or row.get("source_profile_sha256") is not None
                    or any(
                        row[k] is not None
                        for k in (
                            "source_seq",
                            "source_timestamp_ns",
                            "depth_scale_m_per_unit",
                            "source_dtype",
                        )
                    )
                ):
                    raise ValueError("missing depth frame claims measurement/source")
            else:
                scale = row["depth_scale_m_per_unit"]
                if (
                    scale is None
                    or not math.isfinite(scale)
                    or scale <= 0
                    or row["source_timestamp_ns"] is None
                    or row["source_seq"] is None
                ):
                    raise ValueError("depth source unit/identity missing")
                with_source = True
                source_digest = row.get("source_profile_sha256")
                if source_digest is not None or report.get("source_contract_required"):
                    source_profile = source_profiles.get(source_digest)
                    if source_profile is None:
                        raise ValueError("depth frame source profile missing")
                    dtype = np.dtype(row["source_dtype"])
                    if (
                        source_profile["scale_m_per_unit"] != scale
                        or (dtype.kind, dtype.itemsize) != ENCODINGS[source_profile["encoding"]]
                    ):
                        raise ValueError("depth frame units/dtype differ from source profile")
            if frame.pts is None or abs(float(frame.pts * frame.time_base) - count / fps) > 1e-6:
                raise ValueError("depth PTS mismatch")
            observed.update(pixels.astype("<u2").tobytes())
            count += 1
    if (
        count != frame_count
        or next(rows, None) is not None
        or next(masks, None) is not None
        or not with_source
        and report.get("measurement_status") != "NO_SOURCE_FRAMES"
        or observed.hexdigest() != report["video_encoding"]["codes_sha256"]
        or report["totals"] != totals
        or report["quantization"]["unclipped_max_error_m"] != maximum_error
    ):
        raise ValueError("depth completeness/code hash mismatch")
    expected_status = missing_measurement_status(totals, count).get("measurement_status")
    if "measurement_status" in report and report["measurement_status"] != expected_status:
        raise ValueError("depth measurement status differs from actual masks/source frames")
    if "hc.stats_status" in feature["info"] and (
        feature["info"]["hc.stats_status"] != "NO_VALID_PIXELS"
        or totals["valid_pixels"] != 0
        or feature["info"].get("hc.stats_valid_pixels") != 0
    ):
        raise ValueError("depth statistics availability differs from actual valid pixels")
    return totals


def validate_package(root: Path, source: PackageInput) -> PackageManifest:
    root = Path(root)
    info = document(root, "meta/info.json")
    if info.get("codebase_version") != "v3.0" or info.get("total_episodes") != 1:
        raise ValueError("primary package must be one independent LeRobot v3 Episode")
    frames, fps = info["total_frames"], info["fps"]
    if not isinstance(frames, int) or frames <= 0 or not math.isfinite(fps) or fps <= 0:
        raise ValueError("invalid frame count/frequency")
    features = info["features"]
    if not set(source.required_features).issubset(features):
        raise ValueError("declared source modality missing from complete package")
    depths = {key for key, f in features.items() if f.get("info", {}).get("is_depth_map")}
    if depths != set(source.required_depth_features):
        raise ValueError("collected depth features differ from complete package")
    origin = document(root, "hc/source.json")
    if (
        origin.get("identity") != source.identity.model_dump(mode="json")
        or origin.get("source_sha256") != source.source_sha256
    ):
        raise ValueError("package provenance differs from frozen source")
    modalities = document(root, "hc/modalities.json")
    for path in (
        "meta/stats.json",
        "hc/calibration.json",
        "hc/conversion_report.json",
        "hc/quality.json",
    ):
        document(root, path)
    if (root / "hc/quality_rules.json").exists():
        from hc_data_platform.quality.evidence import QcRuleEvidence
        from hc_data_platform.quality.models import QcReportV1

        evidence = QcRuleEvidence.model_validate(document(root, "hc/quality_rules.json"))
        quality = document(root, "hc/quality.json")
        report = QcReportV1.model_validate(quality.get("quality_report", quality))
        evidence.validate_report(report)
        if (
            evidence.source_sha256 != source.source_sha256
            or evidence.rollout_id != source.identity.rollout_id
        ):
            raise ValueError("QC rule evidence belongs to another Episode source")
        if (
            quality.get("rule_evidence") is not None
            and QcRuleEvidence.model_validate(quality["rule_evidence"]) != evidence
        ):
            raise ValueError("recording and package rule evidence differ")
    tasks = list(parquet_rows(package_file(root, "meta/tasks.parquet")))
    episodes = list(parquet_rows(package_file(root, EPISODES_PATH)))
    if (
        len(episodes) != 1
        or len(tasks) != info["total_tasks"]
        or not tasks
        or {t["task_index"] for t in tasks} != set(range(len(tasks)))
    ):
        raise ValueError("episode/task metadata mismatch")
    episode = episodes[0]
    if any(
        episode.get(key) != value
        for key, value in {
            "episode_index": 0,
            "length": frames,
            "dataset_from_index": 0,
            "dataset_to_index": frames,
            "data/chunk_index": 0,
            "data/file_index": 0,
        }.items()
    ):
        raise ValueError("independent Episode offsets/indexes invalid")
    parquet = pq.ParquetFile(package_file(root, DATA_PATH))
    if parquet.metadata.num_rows != frames:
        raise ValueError("numerical row count mismatch")
    for key, feature in features.items():
        if feature["dtype"] == "video":
            continue
        shape = feature.get("shape")
        if not shape or any(not isinstance(n, int) or n < 1 for n in shape):
            raise ValueError("invalid numerical feature shape")
        expected = pa.type_for_alias(feature["dtype"])
        if shape != [1]:
            for width in reversed(shape):
                expected = pa.list_(expected, width)
        if (
            key not in parquet.schema_arrow.names
            or parquet.schema_arrow.field(key).type != expected
        ):
            raise ValueError(f"numerical feature dtype/shape differs from official metadata: {key}")
    offset = 0
    for batch in parquet.iter_batches(batch_size=4096):
        columns = batch.to_pydict()
        count = batch.num_rows
        index = list(range(offset, offset + count))
        if (
            columns.get("index") != index
            or columns.get("frame_index") != index
            or columns.get("episode_index") != [0] * count
            or any(t not in range(len(tasks)) for t in columns["task_index"])
            or not np.allclose(
                columns["timestamp"],
                np.asarray(index) / fps,
                rtol=0,
                atol=max(1e-6, frames / fps * 1.2e-7),
            )
        ):
            raise ValueError("data indexes/timestamps invalid")
        for key, feature in features.items():
            if feature["dtype"] != "video" and key not in columns:
                raise ValueError(f"missing declared numerical feature: {key}")
        offset += count
    for key, feature in features.items():
        if feature["dtype"] != "video":
            continue
        if "/" in key or "\\" in key:
            raise ValueError("unsafe video feature key")
        relative = VIDEO_PATH.format(video_key=key, chunk_index=0, file_index=0)
        video = package_file(root, relative)
        if any(
            episode.get(f"videos/{key}/{suffix}") != value
            for suffix, value in {
                "chunk_index": 0,
                "file_index": 0,
                "from_timestamp": 0.0,
                "to_timestamp": frames / fps,
            }.items()
        ):
            raise ValueError("video offset mismatch")
        if key in depths:
            if "hc.timestamp_ns" not in parquet.schema_arrow.names:
                raise ValueError("depth reuse requires exact Episode timestamp evidence")
            target_columns = ["hc.timestamp_ns"] + [
                f"hc.{kind}.{key}"
                for kind in ("source_timestamps_ns", "time_error_ns", "repeated", "valid")
                if f"hc.{kind}.{key}" in parquet.schema_arrow.names
            ]
            target_rows = (
                row
                for batch in parquet.iter_batches(batch_size=128, columns=target_columns)
                for row in batch.to_pylist()
            )
            depth_totals = validate_depth(
                root,
                modalities["depth"][key]["evidence_path"],
                video,
                feature,
                frames,
                fps,
                target_rows=target_rows,
                feature_key=key,
            )
            depth_info = feature["info"]
            if (
                "hc.stats_valid_pixels" in depth_info
                and depth_info["hc.stats_valid_pixels"] != depth_totals["valid_pixels"]
            ):
                raise ValueError("depth statistics valid-pixel count differs from masks")
            if depth_totals["valid_pixels"] == 0 and (
                depth_info.get("hc.stats_status") != "NO_VALID_PIXELS"
                or depth_info.get("hc.stats_valid_pixels") != 0
                or key in document(root, "meta/stats.json")
                or any(column.startswith(f"stats/{key}/") for column in episode)
            ):
                raise ValueError("depth statistics claim measurements for absent valid pixels")
        else:
            count = 0
            with av.open(str(video)) as container:
                for frame in container.decode(video=0):
                    if (
                        [frame.height, frame.width, 3] != feature["shape"]
                        or frame.pts is None
                        or abs(float(frame.pts * frame.time_base) - count / fps) > 1e-6
                    ):
                        raise ValueError("RGB dimensions/time grid mismatch")
                    count += 1
            if count != frames:
                raise ValueError("RGB frame count mismatch")
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            if path.is_symlink():
                raise ValueError("package symlink forbidden")
            continue
        relative = path.relative_to(root).as_posix()
        if relative in {"hc/manifest.json", "hc/commit.json"}:
            package_file(root, relative, receipt=True)
            continue
        package_file(root, relative)
        media_type = {
            ".json": "application/json",
            ".mp4": "video/mp4",
            ".parquet": "application/vnd.apache.parquet",
        }.get(path.suffix, "application/octet-stream")
        files.append(
            FileEntry(
                path=relative,
                size=path.stat().st_size,
                sha256=file_hash(path),
                media_type=media_type,
            )
        )
    manifest = PackageManifest(input=source, frame_count=frames, files=tuple(files))
    if any((root / name).exists() for name in ("hc/manifest.json", "hc/commit.json")):
        # Downloaded packages include these two self-excluding receipts. Verify
        # their complete content contract; never silently omit a forged receipt.
        manifest_path = package_file(root, "hc/manifest.json", receipt=True)
        receipt_path = package_file(root, "hc/commit.json", receipt=True)
        if manifest_path.stat().st_size > 16 * 1024**2 or receipt_path.stat().st_size > 1024**2:
            raise ValueError("package receipt exceeds size limit")
        saved = PackageManifest.model_validate_json(manifest_path.read_bytes())
        receipt = CommitReceipt.model_validate_json(receipt_path.read_bytes())
        data = receipt.data
        if (
            saved != manifest
            or receipt.manifest_sha256 != file_hash(manifest_path)
            or receipt.idempotency_key != source.idempotency_key
            or data.identity != source.identity
            or data.data_version_id != source.data_version_id
            or data.content_hash != manifest.content_hash
            or data.schema_snapshot_id != source.schema_snapshot_id
            or data.package_prefix != source.package_prefix
            or data.frame_count != frames
        ):
            raise ValueError("downloaded package receipt/manifest/content mismatch")
    return manifest
