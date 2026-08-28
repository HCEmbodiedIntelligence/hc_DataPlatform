"""Native Lance Snapshot and LeRobot v3 archive exporters.

Both exporters write an attempt-isolated archive, reload it with the native
storage reader, and only then promote the bytes into the downloadable namespace.
"""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

from hc_data_platform.core.errors import problem

from .models import (
    ExportFormat,
    ExportResultV1,
    ExportStepV1,
    PublishedDatasetManifestV1,
)
from .ports import ArtifactSinkPort, ExportSourcePort
from .service import canonical_json_bytes


def collect_and_validate_export_steps(
    manifest: PublishedDatasetManifestV1, source: ExportSourcePort
) -> list[ExportStepV1]:
    collected: list[ExportStepV1] = []
    modality_names: set[str] | None = None
    for rollout in manifest.rollouts:
        steps = sorted(
            source.read_steps(
                project_id=manifest.project_id,
                dataset_id=manifest.dataset_id,
                rollout_id=rollout.rollout_id,
                lance_version=rollout.base_lance_version,
                ranges=rollout.included_step_ranges,
            ),
            key=lambda item: item.step_index,
        )
        expected = [
            index
            for item in rollout.included_step_ranges
            for index in range(item.start_step, item.end_step)
        ]
        actual = [step.step_index for step in steps]
        if actual != expected or any(step.rollout_id != rollout.rollout_id for step in steps):
            raise problem(
                status=409,
                code="EXPORT_SOURCE_INCOMPLETE",
                title="Export source is incomplete",
                detail=(
                    f"Synchronized source steps do not match the manifest for {rollout.rollout_id}."
                ),
                details={"expected_steps": expected, "actual_steps": actual},
            )
        timestamps = [step.timestamp_ns for step in steps]
        if any(right <= left for left, right in zip(timestamps, timestamps[1:], strict=False)):
            raise problem(
                status=409,
                code="EXPORT_SOURCE_TIME_ORDER_INVALID",
                title="Export source timestamps are not ordered",
                detail=f"Rollout {rollout.rollout_id} is not strictly time ordered.",
            )
        for step in steps:
            names = set(step.modalities)
            if not names:
                raise problem(
                    status=409,
                    code="EXPORT_MODALITIES_MISSING",
                    title="Export modalities are missing",
                    detail="Every exported Step must contain synchronized modality values.",
                )
            if modality_names is None:
                modality_names = names
            elif names != modality_names:
                raise problem(
                    status=409,
                    code="EXPORT_MODALITY_SCHEMA_MISMATCH",
                    title="Export modality schema mismatch",
                    detail="All exported Steps must have the same modality keys.",
                    details={
                        "expected": sorted(modality_names),
                        "actual": sorted(names),
                        "rollout_id": step.rollout_id,
                        "step_index": step.step_index,
                    },
                )
        collected.extend(steps)
    return collected


def _zip_files(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, files[name])
    return output.getvalue()


