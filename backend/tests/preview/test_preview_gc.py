from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hc_data_platform.core.context import current_request_context
from hc_data_platform.preview.gc import (
    PreviewGarbageCollector,
    PreviewStagingSweeper,
    _run_global_gc,
)
from hc_data_platform.preview.memory import (
    InMemoryPreviewArtifactStore,
    InMemoryPreviewRepository,
)
from hc_data_platform.preview.models import (
    EncodedPreviewArtifactV1,
    PreviewArtifactV1,
    PreviewRequestV1,
    PreviewScopeV1,
)
from hc_data_platform.preview.service import PREVIEW_PIPELINE_REVISION, preview_artifact_key

NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)
SCOPE = PreviewScopeV1(
    organization_id="organization-1",
    project_id="project-1",
    region_code="cn-test",
)
SCOPE_TWO = PreviewScopeV1(
    organization_id="organization-2",
    project_id="project-2",
    region_code="cn-test",
)


def _ready(
    repository: InMemoryPreviewRepository,
    store: InMemoryPreviewArtifactStore,
    *,
    camera_id: str,
    created_at: datetime,
    ttl: timedelta,
    scope: PreviewScopeV1 = SCOPE,
) -> PreviewArtifactV1:
    request = PreviewRequestV1(
        project_id=scope.project_id,
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        lance_version="1",
        camera_id=camera_id,
        start_step=0,
        end_step=30,
    )
    key = preview_artifact_key(request)
    artifact, job = repository.ensure_artifact_job(
        scope=scope,
        artifact_key=key,
        request=request,
        pipeline_revision=PREVIEW_PIPELINE_REVISION,
        rebuild_source_id="lance:dataset-1:1:rollout-1",
        artifact_ttl=ttl,
        now=created_at,
    )
    assert job is not None
    publication = store.publish(
        project_id=scope.project_id,
        artifact_key=key,
        encoded=EncodedPreviewArtifactV1(
            artifact_uri="memory://encoded/index.m3u8",
            duration_seconds=1,
            frame_count=30,
        ),
    )
    return repository.mark_ready(
        scope=scope,
        job_id=job.job_id,
        artifact_key=artifact.artifact_key,
        publication=publication,
        frame_count=30,
        duration_seconds=1,
        now=created_at,
    )


def _collector(
    repository: InMemoryPreviewRepository,
    store: InMemoryPreviewArtifactStore,
    *,
    project_quota: int = 10_000,
    global_quota: int = 10_000,
) -> PreviewGarbageCollector:
    return PreviewGarbageCollector(
        repository,
        store,
        project_quota_bytes=project_quota,
        global_quota_bytes=global_quota,
        high_watermark_percent=80,
        low_watermark_percent=40,
        clock=lambda: NOW,
    )


def test_ttl_gc_deletes_exact_objects_then_database_metadata() -> None:
    repository = InMemoryPreviewRepository()
    store = InMemoryPreviewArtifactStore()
    artifact = _ready(
        repository,
        store,
        camera_id="front",
        created_at=NOW - timedelta(days=2),
        ttl=timedelta(days=1),
    )
    expected_keys = {item.key for item in artifact.objects}

    result = _collector(repository, store).run_once()

    assert result.deleted_artifacts == 1
    assert result.deleted_bytes == artifact.total_bytes
    assert result.failed_artifacts == 0
    assert set(store.delete_calls[0]) == expected_keys
    assert store.objects == {}
    assert repository.storage_totals() == (0, 0)


def test_high_watermark_uses_lru_but_never_deletes_a_held_artifact() -> None:
    repository = InMemoryPreviewRepository()
    store = InMemoryPreviewArtifactStore()
    held = _ready(
        repository,
        store,
        camera_id="held",
        created_at=NOW - timedelta(hours=3),
        ttl=timedelta(days=30),
    )
    middle = _ready(
        repository,
        store,
        camera_id="middle",
        created_at=NOW - timedelta(hours=2),
        ttl=timedelta(days=30),
    )
    newest = _ready(
        repository,
        store,
        camera_id="newest",
        created_at=NOW - timedelta(hours=1),
        ttl=timedelta(days=30),
    )
    repository.set_protection(SCOPE, held.artifact_key, legal_hold=True)
    total, _ = repository.storage_totals()

    result = _collector(
        repository,
        store,
        project_quota=total,
        global_quota=total * 10,
    ).run_once()

    remaining_keys = set(store.objects)
    assert result.deleted_artifacts == 2
    assert all(item.key in remaining_keys for item in held.objects)
    assert all(item.key not in remaining_keys for item in (*middle.objects, *newest.objects))
    assert repository.storage_totals() == (held.total_bytes, 1)


