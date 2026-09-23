"""B's real API -> MinIO -> Temporal -> QC/media/Lance -> review -> export gate.

Run only via B Compose with HC_TEST_POSTGRES_DSN and OPENARM_REAL_INFRA=1.
Authentication is supplied by the test harness; every data/workflow adapter is real.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import zipfile
from dataclasses import replace
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from hc_data_platform.annotation.models import (
    AnnotationTag,
    ReviewDecision,
    TagSchemaDocument,
    TagSchemaTarget,
)
from hc_data_platform.core.config import Settings
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.raw_sources import PostgresRawSourceRepository
from hc_data_platform.lerobot_imports.committed import discover_committed_source
from hc_data_platform.lerobot_imports.router import get_service, router
from hc_data_platform.lerobot_imports.service import LeRobotWebUploadService
from hc_data_platform.publishing.models import ExportFormat, PublishDatasetRequestV1
from hc_data_platform.runtime import _s3, build_runtime
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.tools.lerobot_platform_upload import build_native_source
from hc_data_platform.workflow.activities import (
    configure_activity_dependencies,
    create_aligned_media,
)
from hc_data_platform.workflow.lerobot_workflow import (
    LeRobotImportWorkflow,
    LeRobotImportWorkflowInput,
)
from hc_data_platform.workflow.models import JobRecord
from hc_data_platform.workflow.worker import discover_temporal_registrations
from tests.dashboard.test_dashboard_postgres import (
    isolated_dsn,  # noqa: F401
    migration_uri,
)

from .test_generic_openarm import FIXTURES


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", ["valid-openarm", "valid-generic", "a-actual"])
async def test_real_openarm_roundtrip(isolated_dsn, tmp_path, monkeypatch, fixture):  # noqa: F811
    if os.getenv("OPENARM_REAL_INFRA") != "1":
        pytest.skip("OPENARM_REAL_INFRA=1 requires B PostgreSQL, MinIO and Temporal")
    suffix = uuid4().hex[:12]
    org, project, region = "b-roundtrip-" + suffix, "b-project-" + suffix, "cn-test"
    dataset = "dataset_task_" + hashlib.sha256(suffix.encode()).hexdigest()[:32]
    queue = "openarm-b-test-" + suffix
    monkeypatch.setenv("HC_MEDIA_TEMPORAL_TASK_QUEUE", queue)
    root = FIXTURES / fixture
    capture = json.loads((root / "capture-context.json").read_text())
    task_id, robot_id = capture["collection_task_id"] + "-" + suffix, capture["robot_id"]
    settings = Settings(
        environment="test",
        postgres_dsn=migration_uri(
            os.environ["HC_TEST_POSTGRES_DSN"], conninfo_to_dict(isolated_dsn)["dbname"]
        ),
        auth_abuse_enabled=False,
        object_store_endpoint="http://minio:9000",
        object_store_public_endpoint="http://minio:9000",
        object_store_bucket="openarm-b-tests",
        lance_root_uri=str(tmp_path / "lance"),
        alignment_staging_root=str(tmp_path / "alignment"),
        aligned_media_staging_root=str(tmp_path / "media"),
        media_temporal_task_queue=queue,
        robot_model_asset_root=str(tmp_path / "models"),
    )
    s3, _, storage = _s3(settings)
    try:
        s3.head_bucket(Bucket=settings.object_store_bucket)
    except Exception:
        s3.create_bucket(Bucket=settings.object_store_bucket)
    runtime = build_runtime(settings, include_media=True)
    connect = psycopg_connection_factory(isolated_dsn)
    raw_repository = PostgresRawSourceRepository(connect)
    actor = AuthContext(
        subject_id="b-operator",
        organization_ids=frozenset({org}),
        project_ids=frozenset({project}),
        region_codes=frozenset({region}),
        organization_scope_triples=frozenset({(org, project, region), (org, project, None)}),
        scope_pairs=frozenset({(project, region), (project, None)}),
        capabilities=frozenset(
            {
                "upload.manage",
                "upload.read",
                "data_schema.publish",
                "data_schema.read",
                "annotation_task.claim",
                "annotation.read",
                "annotation.write",
                "annotation.save",
                "annotation.submit",
                "annotation.review",
                "dataset_version.publish",
            }
        ),
    )
    scope = RequestContext(
        organization_id=org,
        project_id=project,
        region_code=region,
        subject_id=actor.subject_id,
        request_id=suffix,
    )
    token = bind_request_context(scope)
    try:
        with connect() as connection:
            connection.execute(
                "INSERT INTO registry.organization_projects VALUES (%s,%s,%s)",
                (org, project, "B synthetic"),
            )
            connection.execute(
                """INSERT INTO collection_tasks.collection_tasks
                (organization_id,project_id,collection_task_id,dataset_id,task_code,name,task_type,
                 scenario,description,target_json,status,version,create_fingerprint,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,'B synthetic','ROBOT','B-test','',
                        '{"package_count":2}','ACTIVE',1,%s,now(),now())""",
                (
                    org,
                    project,
                    task_id,
                    dataset,
                    str(int(suffix, 16) % 100_000_000).zfill(8),
                    "b" * 64,
                ),
            )
        schema_id = "b-labels-" + suffix
        schema = runtime.annotation.create_tag_schema_version(
            project_id=project,
            name="OpenArm outcomes",
            actor=actor,
            schema_id=schema_id,
            document=TagSchemaDocument(
                nodes=tuple(
                    {"tag_id": value, "code": value, "display_name": value}
                    for value in ("success", "failure")
                )
            ),
            compatible_targets=(
                TagSchemaTarget(
                    region_code=region,
                    dataset_id=dataset,
                    dataset_schema_snapshot_id="native-v1",
                    task_kind="TAGGING",
                ),
            ),
        )
        runtime.annotation.publish_tag_schema_version(
            project_id=project, schema_id=schema_id, version=schema.version, actor=actor
        )
        service = LeRobotWebUploadService(storage, raw_sources=raw_repository)
        app = FastAPI()

        @app.exception_handler(ProblemException)
        async def problem_handler(request: Request, exc: ProblemException):
            return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

        @app.middleware("http")
        async def auth(request: Request, call_next):
            request.state.auth_context = actor
            current = bind_request_context(scope)
            try:
                return await call_next(request)
            finally:
                reset_request_context(current)

        app.include_router(router)
        app.dependency_overrides[get_service] = lambda: service
        client = TestClient(app, headers={"X-Organization-Id": org})
        base = f"/api/v1/projects/{project}/regions/{region}/lerobot-imports"
        source = build_native_source(
            root, dataset_id=dataset, collection_task_id=task_id, robot_id=robot_id
        )
        manifest = source.manifest.model_dump(mode="json")
        result = client.post(base, json=manifest)
        assert result.status_code == 201, result.text
        grant = result.json()
        import_id = grant["import_id"]
        for asset in grant["assets"]:
            path = asset["path"]
            upload = client.put(
                f"{base}/{import_id}/assets:upload-part",
                params={
                    "dataset_id": dataset,
                    "path": path,
                    "multipart_upload_id": asset["multipart_upload_id"],
                    "part_number": 1,
                },
                content=source.files[path].read_bytes(),
            )
            assert upload.status_code == 200, upload.text
            completed = client.post(
                f"{base}/{import_id}/assets:complete",
                json={
                    "dataset_id": dataset,
                    "path": path,
                    "multipart_upload_id": asset["multipart_upload_id"],
                    "size": source.files[path].stat().st_size,
                    "part_count": 1,
                },
            )
            assert completed.status_code == 200, completed.text
        committed = client.post(f"{base}/{import_id}:commit", json={"manifest": manifest})
        assert committed.status_code == 200, committed.text
        raw = raw_repository.get_source(
            organization_id=org, project_id=project, region_code=region, raw_source_id=import_id
        )
        discovered = discover_committed_source(storage, raw)
        assert len(discovered.episodes) == len(capture["episodes"])
        assert discover_committed_source(storage, raw) == discovered
        configure_activity_dependencies(runtime.activities)
        temporal = await Client.connect("temporal:7233", data_converter=pydantic_data_converter)
        workflows, activities = discover_temporal_registrations()
        async with Worker(
            temporal,
            task_queue=queue,
            workflows=workflows,
            activities=[*activities, create_aligned_media],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            job = await asyncio.wait_for(
                temporal.execute_workflow(
                    LeRobotImportWorkflow.run,
                    LeRobotImportWorkflowInput(
                        task=discovered.plan.episode_tasks[0],
                        episode_count=len(discovered.episodes),
                    ),
                    id="b-roundtrip-" + suffix,
                    task_queue=queue,
                    result_type=JobRecord,
                ),
                timeout=180,
            )
            assert job.status.value == "SUCCEEDED", job.model_dump()
        episodes = raw_repository.list_episodes(
            organization_id=org, project_id=project, region_code=region, raw_source_id=import_id
        )
        assert all(e.status.value == "READY" for e in episodes)
        from hc_data_platform.quality.postgres import PostgresQualityRepository

        qc_receipts = []
        for episode, task in zip(episodes, discovered.plan.episode_tasks, strict=True):
            report = PostgresQualityRepository(connect).get_report(
                project_id=project, region_code=region, rollout_id=episode.episode_id
            )
            assert report is not None and report.status.value == "PASS"
            assert runtime.activities.lerobot_pipeline.episode_ready(task)
            qc_receipts.append(
                {
                    "episode": episode.source_episode_index,
                    "status": report.status.value,
                    "sha256": report.content_sha256,
                }
            )
        version = max(e.dataset_version for e in episodes)
        for episode, source_episode in zip(episodes, capture["episodes"], strict=True):
            preview = client.get(f"{base}/{import_id}/episodes/{episode.source_episode_index}")
            assert preview.status_code == 200, preview.text
            assert len(preview.json()["videos"]) == len(capture["profile"]["cameras"])
            with connect() as connection:
                annotation_id = connection.execute(
                    "SELECT task_id FROM annotation.annotation_tasks "
                    "WHERE organization_id=%s AND rollout_id=%s",
                    (org, episode.episode_id),
                ).fetchone()[0]
            task = runtime.annotation.claim(annotation_id, actor)
            revision = runtime.annotation.save_draft(
                annotation_id,
                actor,
                [],
                tags=[
                    AnnotationTag(
                        annotation_id="outcome",
                        tag_id=source_episode["outcome"],
                        path=(source_episode["outcome"],),
                        start_step=0,
                        end_step=episode.frame_count,
                    )
                ],
                expected_revision=0,
                if_match=task.etag,
                client_mutation_id="b-outcome",
            )
            submitted = runtime.annotation.submit(
                annotation_id,
                actor,
                expected_revision=revision.revision,
                if_match=runtime.annotation.get_task(annotation_id).etag,
            )
            runtime.annotation.review(
                annotation_id,
                replace(actor, subject_id="b-reviewer"),
                ReviewDecision.APPROVE,
                revision=revision.revision,
                if_match=submitted.etag,
            )
        published = runtime.publisher.publish(
            PublishDatasetRequestV1(
                project_id=project,
                dataset_id=dataset,
                dataset_version="openarm-v1",
                base_lance_version=str(version),
            )
        )
        assert len(published.rollouts) == len(episodes)
        exported = runtime.exporter.export(published, format=ExportFormat.LEROBOT_V3)
        url = runtime.exporter.authorize_download(exported)
        with urlopen(url) as response:
            content = response.read()
        assert hashlib.sha256(content).hexdigest() == exported.artifact_content_hash
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            info = json.loads(archive.read("meta/info.json"))
            rows = pq.read_table(
                pa.BufferReader(archive.read("data/chunk-000/file-000.parquet"))
            ).to_pylist()
            annotations = json.loads(archive.read("meta/annotations.json"))
            original = pq.read_table(root / "data").to_pylist()
            mappings = [
                json.loads(line)
                for line in (root / "source_mapping.jsonl").read_text().splitlines()
            ]
            for row, source_row, mapping in zip(rows, original, mappings, strict=True):
                source_metadata = json.loads(row["hc.source.metadata.source"])
                assert source_metadata["source_mapping"] == mapping
                assert source_metadata["source_timestamp"] == source_row["timestamp"]
                for key in ("episode_index", "frame_index", "timestamp", "task_index"):
                    assert row[key] == source_row[key]
                assert row["hc.time_error_ns.action"] <= 1_000
                assert row["hc.repeated.action"] is False
            for key in ("action", "observation.state"):
                np.testing.assert_allclose(
                    [r[key] for r in rows], [r[key] for r in original], rtol=1e-6, atol=1e-7
                )
                assert info["features"][key]["names"] == [
                    a["name"] for a in capture["profile"]["axes"]
                ]
                assert info["features"][key]["units"] == [
                    a["unit"] for a in capture["profile"]["axes"]
                ]
            assert [e["source_episode"] for e in annotations["episodes"]] == capture["episodes"]
            assert all(e["capture_context"] == capture for e in annotations["episodes"])
            assert all(
                e["tags"][0]["ranges"]
                == [
                    {
                        "start_frame": 0,
                        "end_frame": episodes[i].frame_count,
                        "source_start_step": 0,
                        "source_end_step": episodes[i].frame_count,
                    }
                ]
                for i, e in enumerate(annotations["episodes"])
            )
            output = Path(os.getenv("OPENARM_ROUNDTRIP_OUTPUT", str(tmp_path / "output"))) / fixture
            output.mkdir(parents=True, exist_ok=True)
            archive.extractall(output)
            output.with_suffix(".zip").write_bytes(content)
        files = client.get(f"{base}/{import_id}/files", params={"limit": 100}).json()["files"]
        for item in files:
            grant = client.get(
                f"{base}/{import_id}/assets:read", params={"path": item["path"], "download": "true"}
            )
            assert grant.status_code == 200
            with urlopen(grant.json()["url"]) as response:
                downloaded = response.read()
            assert hashlib.sha256(downloaded).hexdigest() == item["sha256"]
            assert downloaded == (root / item["path"]).read_bytes()
        evidence = {
            "fixture": fixture,
            "raw_source_id": import_id,
            "workflow_id": job.workflow_id,
            "status": job.status.value,
            "qc": qc_receipts,
            "source_mapping_and_time_grid": "exact",
            "episodes": len(episodes),
            "frames": len(rows),
            "cameras": len(capture["profile"]["cameras"]),
            "downloaded_assets": len(files),
            "export_sha256": exported.artifact_content_hash,
            "source_marker": json.loads((root / "export-complete.json").read_text()),
            "auth": "test principal injection",
            "adapters": "production PostgreSQL/MinIO/Temporal/QC/media/Lance/annotation/publishing",
        }
        output.with_suffix(".evidence.json").write_text(json.dumps(evidence, indent=2))
    finally:
        reset_request_context(token)
