from __future__ import annotations

from pathlib import Path

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.raw_sources import (
    CommittedRawSourceGraph,
    InMemoryRawSourceRepository,
    RawIngestJob,
    RawIngestJobType,
    RawSource,
    RawSourceEpisode,
    RawSourceFormat,
)


def _graph(*, content_hash: str = "a" * 64) -> CommittedRawSourceGraph:
    source = RawSource(
        raw_source_id="raw-1",
        organization_id="org-1",
        project_id="project-1",
        region_code="cn-hz",
        upload_id="upload-1",
        dataset_id="dataset-1",
        collection_task_id="task-1",
        robot_id="robot-1",
        source_format=RawSourceFormat.LEROBOT_V3,
        source_format_version="v3.0",
        manifest_key="raw/org-1/dataset-1/upload-1/manifest.json",
        storage_prefix="raw/org-1/dataset-1/upload-1/source",
        content_hash=content_hash,
        file_count=4,
        total_bytes=1024,
    )
    return CommittedRawSourceGraph(
        source=source,
        episodes=(
            RawSourceEpisode(
                organization_id=source.organization_id,
                project_id=source.project_id,
                region_code=source.region_code,
                raw_source_id=source.raw_source_id,
                episode_id="episode-0",
                source_episode_index=0,
            ),
        ),
        job=RawIngestJob(
            organization_id=source.organization_id,
            project_id=source.project_id,
            region_code=source.region_code,
            job_id="job-1",
            raw_source_id=source.raw_source_id,
            job_type=RawIngestJobType.LEROBOT_IMPORT,
            adapter_name="lerobot_v3",
        ),
    )


def test_raw_source_graph_is_idempotent_and_rejects_changed_content() -> None:
    repository = InMemoryRawSourceRepository()
    graph = _graph()

    assert repository.register_committed(graph).source == graph.source
    assert repository.register_committed(graph).source == graph.source
    assert (
        repository.list_episodes(
            organization_id="org-1",
            project_id="project-1",
            region_code="cn-hz",
            raw_source_id="raw-1",
        )
        == graph.episodes
    )

    with pytest.raises(ProblemException) as captured:
        repository.register_committed(_graph(content_hash="b" * 64))
    assert captured.value.problem.code == "RAW_SOURCE_IDENTITY_CONFLICT"


def test_raw_source_migration_persists_locations_lineage_jobs_and_status_sync() -> None:
    migration = (
        Path(__file__).resolve().parents[2] / "migrations" / "ingest" / "010_raw_sources.sql"
    ).read_text(encoding="utf-8")

    for relation in ("raw_sources", "raw_source_episodes", "raw_ingest_jobs"):
        assert f"ingest.{relation}" in migration
    for column in (
        "source_format",
        "source_format_version",
        "manifest_key",
        "storage_prefix",
        "content_hash",
        "processing_status",
    ):
        assert column in migration
    assert "FROM ingest.rollout_objects object" in migration
    assert "workflow_jobs_sync_raw_source" in migration
    assert "LEROBOT_V3" in migration