def test_global_high_watermark_coordinates_lru_across_rls_scopes() -> None:
    repository = InMemoryPreviewRepository()
    store = InMemoryPreviewArtifactStore()
    held = _ready(
        repository,
        store,
        camera_id="held",
        created_at=NOW - timedelta(hours=3),
        ttl=timedelta(days=30),
        scope=SCOPE,
    )
    middle = _ready(
        repository,
        store,
        camera_id="middle",
        created_at=NOW - timedelta(hours=2),
        ttl=timedelta(days=30),
        scope=SCOPE,
    )
    newest = _ready(
        repository,
        store,
        camera_id="newest",
        created_at=NOW - timedelta(hours=1),
        ttl=timedelta(days=30),
        scope=SCOPE_TWO,
    )
    repository.set_protection(SCOPE, held.artifact_key, governance_hold=True)
    held = held.model_copy(update={"governance_hold": True})
    remaining = {
        held.artifact_id: held,
        middle.artifact_id: middle,
        newest.artifact_id: newest,
    }

    class ScopedCollector:
        global_quota_bytes = sum(item.total_bytes for item in remaining.values())
        high_watermark_percent = 80
        low_watermark_percent = 40
        batch_size = 10

        @staticmethod
        def now() -> datetime:
            return NOW

        @staticmethod
        def _visible() -> list[PreviewArtifactV1]:
            context = current_request_context()
            return [
                item
                for item in remaining.values()
                if item.scope.organization_id == context.organization_id
                and item.scope.project_id == context.project_id
                and item.scope.region_code == context.region_code
            ]

        def storage_totals(self) -> tuple[int, int]:
            visible = self._visible()
            return sum(item.total_bytes for item in visible), len(visible)

        def oldest_reclaimable(self, *, now: datetime) -> PreviewArtifactV1 | None:
            del now
            candidates = [
                item
                for item in self._visible()
                if not item.legal_hold
                and not item.governance_hold
                and item.active_reference_count == 0
            ]
            return min(candidates, key=lambda item: item.last_accessed_at, default=None)

        @staticmethod
        def reclaim_candidate(artifact: PreviewArtifactV1) -> tuple[bool, int]:
            removed = remaining.pop(artifact.artifact_id, None)
            if removed is None:
                return False, 0
            return True, store.delete_exact(artifact.objects)

    _run_global_gc(
        ScopedCollector(),  # type: ignore[arg-type]
        (
            "organization-1/project-1/cn-test",
            "organization-2/project-2/cn-test",
        ),
    )

    assert set(remaining) == {held.artifact_id}
    assert all(item.key in store.objects for item in held.objects)
    assert all(item.key not in store.objects for item in (*middle.objects, *newest.objects))


def test_staging_sweeper_removes_only_expired_direct_children(tmp_path: Path) -> None:
    root = tmp_path / "preview-staging"
    expired = root / "artifact-expired"
    active = root / "artifact-active"
    expired.mkdir(parents=True)
    active.mkdir()
    (expired / "index.m3u8").write_text("expired", encoding="utf-8")
    (active / "index.m3u8").write_text("active", encoding="utf-8")
    old_timestamp = (NOW - timedelta(hours=25)).timestamp()
    os.utime(expired, (old_timestamp, old_timestamp))

    deleted = PreviewStagingSweeper(
        (root,),
        ttl=timedelta(hours=24),
        clock=lambda: NOW,
    ).run_once()

    assert deleted == 1
    assert not expired.exists()
    assert active.exists()
