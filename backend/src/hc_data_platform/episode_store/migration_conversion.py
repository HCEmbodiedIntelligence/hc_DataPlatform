"""Frozen, resumable legacy conversion. Staging never changes visible pointers.

The caller supplies the same authorized PreparedAssets port used by processing.
This layer verifies those frozen bytes, preserves exact legacy logical rows and
records their origin. Resolving historical object manifests/units is a separate
input preparation step; an arbitrary video without depth evidence cannot pass.
"""

import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot, StepRecord
from hc_data_platform.lance_catalog.service import compute_fragment_hash
from hc_data_platform.publishing.depth_evidence import file_hash
from hc_data_platform.publishing.feature_names import camera_feature
from hc_data_platform.publishing.lerobot_export import DATA_PATH, VIDEO_PATH, _feature_names

from .commit import EpisodePackageCommitter
from .models import Digest, FrozenModel, LegacyEpisodeRef, PackageInput, canonical, digest
from .postgres import PackageConflict, assert_scope
from .reader import restore_value
from .schemas import EpisodeSchemas
from .validation import document, package_file, parquet_rows, validate_package
from .writer import write_episode, write_json

METADATA_FIELDS = {"task", "robot_type", "source", "modalities", "calibration", "camera_topics"}


def _features(first, metadata, videos):
    if set(metadata) - METADATA_FIELDS:
        raise ValueError("migration metadata cannot inherit quality/review decisions")
    mapping = _feature_names(sorted(first.modalities), camera_topics=metadata.get("camera_topics"))
    expected_cameras = {
        target
        for name, target in mapping.items()
        if camera_feature(name)
        or target.startswith("observation.images.")
        or name in metadata.get("camera_topics", ())
        or isinstance(first.modalities[name], dict)
        and first.modalities[name].get("schema_version") == "aligned-media-frame-ref/v1"
    }
    if expected_cameras != set(videos):
        raise PackageConflict("migration requires prepared media for every legacy camera")
    return mapping


class MigrationEpisodePlan(FrozenModel):
    format: Literal["hc-episode-migration-plan/1"] = "hc-episode-migration-plan/1"
    inventory_sha256: Digest
    legacy: LegacyEpisodeRef
    revision_row_sha256: tuple[Digest, ...] = Field(min_length=1)
    schema_snapshot: DatasetSchemaSnapshot
    source_sha256: Digest
    asset_contract: dict[str, Any]
    metadata: dict[str, Any]
    required_features: tuple[str, ...] = Field(min_length=1)
    required_depth_features: tuple[str, ...] = ()
    processor_version: Literal["hc-legacy-migration/1"] = "hc-legacy-migration/1"

    @property
    def sha256(self):
        return digest(self.model_dump(mode="json"))

    @property
    def source(self):
        return PackageInput(
            identity=self.legacy.identity,
            source_sha256=self.source_sha256,
            processor_version=self.processor_version,
            configuration_sha256=self.sha256,
            schema_snapshot_id=self.legacy.schema_snapshot_id,
            required_features=self.required_features,
            required_depth_features=self.required_depth_features,
        )


def asset_contract(assets):
    """Paths and attempt IDs may move; bytes and declared semantics may not."""
    assert_scope(assets.identity)
    result = {}
    for feature in sorted(assets.videos):
        media = assets.video(assets.identity, feature)
        result[feature] = {
            "sha256": media.sha256,
            "size": media.size,
            "feature": media.feature,
            "evidence": [e.model_dump(mode="json") for e in media.evidence_files],
        }
    auxiliary = []
    for entry in assets.auxiliary_files:
        if assets.auxiliary_root is None or not entry.path.startswith("hc/recording/"):
            raise PackageConflict("migration recording evidence lacks its frozen root")
        path = package_file(assets.auxiliary_root, entry.path)
        if path.stat().st_size != entry.size or file_hash(path) != entry.sha256:
            raise PackageConflict("migration recording evidence changed")
        auxiliary.append(entry.model_dump(mode="json"))
    return {"videos": result, "auxiliary": auxiliary}


