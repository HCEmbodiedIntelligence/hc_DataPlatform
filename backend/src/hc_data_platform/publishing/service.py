from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from urllib.parse import quote

from hc_data_platform.core.errors import problem

from .models import (
    DatasetVersionPublishedV1,
    DerivedStatus,
    ExcludedRolloutV1,
    ExportFormat,
    ExportResultV1,
    PublicationAssetV1,
    PublishDatasetRequestV1,
    PublishedDatasetManifestV1,
    PublishedRolloutV1,
    PublishPreflightReportV1,
    StepRangeV1,
)
from .ports import (
    AnnotationSnapshotPort,
    ArtifactSinkPort,
    CatalogSnapshotPort,
    ExporterPort,
    ExportSourcePort,
    PublicationAssetSinkPort,
    PublishedManifestRepository,
)


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def select_export_rollouts(
    manifest: PublishedDatasetManifestV1, rollout_ids: tuple[str, ...]
) -> PublishedDatasetManifestV1:
    selected = set(rollout_ids)
    rollouts = tuple(item for item in manifest.rollouts if item.rollout_id in selected)
    if not selected or len(rollouts) != len(selected):
        raise problem(
            status=422,
            code="EXPORT_EPISODE_NOT_ELIGIBLE",
            title="Selected Episodes are not eligible for export",
            detail="Every selected Episode must pass quality checks and annotation review.",
        )
    content_hash = hashlib.sha256(
        canonical_json_bytes(
            {
                "publication_content_hash": manifest.content_hash,
                "rollout_ids": sorted(selected),
            }
        )
    ).hexdigest()
    return manifest.model_copy(
        update={
            "rollouts": rollouts,
            "excluded_rollouts": (),
            "content_hash": content_hash,
        }
    )


def normalize_exclusions(
    ranges: Sequence[StepRangeV1], *, total_steps: int
) -> tuple[StepRangeV1, ...]:
    clipped = [
        StepRangeV1(
            start_step=max(0, item.start_step),
            end_step=min(total_steps, item.end_step),
        )
        for item in ranges
        if item.start_step < total_steps and item.end_step > 0
    ]
    if not clipped:
        return ()
    clipped.sort(key=lambda item: (item.start_step, item.end_step))
    merged: list[StepRangeV1] = []
    for item in clipped:
        if not merged or item.start_step > merged[-1].end_step:
            merged.append(item)
            continue
        previous = merged[-1]
        merged[-1] = StepRangeV1(
            start_step=previous.start_step,
            end_step=max(previous.end_step, item.end_step),
        )
    return tuple(merged)


def complement_ranges(
    ranges: Sequence[StepRangeV1], *, total_steps: int
) -> tuple[StepRangeV1, ...]:
    included: list[StepRangeV1] = []
    cursor = 0
    for item in ranges:
        if cursor < item.start_step:
            included.append(StepRangeV1(start_step=cursor, end_step=item.start_step))
        cursor = max(cursor, item.end_step)
    if cursor < total_steps:
        included.append(StepRangeV1(start_step=cursor, end_step=total_steps))
    return tuple(included)