def _safe_extract(archive_bytes: bytes, destination: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for name in archive.namelist():
            path = Path(name)
            if path.is_absolute() or ".." in path.parts:
                raise problem(
                    status=409,
                    code="EXPORT_ARCHIVE_INVALID",
                    title="Export archive is invalid",
                    detail="The staged archive contains an unsafe member path.",
                )
        archive.extractall(destination)


def _artifact_uri(manifest: PublishedDatasetManifestV1, format: ExportFormat, filename: str) -> str:
    return "/".join(
        (
            "exports",
            quote(manifest.project_id, safe=""),
            quote(manifest.dataset_id, safe=""),
            quote(manifest.dataset_version, safe=""),
            format.value,
            manifest.content_hash,
            filename,
        )
    )


def _publish_validated(
    *,
    format: ExportFormat,
    manifest: PublishedDatasetManifestV1,
    steps: Sequence[ExportStepV1],
    sink: ArtifactSinkPort,
    attempt_id: str,
    artifact_uri: str,
    media_type: str,
    build: Callable[[], bytes],
    validate: Callable[[bytes], None],
) -> ExportResultV1:
    existing = sink.get_published(artifact_uri)
    if existing is not None:
        validate(existing)
        download_uri = sink.get_download_uri(artifact_uri)
        if download_uri is None:
            raise problem(
                status=409,
                code="EXPORT_AUTHORIZATION_MISSING",
                title="Export authorization is missing",
                detail="A final artifact exists without a download authorization.",
            )
        return ExportResultV1(
            format=format,
            project_id=manifest.project_id,
            dataset_id=manifest.dataset_id,
            dataset_version=manifest.dataset_version,
            manifest_content_hash=manifest.content_hash,
            attempt_id=attempt_id,
            artifact_uri=artifact_uri,
            download_uri=download_uri,
            artifact_content_hash=hashlib.sha256(existing).hexdigest(),
            row_count=len(steps),
            media_type=media_type,
        )

    content = build()
    sink.stage_attempt(attempt_id, content)
    staged = sink.read_attempt(attempt_id)
    digest = hashlib.sha256(staged).hexdigest()
    validate(staged)
    download_uri = sink.publish_attempt(
        attempt_id=attempt_id,
        artifact_uri=artifact_uri,
        expected_sha256=digest,
    )
    return ExportResultV1(
        format=format,
        project_id=manifest.project_id,
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        manifest_content_hash=manifest.content_hash,
        attempt_id=attempt_id,
        artifact_uri=artifact_uri,
        download_uri=download_uri,
        artifact_content_hash=digest,
        row_count=len(steps),
        media_type=media_type,
    )


def _require_arrow() -> tuple[Any, Any]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - exercised in minimal deployments
        raise problem(
            status=503,
            code="EXPORT_DEPENDENCY_MISSING",
            title="Export dependency is missing",
            detail="Install the backend data extra to enable native dataset exports.",
        ) from exc
    return pa, pq


def _require_lance() -> Any:
    try:
        import lance
    except ImportError as exc:  # pragma: no cover - exercised in minimal deployments
        raise problem(
            status=503,
            code="EXPORT_DEPENDENCY_MISSING",
            title="Export dependency is missing",
            detail="Install the backend data extra to enable Lance Snapshot export.",
        ) from exc
    return lance


class LanceSnapshotExporter:
    @property
    def format(self) -> ExportFormat:
        return ExportFormat.LANCE_SNAPSHOT

    def export(
        self,
        *,
        manifest: PublishedDatasetManifestV1,
        source: ExportSourcePort,
        sink: ArtifactSinkPort,
        attempt_id: str,
    ) -> ExportResultV1:
        steps = collect_and_validate_export_steps(manifest, source)
        artifact_uri = _artifact_uri(manifest, self.format, "aligned-steps.lance.zip")
        return _publish_validated(
            format=self.format,
            manifest=manifest,
            steps=steps,
            sink=sink,
            attempt_id=attempt_id,
            artifact_uri=artifact_uri,
            media_type="application/zip",
            build=lambda: self._build(manifest, steps),
            validate=lambda content: self._validate(content, manifest, steps),
        )

    @staticmethod
    def _rows(steps: Sequence[ExportStepV1]) -> list[dict[str, Any]]:
        return [
            {
                "rollout_id": step.rollout_id,
                "step_index": step.step_index,
                "timestamp_ns": step.timestamp_ns,
                "modalities_json": canonical_json_bytes(step.modalities).decode(),
                "source_timestamps_ns_json": canonical_json_bytes(
                    step.source_timestamps_ns
                ).decode(),
                "time_error_ns_json": canonical_json_bytes(step.time_error_ns).decode(),
                "valid_json": canonical_json_bytes(step.valid).decode(),
                "repeated_json": canonical_json_bytes(step.repeated).decode(),
                "sample_valid": step.sample_valid,
            }
            for step in steps
        ]

    def _build(self, manifest: PublishedDatasetManifestV1, steps: Sequence[ExportStepV1]) -> bytes:
        pa, _ = _require_arrow()
        lance = _require_lance()
        with tempfile.TemporaryDirectory(prefix="hc-lance-export-") as temp:
            root = Path(temp)
            dataset_path = root / "aligned_steps.lance"
            table = pa.Table.from_pylist(self._rows(steps))
            lance.write_dataset(table, str(dataset_path), mode="create")
            files = {
                f"aligned_steps.lance/{path.relative_to(dataset_path).as_posix()}": (
                    path.read_bytes()
                )
                for path in dataset_path.rglob("*")
                if path.is_file()
            }
            files["snapshot-manifest.json"] = canonical_json_bytes(
                {
                    "schema_version": "lance-snapshot-export/v1",
                    "publication_content_hash": manifest.content_hash,
                    "row_count": len(steps),
                }
            )
            return _zip_files(files)

    def _validate(
        self,
        content: bytes,
        manifest: PublishedDatasetManifestV1,
        steps: Sequence[ExportStepV1],
    ) -> None:
        lance = _require_lance()
        with tempfile.TemporaryDirectory(prefix="hc-lance-validate-") as temp:
            root = Path(temp)
            _safe_extract(content, root)
            try:
                snapshot = json.loads((root / "snapshot-manifest.json").read_bytes())
                dataset = lance.dataset(str(root / "aligned_steps.lance"))
                rows = dataset.to_table().to_pylist()
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                raise problem(
                    status=409,
                    code="LANCE_SNAPSHOT_VALIDATION_FAILED",
                    title="Lance Snapshot validation failed",
                    detail="The staged archive cannot be reloaded as a Lance dataset.",
                ) from exc
            expected = self._rows(steps)
            if (
                snapshot.get("publication_content_hash") != manifest.content_hash
                or snapshot.get("row_count") != len(steps)
                or rows != expected
            ):
                raise problem(
                    status=409,
                    code="LANCE_SNAPSHOT_VALIDATION_FAILED",
                    title="Lance Snapshot validation failed",
                    detail="Reloaded Lance rows do not match the frozen training manifest.",
                )


class _FeatureShapeError(ValueError):
    pass


def _feature_type(value: Any, pa: Any) -> tuple[Any, str, tuple[int, ...]]:
    if isinstance(value, bool):
        return pa.bool_(), "bool", ()
    if isinstance(value, int) and not isinstance(value, bool):
        return pa.int64(), "int64", ()
    if isinstance(value, float):
        return pa.float64(), "float64", ()
    if isinstance(value, str):
        return pa.string(), "string", ()
    if isinstance(value, bytes):
        return pa.binary(), "binary", ()
    if isinstance(value, (list, tuple)):
        if not value:
            raise _FeatureShapeError("empty modality arrays have no stable element type")
        child_type, dtype, child_shape = _feature_type(value[0], pa)
        for child in value[1:]:
            current_type, current_dtype, current_shape = _feature_type(child, pa)
            if current_type != child_type or current_dtype != dtype or current_shape != child_shape:
                raise _FeatureShapeError("modality arrays must be rectangular and typed")
        return pa.list_(child_type, len(value)), dtype, (len(value), *child_shape)
    raise _FeatureShapeError(f"unsupported LeRobot modality value type: {type(value).__name__}")


def _parquet_bytes(table: Any, pq: Any) -> bytes:
    output = io.BytesIO()
    pq.write_table(
        table,
        output,
        compression="NONE",
        use_dictionary=False,
        write_statistics=False,
        version="2.6",
        data_page_version="1.0",
    )
    return output.getvalue()


def _scalar_feature(dtype: str) -> dict[str, Any]:
    return {"dtype": dtype, "shape": [1], "names": None}


class LeRobotV3Exporter:
    @property
    def format(self) -> ExportFormat:
        return ExportFormat.LEROBOT_V3

    def export(
        self,
        *,
        manifest: PublishedDatasetManifestV1,
        source: ExportSourcePort,
        sink: ArtifactSinkPort,
        attempt_id: str,
    ) -> ExportResultV1:
        steps = collect_and_validate_export_steps(manifest, source)
        artifact_uri = _artifact_uri(manifest, self.format, "dataset.lerobot-v3.zip")
        return _publish_validated(
            format=self.format,
            manifest=manifest,
            steps=steps,
            sink=sink,
            attempt_id=attempt_id,
            artifact_uri=artifact_uri,
            media_type="application/zip",
            build=lambda: self._build(manifest, steps),
            validate=lambda content: self._validate(content, manifest, steps),
        )

    @staticmethod
    def _episode_steps(
        manifest: PublishedDatasetManifestV1, steps: Sequence[ExportStepV1]
    ) -> list[list[ExportStepV1]]:
        return [
            [step for step in steps if step.rollout_id == rollout.rollout_id]
            for rollout in manifest.rollouts
        ]

    def _build(self, manifest: PublishedDatasetManifestV1, steps: Sequence[ExportStepV1]) -> bytes:
        pa, pq = _require_arrow()
        frequencies = {rollout.alignment_frequency_hz for rollout in manifest.rollouts}
        if len(frequencies) != 1:
            raise problem(
                status=409,
                code="LEROBOT_FREQUENCY_MISMATCH",
                title="LeRobot frequency mismatch",
                detail="A LeRobot v3 dataset requires one synchronized frame frequency.",
            )
        fps = frequencies.pop()
        modality_names = sorted(steps[0].modalities)
        modality_types: dict[str, Any] = {}
        features: dict[str, dict[str, Any]] = {}
        for name in modality_names:
            try:
                arrow_type, dtype, shape = _feature_type(steps[0].modalities[name], pa)
                pa.array([step.modalities[name] for step in steps], type=arrow_type)
            except (TypeError, ValueError) as exc:
                raise problem(
                    status=409,
                    code="LEROBOT_MODALITY_SCHEMA_MISMATCH",
                    title="LeRobot modality schema mismatch",
                    detail=f"Modality {name!r} has no stable LeRobot feature schema.",
                ) from exc
            modality_types[name] = arrow_type
            features[name] = {
                "dtype": dtype,
                "shape": list(shape or (1,)),
                "names": None,
            }

        default_features = {
            "timestamp": _scalar_feature("float32"),
            "frame_index": _scalar_feature("int64"),
            "episode_index": _scalar_feature("int64"),
            "index": _scalar_feature("int64"),
            "task_index": _scalar_feature("int64"),
            "hc.rollout_id": _scalar_feature("string"),
            "hc.source_step_index": _scalar_feature("int64"),
            "hc.source_timestamp_ns": _scalar_feature("int64"),
            "hc.sample_valid": _scalar_feature("bool"),
        }
        for name in modality_names:
            default_features[f"hc.source_timestamps_ns.{name}"] = _scalar_feature("string")
            default_features[f"hc.time_error_ns.{name}"] = _scalar_feature("int64")
            default_features[f"hc.valid.{name}"] = _scalar_feature("bool")
            default_features[f"hc.repeated.{name}"] = _scalar_feature("bool")
        features.update(default_features)

        episode_steps = self._episode_steps(manifest, steps)
        data_columns: dict[str, list[Any]] = {name: [] for name in features}
        episode_rows: list[dict[str, Any]] = []
        global_index = 0
        for episode_index, (rollout, selected) in enumerate(
            zip(manifest.rollouts, episode_steps, strict=True)
        ):
            start_index = global_index
            for frame_index, step in enumerate(selected):
                for name in modality_names:
                    data_columns[name].append(step.modalities[name])
                data_columns["timestamp"].append(frame_index / fps)
                data_columns["frame_index"].append(frame_index)
                data_columns["episode_index"].append(episode_index)
                data_columns["index"].append(global_index)
                data_columns["task_index"].append(episode_index)
                data_columns["hc.rollout_id"].append(step.rollout_id)
                data_columns["hc.source_step_index"].append(step.step_index)
                data_columns["hc.source_timestamp_ns"].append(step.timestamp_ns)
                data_columns["hc.sample_valid"].append(step.sample_valid)
                for name in modality_names:
                    source_timestamps = step.source_timestamps_ns.get(name, ())
                    time_error = step.time_error_ns.get(name)
                    data_columns[f"hc.source_timestamps_ns.{name}"].append(
                        json.dumps(source_timestamps, separators=(",", ":"))
                    )
                    data_columns[f"hc.time_error_ns.{name}"].append(
                        -1 if time_error is None else time_error
                    )
                    data_columns[f"hc.valid.{name}"].append(step.valid.get(name, True))
                    data_columns[f"hc.repeated.{name}"].append(step.repeated.get(name, False))
                global_index += 1
            episode_rows.append(
                {
                    "episode_index": episode_index,
                    "tasks": [f"rollout:{rollout.rollout_id}"],
                    "length": len(selected),
                    "data/chunk_index": 0,
                    "data/file_index": 0,
                    "dataset_from_index": start_index,
                    "dataset_to_index": global_index,
                    "hc/rollout_id": rollout.rollout_id,
                    "hc/annotation_revision": rollout.annotation_revision,
                }
            )

        arrays: list[Any] = []
        fields: list[Any] = []
        for name in modality_names:
            fields.append(pa.field(name, modality_types[name], nullable=False))
            arrays.append(pa.array(data_columns[name], type=modality_types[name]))
        scalar_types = {
            "timestamp": pa.float32(),
            "frame_index": pa.int64(),
            "episode_index": pa.int64(),
            "index": pa.int64(),
            "task_index": pa.int64(),
            "hc.rollout_id": pa.string(),
            "hc.source_step_index": pa.int64(),
            "hc.source_timestamp_ns": pa.int64(),
            "hc.sample_valid": pa.bool_(),
        }
        for name in modality_names:
            scalar_types[f"hc.source_timestamps_ns.{name}"] = pa.string()
            scalar_types[f"hc.time_error_ns.{name}"] = pa.int64()
            scalar_types[f"hc.valid.{name}"] = pa.bool_()
            scalar_types[f"hc.repeated.{name}"] = pa.bool_()
        for name, arrow_type in scalar_types.items():
            fields.append(pa.field(name, arrow_type, nullable=False))
            arrays.append(pa.array(data_columns[name], type=arrow_type))
        data_table = pa.Table.from_arrays(arrays, schema=pa.schema(fields))

        tasks_table = pa.table(
            {
                "task_index": pa.array(range(len(manifest.rollouts)), type=pa.int64()),
                "task": pa.array(
                    [f"rollout:{item.rollout_id}" for item in manifest.rollouts],
                    type=pa.string(),
                ),
            }
        )
        pandas_metadata = {
            "index_columns": ["task"],
            "column_indexes": [
                {
                    "name": None,
                    "field_name": None,
                    "pandas_type": "unicode",
                    "numpy_type": "object",
                    "metadata": {"encoding": "UTF-8"},
                }
            ],
            "columns": [
                {
                    "name": "task_index",
                    "field_name": "task_index",
                    "pandas_type": "int64",
                    "numpy_type": "int64",
                    "metadata": None,
                },
                {
                    "name": "task",
                    "field_name": "task",
                    "pandas_type": "unicode",
                    "numpy_type": "object",
                    "metadata": None,
                },
            ],
            "creator": {"library": "hc-data-platform", "version": "1"},
            "pandas_version": "2.0.0",
        }
        tasks_table = tasks_table.replace_schema_metadata(
            {b"pandas": canonical_json_bytes(pandas_metadata)}
        )
        episodes_table = pa.Table.from_pylist(episode_rows)
        info = {
            "codebase_version": "v3.0",
            "fps": fps,
            "features": features,
            "total_episodes": len(manifest.rollouts),
            "total_frames": len(steps),
            "total_tasks": len(manifest.rollouts),
            "chunks_size": 1000,
            "data_files_size_in_mb": 100,
            "video_files_size_in_mb": 500,
            "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
            "video_path": None,
            "robot_type": "hc-data-platform",
            "splits": {"train": f"0:{len(manifest.rollouts)}"},
        }
        return _zip_files(
            {
                "data/chunk-000/file-000.parquet": _parquet_bytes(data_table, pq),
                "meta/episodes/chunk-000/file-000.parquet": _parquet_bytes(episodes_table, pq),
                "meta/info.json": canonical_json_bytes(info),
                "meta/stats.json": canonical_json_bytes({}),
                "meta/tasks.parquet": _parquet_bytes(tasks_table, pq),
                "hc-publication-manifest.json": canonical_json_bytes(
                    {
                        "schema_version": "hc-lerobot-export/v1",
                        "publication_content_hash": manifest.content_hash,
                        "rollouts": [item.model_dump(mode="json") for item in manifest.rollouts],
                    }
                ),
            }
        )

    def _validate(
        self,
        content: bytes,
        manifest: PublishedDatasetManifestV1,
        steps: Sequence[ExportStepV1],
    ) -> None:
        pa, pq = _require_arrow()
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                required = {
                    "data/chunk-000/file-000.parquet",
                    "meta/episodes/chunk-000/file-000.parquet",
                    "meta/info.json",
                    "meta/stats.json",
                    "meta/tasks.parquet",
                    "hc-publication-manifest.json",
                }
                if not required.issubset(archive.namelist()):
                    raise KeyError(sorted(required - set(archive.namelist())))
                info = json.loads(archive.read("meta/info.json"))
                hc_manifest = json.loads(archive.read("hc-publication-manifest.json"))
                data = pq.read_table(
                    pa.BufferReader(archive.read("data/chunk-000/file-000.parquet"))
                )
                episodes = pq.read_table(
                    pa.BufferReader(archive.read("meta/episodes/chunk-000/file-000.parquet"))
                )
                tasks = pq.read_table(pa.BufferReader(archive.read("meta/tasks.parquet")))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            raise problem(
                status=409,
                code="LEROBOT_VALIDATION_FAILED",
                title="LeRobot v3 validation failed",
                detail="The staged archive cannot be reloaded as a LeRobot v3 dataset.",
            ) from exc

        modality_names = sorted(steps[0].modalities)
        rows = data.to_pylist()
        episode_rows = episodes.to_pylist()
        task_rows = tasks.to_pylist()
        expected_episode_steps = self._episode_steps(manifest, steps)
        valid = (
            info.get("codebase_version") == "v3.0"
            and info.get("video_path") is None
            and info.get("total_frames") == len(steps)
            and info.get("total_episodes") == len(manifest.rollouts)
            and hc_manifest.get("publication_content_hash") == manifest.content_hash
            and len(rows) == len(steps)
            and len(episode_rows) == len(manifest.rollouts)
            and len(task_rows) == len(manifest.rollouts)
            and all(name in data.column_names for name in modality_names)
        )
        offset = 0
        if valid:
            for episode_index, (rollout, selected) in enumerate(
                zip(manifest.rollouts, expected_episode_steps, strict=True)
            ):
                metadata = episode_rows[episode_index]
                if (
                    metadata["episode_index"] != episode_index
                    or metadata["length"] != len(selected)
                    or metadata["dataset_from_index"] != offset
                    or metadata["dataset_to_index"] != offset + len(selected)
                ):
                    valid = False
                    break
                for frame_index, expected in enumerate(selected):
                    actual = rows[offset + frame_index]
                    if (
                        actual["episode_index"] != episode_index
                        or actual["frame_index"] != frame_index
                        or actual["hc.rollout_id"] != rollout.rollout_id
                        or actual["hc.source_step_index"] != expected.step_index
                        or actual["hc.source_timestamp_ns"] != expected.timestamp_ns
                        or any(
                            canonical_json_bytes(actual[name])
                            != canonical_json_bytes(expected.modalities[name])
                            for name in modality_names
                        )
                    ):
                        valid = False
                        break
                offset += len(selected)
                if not valid:
                    break
        if not valid:
            raise problem(
                status=409,
                code="LEROBOT_VALIDATION_FAILED",
                title="LeRobot v3 validation failed",
                detail="Reloaded episode Steps do not match the frozen training manifest.",
            )