def pinned_legacy(legacy, reference):
    assert_scope(reference.identity)
    identity = reference.identity
    version = legacy.version_snapshot(
        identity.dataset_id, project_id=identity.project_id, version=reference.dataset_version
    )
    lineage = legacy.lineage(
        identity.dataset_id,
        identity.rollout_id,
        project_id=identity.project_id,
        version=reference.dataset_version,
    )
    if (
        version.dataset_uri != reference.dataset_uri
        or version.lance_version != reference.lance_version
        or version.schema_snapshot_id != reference.schema_snapshot_id
        or lineage.schema_snapshot_id != reference.schema_snapshot_id
        or lineage.fragment_content_hash != reference.content_hash
        or identity.rollout_id not in version.committed_rollouts
    ):
        raise PackageConflict("legacy storage/lineage differs from frozen migration reference")
    return version, lineage


def legacy_steps(legacy, reference, *, checkpoint=lambda stage: None):
    """Bounded, exact-version reads; verify length and logical order at every window."""
    pinned_legacy(legacy, reference)
    identity = reference.identity
    for start in range(0, reference.frame_count, 1024):
        end = min(start + 1024, reference.frame_count)
        selected = legacy.read_steps(
            identity.dataset_id,
            identity.rollout_id,
            start,
            end,
            project_id=identity.project_id,
            version=reference.dataset_version,
        )
        if (
            selected.dataset_version != reference.dataset_version
            or len(selected.steps) != end - start
        ):
            raise PackageConflict("legacy migration window is incomplete or changed version")
        for index, row in enumerate(selected.steps, start):
            if (
                row.step_index != index
                or row.rollout_id != identity.rollout_id
                or row.schema_version != "1"
            ):
                raise PackageConflict("legacy migration row identity/order/schema differs")
            yield row
        checkpoint("migration_source_window_read")


def freeze_plan(inventory, reference, legacy, schema, assets, *, metadata):
    """Freeze an inventoried reference and already authorized, prepared media."""
    assert_scope(reference.identity)
    payload = inventory["inventory"]
    scope = reference.identity
    if (
        inventory["inventory_sha256"] != digest(payload)
        or payload.get("format") != "hc-episode-migration-inventory/1"
        or payload["scope"]
        != {
            k: getattr(scope, k)
            for k in ("organization_id", "project_id", "region_code", "dataset_id")
        }
    ):
        raise PackageConflict("migration inventory hash/scope differs")
    revisions = sorted(
        {
            c["revision_row_sha256"]
            for c in payload["legacy_candidates"]
            if c["legacy"] == reference.model_dump(mode="json")
            and c["state"] == "AWAITING_VERIFICATION"
        }
    )
    if not revisions:
        raise PackageConflict("migration reference was not an unblocked inventory candidate")
    version, lineage = pinned_legacy(legacy, reference)
    EpisodeSchemas.validate(schema)
    if (
        schema.project_id != scope.project_id
        or schema.dataset_id != scope.dataset_id
        or schema.schema_snapshot_id != reference.schema_snapshot_id
        or schema.fingerprint != version.schema_fingerprint
        or schema.frequency_hz != version.frequency_hz
        or assets.identity != scope
    ):
        raise PackageConflict("migration schema/assets differ from the fixed legacy binding")
    first = next(legacy_steps(legacy, reference))
    mapping = _features(first, metadata, assets.videos)
    contract = asset_contract(assets)
    # Detach mutable caller dictionaries from the frozen plan.
    return MigrationEpisodePlan.model_validate_json(
        canonical(
            {
                "inventory_sha256": inventory["inventory_sha256"],
                "legacy": reference.model_dump(mode="json"),
                "revision_row_sha256": revisions,
                "schema_snapshot": schema.model_dump(mode="json"),
                "source_sha256": lineage.source_sha256,
                "asset_contract": contract,
                "metadata": metadata,
                "required_features": sorted(mapping.values()),
                "required_depth_features": sorted(
                    name
                    for name, video in contract["videos"].items()
                    if video["feature"].get("info", {}).get("is_depth_map")
                ),
            }
        )
    )


def restored_steps(root, reference):
    """Reconstruct original sparse maps, not API defaults or video transport wrappers."""
    info = document(root, "meta/info.json")
    trace = iter(parquet_rows(package_file(root, "hc/source_frames.parquet")))
    for row in parquet_rows(package_file(root, DATA_PATH)):
        original = next(trace, None)
        if original is None or (original["frame_index"], original["timestamp_ns"]) != (
            row["frame_index"],
            row["hc.timestamp_ns"],
        ):
            raise PackageConflict("migration source trace is incomplete or changed time")
        values = {}
        for name, feature in info["hc.feature_mapping"].items():
            values[name] = (
                json.loads(row["hc.source_ref." + feature])
                if info["features"][feature]["dtype"] == "video"
                else restore_value(row[feature], feature, info)
            )
        yield StepRecord(
            rollout_id=reference.identity.rollout_id,
            step_index=row["frame_index"],
            timestamp_ns=row["hc.timestamp_ns"],
            sample_valid=row["hc.sample_valid"],
            modalities=values,
            **{
                name: json.loads(original[name])
                for name in ("source_timestamps_ns", "time_error_ns", "valid", "repeated")
            },
        )
    if next(trace, None) is not None:
        raise PackageConflict("migration source trace contains extra rows")


