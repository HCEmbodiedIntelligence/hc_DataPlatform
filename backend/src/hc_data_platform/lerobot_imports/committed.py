"""Versioned C/B boundary: immutable platform assets -> discovered processing plan.

C persists the immutable manifest and RawSource, then invokes this in its scoped
Worker. Robot envelopes are normalized in memory, never overwritten. Discovery does
not claim QC success; each plan task must run through LeRobotPipeline.prepare
and the existing IngestRolloutWorkflow.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from hc_data_platform.core.context import current_request_context
from hc_data_platform.ingest.ports import ObjectStoragePort
from hc_data_platform.ingest.raw_sources import RawSource

from .orchestration import LeRobotImportPlanV1, build_import_plan
from .reader import episode_metadata, safe_relative
from .source_profile import validate_processing_info


class DiscoveredLeRobotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str = "committed-lerobot-discovery/v1"
    plan: LeRobotImportPlanV1
    episodes: tuple[dict[str, Any], ...]
    source_manifest_sha256: str


def normalize_committed_manifest(raw: RawSource, assets: list[dict[str, Any]]) -> dict[str, Any]:
    """assets: path, object_key, size, sha256, optional crc64, from C's DB facts.

    C must pass COMPLETED verified asset facts belonging to this Raw source.
    This is a read view. Never overwrite an already committed source manifest.
    """
    paths = [safe_relative(a["path"]) for a in assets]
    if len(set(paths)) != len(paths) or len(paths) != raw.file_count:
        raise ValueError("LEROBOT_ASSET_INVENTORY: duplicate or missing assets")
    if sum(a["size"] for a in assets) != raw.total_bytes:
        raise ValueError("LEROBOT_ASSET_INVENTORY: total size mismatch")
    return {
        "schema_version": "raw-upload-manifest/v1",
        "upload_id": raw.raw_source_id,
        "source_format": "LEROBOT_V3",
        "content_hash": raw.content_hash,
        "source_prefix": raw.storage_prefix,
        "files": sorted(assets, key=lambda a: a["path"]),
        "organization_id": raw.organization_id,
        "project_id": raw.project_id,
        "region_code": raw.region_code,
        "dataset_id": raw.dataset_id,
        "collection_task_id": raw.collection_task_id,
        "robot_id": raw.robot_id,
    }


def read_committed_manifest(
    storage: ObjectStoragePort, raw: RawSource
) -> tuple[dict[str, Any], bytes]:
    """Return a native read view plus the ORIGINAL bytes used for lineage hashes."""
    body = b"".join(storage.read_chunks(raw.manifest_key))
    manifest = json.loads(body)
    if manifest.get("schema_version") == "robot-ingest-committed/v1":
        from hc_data_platform.robot_ingest.models import RobotIngestUpload

        upload = RobotIngestUpload.model_validate(manifest["upload"])
        target = upload.target
        if (
            (target.organization_id, target.project_id, target.region_code,
             target.dataset_id, target.collection_task_id, upload.authenticated_robot_id)
            != (raw.organization_id, raw.project_id, raw.region_code,
                raw.dataset_id, raw.collection_task_id, raw.robot_id)
            or upload.upload_id != raw.upload_id
            or upload.total_bytes != raw.total_bytes
            or any(a.state.value != "COMPLETED" or
                   (a.actual_size_bytes, a.actual_sha256, a.actual_crc64) !=
                   (a.expected_size_bytes, a.expected_sha256, a.expected_crc64)
                   for a in upload.assets)
        ):
            raise ValueError("LEROBOT_COMMITTED_IDENTITY")
        digest = hashlib.sha256("\n".join(
            f"{a.asset_id}\0{a.expected_sha256}\0{a.expected_size_bytes}"
            for a in upload.assets
        ).encode()).hexdigest()
        if digest != raw.content_hash:
            raise ValueError("LEROBOT_MANIFEST_CHANGED")
        manifest = normalize_committed_manifest(raw, [
            {"path": a.path, "object_key": a.object_key, "size": a.expected_size_bytes,
             "sha256": a.expected_sha256, "crc64": a.expected_crc64}
            for a in upload.assets
        ])
        manifest["robot_upload_id"] = upload.upload_id
    if (manifest.get("content_hash") != raw.content_hash or
            manifest.get("source_prefix") != raw.storage_prefix):
        raise ValueError("LEROBOT_MANIFEST_CHANGED")
    return manifest, body


def discover_committed_source(storage: ObjectStoragePort, raw: RawSource) -> DiscoveredLeRobotV1:
    context = current_request_context()
    if (
        (context.organization_id, context.project_id, context.region_code)
        != (raw.organization_id, raw.project_id, raw.region_code)
        or raw.raw_status.value != "COMMITTED"
        or raw.source_format.value != "LEROBOT_V3"
        or not raw.dataset_id
        or not raw.collection_task_id
        or not raw.robot_id
    ):
        raise ValueError("LEROBOT_COMMITTED_SCOPE: a committed, task-bound Raw source is required")
    manifest, body = read_committed_manifest(storage, raw)
    descriptors = {safe_relative(item["path"]): item for item in manifest["files"]}
    if len(descriptors) != len(manifest["files"]) or len(descriptors) != raw.file_count:
        raise ValueError("LEROBOT_ASSET_INVENTORY")
    with tempfile.TemporaryDirectory(prefix="lerobot-discovery-") as directory:
        root = Path(directory)
        for relative, item in descriptors.items():
            if relative not in {
                "meta/info.json",
                "capture-context.json",
                "export-complete.json",
            } and not relative.startswith("meta/episodes/"):
                continue
            if item["size"] > 64 * 1024**2:
                raise ValueError("LEROBOT_METADATA_TOO_LARGE")
            data = bytearray()
            for chunk in storage.read_chunks(
                item.get("object_key") or f"{raw.storage_prefix}/{relative}"
            ):
                data.extend(chunk)
                if len(data) > item["size"]:
                    raise ValueError("LEROBOT_ASSET_INTEGRITY")
            if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError("LEROBOT_ASSET_INTEGRITY")
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        info = validate_processing_info(json.loads((root / "meta/info.json").read_text()))
        episodes = episode_metadata(root, info)
        context_file = root / "capture-context.json"
        if context_file.exists():
            from .capture import verify_completion

            verify_completion(root, descriptors)
            capture = json.loads(context_file.read_text())
            if manifest.get("robot_upload_id") and (
                capture["robot_id"] != raw.robot_id or
                capture["collection_task_id"] != raw.collection_task_id
            ):
                raise ValueError("OPENARM_CAPTURE_IDENTITY_MISMATCH")
            sources = capture["episodes"]
            for ep in episodes:
                source = sources[ep["episode_index"]]
                ep.update(source_episode_id=source["source_episode_id"], outcome=source["outcome"])
    return DiscoveredLeRobotV1(
        plan=build_import_plan(
            organization_id=raw.organization_id,
            project_id=raw.project_id,
            region_code=raw.region_code,
            dataset_id=raw.dataset_id,
            collection_task_id=raw.collection_task_id,
            robot_id=raw.robot_id,
            raw_upload_id=raw.raw_source_id,
            raw_manifest_key=raw.manifest_key,
            episode_count=len(episodes),
        ),
        episodes=tuple(episodes),
        source_manifest_sha256=hashlib.sha256(body).hexdigest(),
    )
