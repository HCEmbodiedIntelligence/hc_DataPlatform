import hashlib
import json
from dataclasses import replace

import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.ingest.ports import InMemoryObjectStorage
from hc_data_platform.ingest.raw_sources import RawSource
from hc_data_platform.lerobot_imports.committed import (
    discover_committed_source,
    normalize_committed_manifest,
)

from .test_generic_openarm import FIXTURES


def test_c_discovers_committed_assets_without_browser_session_or_shared_source_directory():
    root = FIXTURES / "valid-openarm"
    storage = InMemoryObjectStorage()
    assets = []
    for i, path in enumerate(sorted(p for p in root.rglob("*") if p.is_file())):
        body = path.read_bytes()
        key = f"robot-ingest/asset-{i}"
        storage.objects[key] = body
        assets.append(
            {
                "path": path.relative_to(root).as_posix(),
                "object_key": key,
                "size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )
    raw = RawSource(
        raw_source_id="raw-" + "a" * 32,
        organization_id="org",
        project_id="project",
        region_code="region",
        upload_id="upload",
        dataset_id="dataset",
        collection_task_id="task",
        robot_id="robot",
        source_format="LEROBOT_V3",
        source_format_version="v3.0",
        manifest_key="committed/manifest.json",
        storage_prefix="robot-ingest",
        content_hash="b" * 64,
        file_count=len(assets),
        total_bytes=sum(a["size"] for a in assets),
    )
    manifest = normalize_committed_manifest(raw, assets)
    storage.objects[raw.manifest_key] = json.dumps(manifest).encode()
    scope = RequestContext(organization_id="org", project_id="project", region_code="region")
    token = bind_request_context(scope)
    try:
        first = discover_committed_source(storage, raw)
        assert first == discover_committed_source(storage, raw)
        assert len(first.plan.episode_tasks) == 2
        assert first.episodes[1]["source_episode_id"] == "source-episode-1"
        assert first.plan.episode_tasks[0].source.raw_upload_id == raw.raw_source_id
        storage.objects[assets[0]["object_key"]] = b"modified"
        with pytest.raises(ValueError, match="INTEGRITY"):
            discover_committed_source(storage, raw)
    finally:
        reset_request_context(token)
    token = bind_request_context(replace(scope, organization_id="other"))
    try:
        with pytest.raises(ValueError, match="SCOPE"):
            discover_committed_source(storage, raw)
    finally:
        reset_request_context(token)