def verify_conversion(root, plan, legacy):
    """Compare every logical row and every reused media/evidence byte before commit."""
    manifest = validate_package(root, plan.source)
    if manifest.frame_count != plan.legacy.frame_count:
        raise PackageConflict("migration changed legacy frame count")
    expected_source = {
        **plan.metadata.get("source", {}),
        "migration_plan_sha256": plan.sha256,
        "legacy": plan.legacy.model_dump(mode="json"),
        "identity": plan.legacy.identity.model_dump(mode="json"),
        "source_sha256": plan.source_sha256,
    }
    task_rows = list(parquet_rows(package_file(root, "meta/tasks.parquet")))
    if (
        document(root, "hc/source.json") != expected_source
        or document(root, "hc/calibration.json")
        != plan.metadata.get("calibration", {"status": "unknown"})
        or document(root, "hc/quality.json") != {"status": "UNKNOWN", "rules": []}
        or len(task_rows) != 1
        or task_rows[0]["task"] != (plan.metadata.get("task") or "unknown")
        or document(root, "meta/info.json")["robot_type"]
        != plan.metadata.get("robot_type", "unknown")
    ):
        raise PackageConflict("migration frozen source/task/calibration/quality metadata differs")
    original_hash = compute_fragment_hash(legacy_steps(legacy, plan.legacy))
    converted_hash = compute_fragment_hash(restored_steps(root, plan.legacy))
    if original_hash != plan.legacy.content_hash or converted_hash != original_hash:
        raise PackageConflict("migration numeric/time/source equivalence hash differs")
    entries = {entry.path: entry for entry in manifest.files}
    for entry in plan.asset_contract["auxiliary"]:
        actual = entries.get(entry["path"])
        if actual is None or (actual.sha256, actual.size) != (entry["sha256"], entry["size"]):
            raise PackageConflict("migration auxiliary recording evidence changed")
    media_results = {}
    for feature, media in plan.asset_contract["videos"].items():
        path = VIDEO_PATH.format(video_key=feature, chunk_index=0, file_index=0)
        if entries[path].sha256 != media["sha256"] or entries[path].size != media["size"]:
            raise PackageConflict("migration media differs from frozen original bytes")
        for evidence in media["evidence"]:
            actual = entries.get("hc/depth/" + feature + "/" + evidence["path"])
            if actual is None or (actual.sha256, actual.size) != (
                evidence["sha256"],
                evidence["size"],
            ):
                raise PackageConflict("migration depth evidence changed")
        media_results[feature] = {
            "sha256": media["sha256"],
            "operation": "byte_copy",
            "requantized": False,
            "transcoded": False,
        }
        if feature in plan.required_depth_features:
            report = document(root, "hc/depth/" + feature + "/report.json")
            media_results[feature].update(
                {
                    "profile_sha256": report["profile_sha256"],
                    "code_values_verified": True,
                    "mask_and_frame_mapping_verified": True,
                    "original_depth_comparison": "inherited_frozen_source_report_not_remeasured",
                    "quantization": report["quantization"],
                    "video_encoding": report["video_encoding"],
                }
            )
    return {
        "format": "hc-episode-migration-equivalence/1",
        "plan_sha256": plan.sha256,
        "inventory_sha256": plan.inventory_sha256,
        "legacy": plan.legacy.model_dump(mode="json"),
        "target_data_version_id": plan.source.data_version_id,
        "frame_count": manifest.frame_count,
        "legacy_logical_sha256": original_hash,
        "converted_logical_sha256": converted_hash,
        "numeric_time_source_comparison": "all_rows_exact",
        "media": media_results,
        "revision_row_sha256": list(plan.revision_row_sha256),
        "annotation_history_rewritten": False,
        "review_automatically_transferred": False,
        "visible_pointers_changed": False,
    }


def _save(path, value):
    """Immutable durable checkpoint. No overwrite, including after response loss."""
    content = canonical(value) + b"\n"
    fd, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != content:
                raise PackageConflict(
                    "migration checkpoint already contains different content"
                ) from None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)


