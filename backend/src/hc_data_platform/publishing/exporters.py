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

from .export_assets import ExportAssetsPort
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
    staging_attempt_id: str | None = None,
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
    stage_id = staging_attempt_id or attempt_id
    sink.stage_attempt(stage_id, content)
    staged = sink.read_attempt(stage_id)
    digest = hashlib.sha256(staged).hexdigest()
    validate(staged)
    download_uri = sink.publish_attempt(
        attempt_id=stage_id,
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


def _lerobot_value(value: Any) -> tuple[Any, dict[str, Any] | None]:
    """Keep vectors numeric and preserve other structured modalities losslessly."""
    if not isinstance(value, dict):
        return value, None
    for key in ("positions", "values"):
        vector = value.get(key)
        if (
            set(value) <= {key, "names"}
            and isinstance(vector, (list, tuple))
            and vector
            and all(isinstance(item, (int, float)) for item in vector)
        ):
            return list(vector), {
                "encoding": "vector",
                "value_key": key,
                "names": value.get("names"),
            }
    if set(value) == {"position_xyz", "orientation_wxyz"}:
        return [*value["position_xyz"], *value["orientation_wxyz"]], {"encoding": "pose"}
    # Keep non-vector structured values lossless in auxiliary JSON features.
    return canonical_json_bytes(value).decode("utf-8"), {"encoding": "json"}


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
    def __init__(self, assets: ExportAssetsPort | None = None) -> None:
        self._assets = assets

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
        from .lerobot_export import EXPORT_REVISION, LeRobotArchive

        steps = collect_and_validate_export_steps(manifest, source)
        archive = LeRobotArchive(manifest, steps, self._assets)
        # A corrected exporter must not reuse immutable artifacts or attempt
        # bytes produced by the former references-only implementation.
        artifact_uri = _artifact_uri(
            manifest, self.format, f"{EXPORT_REVISION}/dataset.lerobot-v3.zip"
        )
        return _publish_validated(
            format=self.format,
            manifest=manifest,
            steps=steps,
            sink=sink,
            attempt_id=attempt_id,
            staging_attempt_id=f"{attempt_id}-{EXPORT_REVISION}",
            artifact_uri=artifact_uri,
            media_type="application/zip",
            build=archive.build,
            validate=archive.validate,
        )
