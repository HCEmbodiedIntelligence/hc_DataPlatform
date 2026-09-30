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

from hc_data_platform.capabilities.registry import CapabilityDescriptor
from hc_data_platform.core.errors import problem
from hc_data_platform.data_formats.archives import zip_files as _zip_files

# External callers of the old private helpers retain their import during migration.
from hc_data_platform.data_formats.arrow import (
    FeatureShapeError as _FeatureShapeError,  # noqa: F401
)
from hc_data_platform.data_formats.arrow import feature_type as _feature_type  # noqa: F401
from hc_data_platform.data_formats.arrow import lerobot_value as _lerobot_value  # noqa: F401
from hc_data_platform.data_formats.arrow import parquet_bytes as _parquet_bytes  # noqa: F401
from hc_data_platform.data_formats.arrow import require_arrow as _require_arrow
from hc_data_platform.data_formats.json import canonical_json_bytes

from .contracts import LEROBOT_EXPORT_REVISION, step_export_descriptor
from .export_assets import ExportAssetsPort
from .models import (
    ExportFormat,
    ExportResultV1,
    ExportStepV1,
    PublishedDatasetManifestV1,
)
from .ports import ArtifactSinkPort, ExportSourcePort
from .source_validation import (
    collect_and_validate_export_steps as collect_and_validate_export_steps,
)


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
    def descriptor(self) -> CapabilityDescriptor:
        return step_export_descriptor(self.format.value, "lance-snapshot/v1")

    def preflight(self, *, manifest: PublishedDatasetManifestV1, source: ExportSourcePort) -> int:
        return len(collect_and_validate_export_steps(manifest, source))

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


def _scalar_feature(dtype: str) -> dict[str, Any]:
    return {"dtype": dtype, "shape": [1], "names": None}


class LeRobotV3Exporter:
    @property
    def descriptor(self) -> CapabilityDescriptor:
        return step_export_descriptor(self.format.value, LEROBOT_EXPORT_REVISION)

    def preflight(self, *, manifest: PublishedDatasetManifestV1, source: ExportSourcePort) -> int:
        return len(collect_and_validate_export_steps(manifest, source))

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