def manifest_content_hash(
    *,
    request: PublishDatasetRequestV1,
    rollouts: Sequence[PublishedRolloutV1],
    excluded_rollouts: Sequence[ExcludedRolloutV1],
) -> str:
    """Hash only frozen publication inputs; clocks and storage locations are excluded."""

    payload = {
        "schema_version": "published-dataset-content/v1",
        **request.model_dump(mode="json"),
        "rollouts": [item.model_dump(mode="json") for item in rollouts],
        "excluded_rollouts": [item.model_dump(mode="json") for item in excluded_rollouts],
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _annotation_asset_bytes(
    *,
    request: PublishDatasetRequestV1,
    content_hash: str,
    rollouts: Sequence[PublishedRolloutV1],
) -> bytes:
    """Build the deterministic logical ``annotations.lance`` asset.

    BE-11 owns this compact logical overlay, not the aligned base data.  A
    production object adapter may materialize the same rows as a physical
    Lance dataset without changing their canonical content hash.
    """

    return canonical_json_bytes(
        {
            "schema_version": "annotations-lance/v1",
            "project_id": request.project_id,
            "dataset_id": request.dataset_id,
            "dataset_version": request.dataset_version,
            "base_lance_version": request.base_lance_version,
            "publication_content_hash": content_hash,
            "rows": [
                {
                    "rollout_id": rollout.rollout_id,
                    "annotation_task_id": rollout.annotation_task_id,
                    "annotation_revision": rollout.annotation_revision,
                    "included_step_ranges": [
                        item.model_dump(mode="json") for item in rollout.included_step_ranges
                    ],
                    "excluded_step_ranges": [
                        item.model_dump(mode="json") for item in rollout.excluded_step_ranges
                    ],
                }
                for rollout in rollouts
            ],
        }
    )


def _training_manifest_bytes(
    *,
    request: PublishDatasetRequestV1,
    content_hash: str,
    annotations_asset: PublicationAssetV1,
    rollouts: Sequence[PublishedRolloutV1],
    excluded_rollouts: Sequence[ExcludedRolloutV1],
) -> bytes:
    return canonical_json_bytes(
        {
            "schema_version": "training-manifest/v1",
            "project_id": request.project_id,
            "dataset_id": request.dataset_id,
            "dataset_version": request.dataset_version,
            "base_lance_version": request.base_lance_version,
            "publication_content_hash": content_hash,
            "annotations": annotations_asset.model_dump(mode="json"),
            "rollouts": [item.model_dump(mode="json") for item in rollouts],
            "excluded_rollouts": [item.model_dump(mode="json") for item in excluded_rollouts],
        }
    )


class _EphemeralPublicationAssetSink:
    """Safe default for tests that do not inject durable object storage."""

    def __init__(self) -> None:
        self.artifacts: dict[str, bytes] = {}

    def put_immutable(self, artifact_uri: str, content: bytes) -> None:
        existing = self.artifacts.get(artifact_uri)
        if existing is not None and existing != content:
            raise problem(
                status=409,
                code="PUBLICATION_ASSET_IMMUTABLE",
                title="Publication asset is immutable",
                detail="The target URI already contains different bytes.",
            )
        self.artifacts[artifact_uri] = content


class DatasetPublisher:
    def __init__(
        self,
        *,
        catalog: CatalogSnapshotPort,
        annotations: AnnotationSnapshotPort,
        repository: PublishedManifestRepository,
        artifact_sink: PublicationAssetSinkPort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._catalog = catalog
        self._annotations = annotations
        self._repository = repository
        self._artifact_sink = artifact_sink or _EphemeralPublicationAssetSink()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def preflight(self, request: PublishDatasetRequestV1) -> PublishPreflightReportV1:
        snapshots = self._catalog.list_rollouts(
            project_id=request.project_id,
            dataset_id=request.dataset_id,
            lance_version=request.base_lance_version,
        )
        rollout_ids = [snapshot.rollout_id for snapshot in snapshots]
        if len(rollout_ids) != len(set(rollout_ids)):
            raise problem(
                status=409,
                code="DUPLICATE_CATALOG_ROLLOUT",
                title="Duplicate catalog rollout",
                detail="The frozen catalog snapshot contains duplicate rollout identities.",
            )

        eligible: list[PublishedRolloutV1] = []
        excluded: list[ExcludedRolloutV1] = []
        for snapshot in sorted(snapshots, key=lambda item: item.rollout_id):
            reasons: list[str] = []
            if snapshot.derived_status is not DerivedStatus.DERIVED_READY:
                reasons.append(f"DERIVED_{snapshot.derived_status.value}")
            approval = self._annotations.get_approved_revision(
                project_id=request.project_id,
                dataset_id=request.dataset_id,
                rollout_id=snapshot.rollout_id,
                lance_version=request.base_lance_version,
            )
            if approval is None:
                reasons.append("ANNOTATION_NOT_APPROVED")
            elif approval.rollout_id != snapshot.rollout_id:
                raise problem(
                    status=409,
                    code="ANNOTATION_ROLLOUT_MISMATCH",
                    title="Annotation rollout mismatch",
                    detail="The approved annotation snapshot belongs to another rollout.",
                )
            if reasons:
                excluded.append(
                    ExcludedRolloutV1(
                        rollout_id=snapshot.rollout_id,
                        reasons=tuple(reasons),
                        quality_status=snapshot.quality_status,
                        derived_status=snapshot.derived_status,
                    )
                )
                continue

            assert approval is not None
            normalized = normalize_exclusions(
                approval.excluded_step_ranges,
                total_steps=snapshot.total_steps,
            )
            included = complement_ranges(normalized, total_steps=snapshot.total_steps)
            if not included:
                excluded.append(
                    ExcludedRolloutV1(
                        rollout_id=snapshot.rollout_id,
                        reasons=("ALL_STEPS_EXCLUDED",),
                        quality_status=snapshot.quality_status,
                        derived_status=snapshot.derived_status,
                    )
                )
                continue
            eligible.append(
                PublishedRolloutV1(
                    rollout_id=snapshot.rollout_id,
                    source_mcap_sha256=snapshot.source_mcap_sha256,
                    base_lance_version=request.base_lance_version,
                    annotation_revision=approval.annotation_revision,
                    annotation_task_id=approval.annotation_task_id,
                    annotation_submission_id=approval.annotation_submission_id,
                    quality_profile_version=snapshot.quality_profile_version,
                    alignment_profile_version=snapshot.alignment_profile_version,
                    alignment_frequency_hz=snapshot.alignment_frequency_hz,
                    converter_version=snapshot.converter_version,
                    total_steps=snapshot.total_steps,
                    included_step_ranges=included,
                    excluded_step_ranges=normalized,
                )
            )
        return PublishPreflightReportV1(
            project_id=request.project_id,
            dataset_id=request.dataset_id,
            dataset_version=request.dataset_version,
            eligible_rollouts=tuple(eligible),
            excluded_rollouts=tuple(excluded),
        )

    def publish(self, request: PublishDatasetRequestV1) -> PublishedDatasetManifestV1:
        preflight = self.preflight(request)
        if not preflight.eligible_rollouts:
            raise problem(
                status=422,
                code="NO_ELIGIBLE_ROLLOUTS",
                title="No eligible rollouts",
                detail=("Publication requires materialized data and an approved Episode version."),
                details={
                    "excluded_rollouts": [
                        item.model_dump(mode="json") for item in preflight.excluded_rollouts
                    ]
                },
            )
        digest = manifest_content_hash(
            request=request,
            rollouts=preflight.eligible_rollouts,
            excluded_rollouts=preflight.excluded_rollouts,
        )
        existing = self._repository.get(
            project_id=request.project_id,
            dataset_id=request.dataset_id,
            dataset_version=request.dataset_version,
        )
        if existing is not None:
            if existing.content_hash == digest:
                # Re-enter the immutable repository boundary so a replay also repairs or
                # verifies rollout-region lineage atomically after the BR01 migration.
                return self._repository.create_immutable(existing)
            raise problem(
                status=409,
                code="DATASET_VERSION_IMMUTABLE",
                title="Dataset version is immutable",
                detail="Content changes require a new dataset version identifier.",
            )

        created_at = self._clock()
        if created_at.tzinfo is None:
            raise ValueError("publishing clock must return a timezone-aware datetime")
        base_uri = "/".join(
            (
                "published",
                quote(request.project_id, safe=""),
                quote(request.dataset_id, safe=""),
                quote(request.dataset_version, safe=""),
            )
        )
        annotations_uri = f"{base_uri}/annotations.lance"
        annotation_bytes = _annotation_asset_bytes(
            request=request,
            content_hash=digest,
            rollouts=preflight.eligible_rollouts,
        )
        annotations_asset = PublicationAssetV1(
            uri=annotations_uri,
            media_type="application/vnd.hc.annotations-lance+json",
            content_sha256=hashlib.sha256(annotation_bytes).hexdigest(),
            size_bytes=len(annotation_bytes),
        )
        training_manifest_uri = f"{base_uri}/training-manifest.json"
        training_bytes = _training_manifest_bytes(
            request=request,
            content_hash=digest,
            annotations_asset=annotations_asset,
            rollouts=preflight.eligible_rollouts,
            excluded_rollouts=preflight.excluded_rollouts,
        )
        training_digest = hashlib.sha256(training_bytes).hexdigest()

        self._artifact_sink.put_immutable(annotations_uri, annotation_bytes)
        self._artifact_sink.put_immutable(training_manifest_uri, training_bytes)
        manifest = PublishedDatasetManifestV1(
            project_id=request.project_id,
            dataset_id=request.dataset_id,
            dataset_version=request.dataset_version,
            base_lance_version=request.base_lance_version,
            created_at=created_at,
            content_hash=digest,
            annotations_uri=annotations_uri,
            annotations_content_sha256=annotations_asset.content_sha256,
            training_manifest_uri=training_manifest_uri,
            training_manifest_content_sha256=training_digest,
            rollouts=preflight.eligible_rollouts,
            excluded_rollouts=preflight.excluded_rollouts,
        )
        return self._repository.create_immutable(manifest)

    def get(
        self, *, project_id: str, dataset_id: str, dataset_version: str
    ) -> PublishedDatasetManifestV1:
        manifest = self._repository.get(
            project_id=project_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )
        if manifest is None:
            raise problem(
                status=404,
                code="DATASET_VERSION_NOT_FOUND",
                title="Dataset version not found",
                detail="No immutable manifest exists for the requested dataset version.",
            )
        return manifest

    @staticmethod
    def event(manifest: PublishedDatasetManifestV1) -> DatasetVersionPublishedV1:
        return DatasetVersionPublishedV1(
            project_id=manifest.project_id,
            dataset_id=manifest.dataset_id,
            dataset_version=manifest.dataset_version,
            manifest_content_hash=manifest.content_hash,
        )


class ExportCoordinator:
    def __init__(
        self,
        *,
        source: ExportSourcePort,
        sink: ArtifactSinkPort,
        exporters: Sequence[ExporterPort],
    ) -> None:
        self._source = source
        self._sink = sink
        self._exporters = {exporter.format: exporter for exporter in exporters}
        if len(self._exporters) != len(exporters):
            raise ValueError("only one exporter may be configured for each format")

    def export(
        self,
        manifest: PublishedDatasetManifestV1,
        *,
        format: ExportFormat,
        attempt_id: str | None = None,
    ) -> ExportResultV1:
        exporter = self._exporter(format)
        selected_attempt = (
            attempt_id
            or hashlib.sha256(f"{manifest.content_hash}:{format.value}".encode()).hexdigest()[:32]
        )
        outcome = "success"
        try:
            return exporter.export(
                manifest=manifest,
                source=self._source,
                sink=self._sink,
                attempt_id=selected_attempt,
            )
        except Exception:
            outcome = "failure"
            raise
        finally:
            from hc_data_platform.core.observability import EXPORTS

            EXPORTS.labels(
                outcome=outcome,
                format=format.value,
            ).inc()

    def preflight(
        self,
        manifest: PublishedDatasetManifestV1,
        *,
        format: ExportFormat,
    ) -> int:
        """Read and validate the immutable source before the materialization activity.

        The materializer deliberately reads the source again: shipping a possibly
        large step list through Temporal history would make the job non-durable.
        This separate bounded activity gives callers an honest preflight phase and
        detects unavailable workers or incomplete source data before writing any
        export bytes.
        """

        self._exporter(format)
        from .exporters import collect_and_validate_export_steps

        return len(collect_and_validate_export_steps(manifest, self._source))

    def verify_artifact(self, result: ExportResultV1) -> None:
        """Re-read the promoted object and verify its immutable content digest."""

        artifact = self._sink.get_published(result.artifact_uri)
        if artifact is None:
            raise problem(
                status=409,
                code="EXPORT_ARTIFACT_NOT_AVAILABLE",
                title="Export artifact is not available",
                detail="The completed workflow no longer has its immutable artifact.",
            )
        if hashlib.sha256(artifact).hexdigest() != result.artifact_content_hash:
            raise problem(
                status=409,
                code="EXPORT_ARTIFACT_HASH_MISMATCH",
                title="Export artifact integrity check failed",
                detail="The stored export artifact does not match its workflow result.",
            )

    def authorize_download(self, result: ExportResultV1) -> str:
        """Mint a new object-store GET capability only after the API reauthorizes it.

        The workflow result's original presign is never reused for a later browser
        request. This check also catches any failed/partial promotion before a URL
        can leave the authenticated boundary.
        """

        self.verify_artifact(result)
        authorization = self._sink.get_download_uri(result.artifact_uri)
        if authorization is None:
            raise problem(
                status=409,
                code="EXPORT_AUTHORIZATION_MISSING",
                title="Export authorization is missing",
                detail="The immutable export cannot receive a download authorization.",
            )
        return authorization

    def _exporter(self, format: ExportFormat) -> ExporterPort:
        exporter = self._exporters.get(format)
        if exporter is None:
            raise problem(
                status=503,
                code="EXPORTER_UNAVAILABLE",
                title="Export worker is unavailable",
                detail=(
                    "The selected native export worker is not available. "
                    "Retry after the worker is restored."
                ),
            )
        return exporter
