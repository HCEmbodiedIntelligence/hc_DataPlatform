from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from threading import RLock
from urllib.parse import quote

from hc_data_platform.core.errors import problem

from .models import (
    ApprovedAnnotationSnapshotV1,
    CatalogRolloutSnapshotV1,
    ExportFormat,
    ExportResultV1,
    ExportStepV1,
    PublishedDatasetManifestV1,
    StepRangeV1,
)
from .ports import ArtifactSinkPort, ExportSourcePort
from .service import canonical_json_bytes


class InMemoryCatalogSnapshot:
    def __init__(self, rollouts: Sequence[CatalogRolloutSnapshotV1] = ()) -> None:
        self.rollouts = tuple(rollouts)

    def list_rollouts(
        self, *, project_id: str, dataset_id: str, lance_version: str
    ) -> Sequence[CatalogRolloutSnapshotV1]:
        del project_id, dataset_id, lance_version
        return self.rollouts


class InMemoryAnnotationSnapshot:
    def __init__(self, approvals: Sequence[ApprovedAnnotationSnapshotV1] = ()) -> None:
        self._approvals = {item.rollout_id: item for item in approvals}

    def get_approved_revision(
        self,
        *,
        project_id: str,
        rollout_id: str,
        dataset_id: str | None = None,
        lance_version: str | None = None,
    ) -> ApprovedAnnotationSnapshotV1 | None:
        del project_id, dataset_id, lance_version
        return self._approvals.get(rollout_id)


class InMemoryPublishedManifestRepository:
    def __init__(self) -> None:
        self._manifests: dict[tuple[str, str, str], PublishedDatasetManifestV1] = {}
        self._lock = RLock()

    def create_immutable(self, manifest: PublishedDatasetManifestV1) -> PublishedDatasetManifestV1:
        key = (manifest.project_id, manifest.dataset_id, manifest.dataset_version)
        with self._lock:
            existing = self._manifests.get(key)
            if existing is not None:
                existing_content = existing.model_dump(exclude={"created_at"})
                candidate_content = manifest.model_dump(exclude={"created_at"})
                if (
                    existing.content_hash == manifest.content_hash
                    and existing_content == candidate_content
                ):
                    return existing
                raise problem(
                    status=409,
                    code="DATASET_VERSION_IMMUTABLE",
                    title="Dataset version is immutable",
                    detail="A different manifest is already published under this version.",
                )
            self._manifests[key] = manifest
            return manifest

    def get(
        self, *, project_id: str, dataset_id: str, dataset_version: str
    ) -> PublishedDatasetManifestV1 | None:
        with self._lock:
            return self._manifests.get((project_id, dataset_id, dataset_version))


class InMemoryExportSource:
    def __init__(self, steps: Sequence[ExportStepV1] = ()) -> None:
        self.steps = tuple(steps)

    def read_steps(
        self,
        *,
        project_id: str,
        dataset_id: str,
        rollout_id: str,
        lance_version: str,
        ranges: Sequence[StepRangeV1],
    ) -> Sequence[ExportStepV1]:
        del project_id, dataset_id, lance_version
        return tuple(
            step
            for step in self.steps
            if step.rollout_id == rollout_id
            and any(item.start_step <= step.step_index < item.end_step for item in ranges)
        )