class LegacyEpisodeConverter:
    def __init__(
        self, legacy, committer: EpisodePackageCommitter, spool, *, checkpoint=lambda stage: None
    ):
        self.legacy, self.committer = legacy, committer
        self.spool, self.checkpoint = Path(spool), checkpoint

    def stage(self, plan: MigrationEpisodePlan, assets):
        assert_scope(plan.legacy.identity)
        # Re-check persisted regional bindings on every retry; a serialized plan
        # is not a credential and cannot grant access to project-global Lance.
        from .catalog import PostgresLegacyEntries

        plan = MigrationEpisodePlan.model_validate_json(plan.model_dump_json())
        version, lineage = pinned_legacy(self.legacy, plan.legacy)
        if plan.legacy not in PostgresLegacyEntries(self.committer.repository, self.legacy)(
            version
        ):
            raise PackageConflict("migration Episode is not in the persisted regional snapshot")
        EpisodeSchemas.validate(plan.schema_snapshot)
        schema = plan.schema_snapshot
        if (
            schema.project_id != plan.legacy.identity.project_id
            or schema.dataset_id != plan.legacy.identity.dataset_id
            or schema.schema_snapshot_id != plan.legacy.schema_snapshot_id
            or schema.fingerprint != version.schema_fingerprint
            or schema.frequency_hz != version.frequency_hz
            or plan.source_sha256 != lineage.source_sha256
        ):
            raise PackageConflict("migration schema/source differs from pinned legacy version")
        if assets.identity != plan.legacy.identity or asset_contract(assets) != plan.asset_contract:
            raise PackageConflict("migration prepared assets differ from frozen plan")
        mapping = _features(
            next(legacy_steps(self.legacy, plan.legacy)), plan.metadata, assets.videos
        )
        depth = sorted(
            name
            for name, media in assets.videos.items()
            if media.feature.get("info", {}).get("is_depth_map")
        )
        if sorted(mapping.values()) != list(plan.required_features) or depth != list(
            plan.required_depth_features
        ):
            raise PackageConflict("migration required features differ from frozen input")
        directory = self.spool / ("plan-" + plan.sha256)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or any(p.is_symlink() for p in directory.parents):
            raise ValueError("migration spool must not contain symlinks")
        descriptor = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            _save(directory / "plan.json", plan.model_dump(mode="json"))
            checkpoint_path = directory / "converted.json"
            if checkpoint_path.exists():
                converted = document(directory, "converted.json")
                relative = converted["package"]
                # Resolve only a canonical descendant. No arbitrary path from a journal.
                package_file(directory, relative + "/meta/info.json")
                root = directory / relative
                report = verify_conversion(root, plan, self.legacy)
                if document(root, "hc/migration.json") != report:
                    raise PackageConflict("migration equivalence report changed")
                manifest = validate_package(root, plan.source)
                if converted != {"package": relative, "manifest_sha256": manifest.content_hash}:
                    raise PackageConflict("migration converted checkpoint changed")
            else:
                relative = "attempt-" + uuid4().hex + "/package"
                root = directory / relative
                write_episode(
                    root,
                    plan.source,
                    legacy_steps(self.legacy, plan.legacy, checkpoint=self.checkpoint),
                    assets,
                    fps=plan.schema_snapshot.frequency_hz,
                    metadata={
                        **plan.metadata,
                        "source": {
                            **plan.metadata.get("source", {}),
                            "migration_plan_sha256": plan.sha256,
                            "legacy": plan.legacy.model_dump(mode="json"),
                        },
                    },
                )
                report = verify_conversion(root, plan, self.legacy)
                write_json(root, "hc/migration.json", report)
                manifest = validate_package(root, plan.source)
                # Persist package bytes before making the resume checkpoint durable.
                for entry in manifest.files:
                    with package_file(root, entry.path).open("rb") as stream:
                        os.fsync(stream.fileno())
                for path in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True) + [
                    root,
                    root.parent,
                ]:
                    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                _save(
                    checkpoint_path, {"package": relative, "manifest_sha256": manifest.content_hash}
                )
            self.checkpoint("migration_converted")
            receipt = self.committer.commit(root, plan.source, activate=False)
            _save(directory / "staged.json", receipt.model_dump(mode="json"))
            self.checkpoint("migration_staged")
            return receipt, report, root
        finally:
            os.close(descriptor)