class InMemoryArtifactSink:
    """Executable storage fake with separate staging and downloadable namespaces."""

    def __init__(self) -> None:
        self.attempts: dict[str, bytes] = {}
        self.artifacts: dict[str, bytes] = {}
        self.download_authorizations: dict[str, str] = {}
        self._lock = RLock()

    def put_immutable(self, artifact_uri: str, content: bytes) -> None:
        """Store a publication asset without creating export download authorization."""

        with self._lock:
            existing = self.artifacts.get(artifact_uri)
            if existing is not None and existing != content:
                raise problem(
                    status=409,
                    code="PUBLICATION_ASSET_IMMUTABLE",
                    title="Publication asset is immutable",
                    detail="The target URI already contains different bytes.",
                )
            self.artifacts[artifact_uri] = content

    def stage_attempt(self, attempt_id: str, content: bytes) -> str:
        with self._lock:
            existing = self.attempts.get(attempt_id)
            if existing is not None and existing != content:
                raise problem(
                    status=409,
                    code="EXPORT_ATTEMPT_IMMUTABLE",
                    title="Export attempt is immutable",
                    detail="The attempt identifier was reused for different staged bytes.",
                )
            self.attempts[attempt_id] = content
        return f"attempt-staging/{quote(attempt_id, safe='')}/artifact"

    def read_attempt(self, attempt_id: str) -> bytes:
        with self._lock:
            try:
                return self.attempts[attempt_id]
            except KeyError as exc:
                raise problem(
                    status=409,
                    code="EXPORT_ATTEMPT_NOT_FOUND",
                    title="Export attempt not found",
                    detail="No staged bytes exist for this export attempt.",
                ) from exc

    def publish_attempt(
        self,
        *,
        attempt_id: str,
        artifact_uri: str,
        expected_sha256: str,
    ) -> str:
        with self._lock:
            content = self.read_attempt(attempt_id)
            actual_sha256 = hashlib.sha256(content).hexdigest()
            if actual_sha256 != expected_sha256:
                raise problem(
                    status=409,
                    code="EXPORT_STAGING_HASH_MISMATCH",
                    title="Export staging hash mismatch",
                    detail="The staged artifact changed before promotion.",
                )
            existing = self.artifacts.get(artifact_uri)
            if existing is not None and existing != content:
                raise problem(
                    status=409,
                    code="EXPORT_ARTIFACT_IMMUTABLE",
                    title="Export artifact is immutable",
                    detail="The final URI already contains different bytes.",
                )
            self.artifacts[artifact_uri] = content
            download_uri = (
                f"memory://download/{quote(artifact_uri, safe='')}/?sha256={expected_sha256}"
            )
            self.download_authorizations[artifact_uri] = download_uri
            return download_uri

    def get_published(self, artifact_uri: str) -> bytes | None:
        with self._lock:
            return self.artifacts.get(artifact_uri)

    def get_download_uri(self, artifact_uri: str) -> str | None:
        with self._lock:
            return self.download_authorizations.get(artifact_uri)


# Keep the dependency-free deterministic reference exporters. Native storage
# implementations live in ``publishing.exporters``.
from .exporters import _collect_and_validate, _publish_validated  # noqa: E402


class InMemoryLanceSnapshotExporter:
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
        steps = _collect_and_validate(manifest, source)
        artifact_uri = f"exports/{manifest.dataset_version}/{self.format.value}/artifact.json"

        def build() -> bytes:
            return canonical_json_bytes(
                {
                    "format": self.format.value,
                    "manifest_content_hash": manifest.content_hash,
                    "rows": [step.model_dump(mode="json") for step in steps],
                }
            )

        def validate(content: bytes) -> None:
            try:
                payload = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise problem(
                    status=409,
                    code="LANCE_SNAPSHOT_VALIDATION_FAILED",
                    title="Lance Snapshot validation failed",
                    detail="The deterministic staged artifact cannot be reloaded.",
                ) from exc
            if payload.get("manifest_content_hash") != manifest.content_hash or payload.get(
                "rows"
            ) != [step.model_dump(mode="json") for step in steps]:
                raise problem(
                    status=409,
                    code="LANCE_SNAPSHOT_VALIDATION_FAILED",
                    title="Lance Snapshot validation failed",
                    detail="Reloaded rows do not match the frozen manifest.",
                )

        return _publish_validated(
            format=self.format,
            manifest=manifest,
            steps=steps,
            sink=sink,
            attempt_id=attempt_id,
            artifact_uri=artifact_uri,
            media_type="application/vnd.hc.lance-snapshot+json",
            build=build,
            validate=validate,
        )


class InMemoryLeRobotV3Exporter:
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
        steps = _collect_and_validate(manifest, source)
        artifact_uri = f"exports/{manifest.dataset_version}/{self.format.value}/artifact.json"
        expected_episodes = [
            {
                "episode_id": rollout.rollout_id,
                "annotation_revision": rollout.annotation_revision,
                "steps": [
                    step.model_dump(mode="json")
                    for step in steps
                    if step.rollout_id == rollout.rollout_id
                ],
            }
            for rollout in manifest.rollouts
        ]

        def build() -> bytes:
            return canonical_json_bytes(
                {
                    "format": self.format.value,
                    "manifest_content_hash": manifest.content_hash,
                    "episodes": expected_episodes,
                }
            )

        def validate(content: bytes) -> None:
            try:
                payload = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise problem(
                    status=409,
                    code="LEROBOT_VALIDATION_FAILED",
                    title="LeRobot v3 validation failed",
                    detail="The deterministic staged artifact cannot be reloaded.",
                ) from exc
            if (
                payload.get("manifest_content_hash") != manifest.content_hash
                or payload.get("episodes") != expected_episodes
            ):
                raise problem(
                    status=409,
                    code="LEROBOT_VALIDATION_FAILED",
                    title="LeRobot v3 validation failed",
                    detail="Reloaded episodes do not match the frozen manifest.",
                )

        return _publish_validated(
            format=self.format,
            manifest=manifest,
            steps=steps,
            sink=sink,
            attempt_id=attempt_id,
            artifact_uri=artifact_uri,
            media_type="application/vnd.hc.lerobot-v3+json",
            build=build,
            validate=validate,
        )
