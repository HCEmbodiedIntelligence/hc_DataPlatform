"""Actual native upload -> Temporal/QC -> package/official previews/annotation.

Synthetic RGB-D recordings exercise real services, not real sensor acceptance.
The API test principal is explicit; production login is covered separately.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import zipfile
from dataclasses import replace
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

import psycopg
import pyarrow.parquet as pq
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer, Worker

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
from tests.dashboard.test_dashboard_postgres import migration_uri

from .native_package_fixture import make_native_rgbd


@pytest.fixture
def isolated_dsn():
    dsn = os.environ.get("HC_G1_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("explicit G1 PostgreSQL required; no production fallback")
    return dsn


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "condition", ["complete", "missing-mask", "profile-mismatch", "frame-mismatch"]
)
async def test_native_rgbd_package_roundtrip(isolated_dsn, tmp_path, monkeypatch, condition):
    if os.getenv("OPENARM_REAL_INFRA") != "1":
        pytest.skip("OPENARM_REAL_INFRA=1 requires B PostgreSQL, MinIO and Temporal")
    suffix = uuid4().hex[:12]
    org, project, region = "b-roundtrip-" + suffix, "b-project-" + suffix, "cn-test"
    dataset = "dataset_task_" + hashlib.sha256(suffix.encode()).hexdigest()[:32]
    queue = "openarm-b-test-" + suffix
    monkeypatch.setenv("HC_MEDIA_TEMPORAL_TASK_QUEUE", queue)
    root = make_native_rgbd(tmp_path / "source")
    if condition == "missing-mask":
        (root / "hc/depth/head/validity/flags.parquet").unlink()
    elif condition == "profile-mismatch":
        path = root / "hc/depth/head/profile.json"
        value = json.loads(path.read_text())
        value["depth_max"] = 9
        path.write_text(json.dumps(value))
    elif condition == "frame-mismatch":
        path = root / "hc/depth/head/frames.parquet"
        table = pq.read_table(path)
        rows = table.to_pylist()
        rows[0]["source_timestamp_ns"] += 1
        import pyarrow as pa

        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)
    task_id, robot_id = "native-task-" + suffix, "fixture-robot"
    settings = Settings(
        environment="test",
        postgres_dsn=migration_uri(
            os.environ["HC_TEST_POSTGRES_DSN"], conninfo_to_dict(isolated_dsn)["dbname"]
        ),
        auth_abuse_enabled=False,
        object_store_endpoint="http://minio:9000",
        object_store_public_endpoint="http://minio:9000",
        object_store_bucket="hc-data-local",
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
                "annotation_task.read",
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
        assert len(discovered.episodes) == 1
        assert discover_committed_source(storage, raw) == discovered
        from hc_data_platform.lance_catalog.adapters import LanceAdapter
        from tests.episode_store.test_bundle_assets_services import forbidden

        monkeypatch.setattr(LanceAdapter, "stage_attempt", forbidden)
        monkeypatch.setattr(LanceAdapter, "commit_staged", forbidden)
        monkeypatch.setattr(runtime.activities.catalog, "commit_fragment", forbidden)
        configure_activity_dependencies(runtime.activities)
        # Losing the commit response after product projection must recover from
        # the immutable package, without encoding its depth or preview again.
        projector = runtime.activities.dataset_ingest_projection
        original_project = projector.project
        projection_attempts = []

        def lose_response_once(**arguments):
            value = original_project(**arguments)
            projection_attempts.append(1)
            if len(projection_attempts) == 1:
                raise RuntimeError("injected lost native commit response")
            return value

        monkeypatch.setattr(projector, "project", lose_response_once)
        preview_encodes = []
        encode = runtime.episode_packages.previews._encode

        def count_preview(*args, **kwargs):
            preview_encodes.append(1)
            return encode(*args, **kwargs)

        monkeypatch.setattr(runtime.episode_packages.previews, "_encode", count_preview)
        temporal = await Client.connect(
            "temporal:7233", namespace="chayi-g1", data_converter=pydantic_data_converter
        )
        workflows, activities = discover_temporal_registrations()
        async with Worker(
            temporal,
            task_queue=queue,
            workflows=workflows,
            activities=[*activities, create_aligned_media],
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
            if condition != "complete":
                assert job.status.value == "TECHNICAL_FAILED", job.model_dump()
                assert job.error_code == (
                    "LEROBOT_DEPTH_EVIDENCE_REQUIRED"
                    if condition == "missing-mask"
                    else "VALIDATION_FAILED"
                )
                episodes = raw_repository.list_episodes(
                    organization_id=org,
                    project_id=project,
                    region_code=region,
                    raw_source_id=import_id,
                )
                assert all(e.status.value == "FAILED" and e.data_ref is None for e in episodes)
                with connect() as connection:
                    for relation in (
                        "episode_store.versions",
                        "lance_rollout_lineage",
                        "annotation.annotation_tasks",
                    ):
                        assert (
                            connection.execute(
                                "SELECT count(*) FROM " + relation + " WHERE organization_id=%s",
                                (org,),
                            ).fetchone()[0]
                            == 0
                        )
                assert preview_encodes == []
                assert (
                    raw_repository.get_source(
                        organization_id=org,
                        project_id=project,
                        region_code=region,
                        raw_source_id=import_id,
                    ).content_hash
                    == raw.content_hash
                )
                for relative, file in source.files.items():
                    access = client.get(
                        f"{base}/{import_id}/assets:read",
                        params={"path": relative, "download": "true"},
                    )
                    assert access.status_code == 200, access.text
                    with urlopen(access.json()["url"]) as response:
                        assert response.read() == file.read_bytes()
                evidence_root = Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", str(tmp_path)))
                (evidence_root / f"native-package-{condition}.json").write_text(
                    json.dumps(
                        {
                            "source": "synthetic RGB-D, deliberately invalid evidence",
                            "condition": condition,
                            "status": job.status.value,
                            "error_code": job.error_code,
                            "workflow_id": job.workflow_id,
                            "packages": 0,
                            "annotation_tasks": 0,
                            "lance_writes": 0,
                            "authorized_raw_downloads_byte_identical": len(source.files),
                        },
                        indent=2,
                    )
                    + "\n"
                )
                return
            assert job.status.value == "SUCCEEDED", job.model_dump()
            assert len(projection_attempts) == 2 and len(preview_encodes) == 2
            history = await temporal.get_workflow_handle(job.workflow_id).fetch_history()
            child_ids = [
                e.child_workflow_execution_started_event_attributes.workflow_execution.workflow_id
                for e in history.events
                if e.HasField("child_workflow_execution_started_event_attributes")
            ]
            child_history = await temporal.get_workflow_handle(child_ids[0]).fetch_history()
        replayer = Replayer(workflows=workflows, data_converter=pydantic_data_converter)
        await replayer.replay_workflow(history)
        await replayer.replay_workflow(child_history)
        episodes = raw_repository.list_episodes(
            organization_id=org, project_id=project, region_code=region, raw_source_id=import_id
        )
        assert all(e.status.value == "READY" for e in episodes)
        assert len(episodes) == 1
        episode = episodes[0]
        assert episode.lance_version is None and episode.data_ref is not None
        data = episode.data_ref
        for update in (
            "data_ref=jsonb_set(data_ref,'{content_hash}','\"0000000000000000000000000000000000000000000000000000000000000000\"')",
            "dataset_version=dataset_version+1",
            "sample_count=frame_count+1",
            "data_ref=NULL,lance_version=NULL,quality_status=NULL",
        ):
            with pytest.raises(psycopg.Error), connect() as connection:
                # SQL clauses are test constants; target identity stays parameterized.
                connection.execute(
                    "UPDATE ingest.raw_source_episodes SET "
                    + update
                    + " WHERE organization_id=%s AND raw_source_id=%s",
                    (org, import_id),
                )
        with connect() as connection, connection.transaction(force_rollback=True):
            # Robot frame_count may describe a larger source window; the exact
            # committed output binding is sample_count. No facts persist here.
            connection.execute(
                "UPDATE ingest.raw_source_episodes SET sample_count=frame_count,"
                "frame_count=frame_count+1 WHERE organization_id=%s AND raw_source_id=%s",
                (org, import_id),
            )
        package = runtime.episode_packages.files.open(data.identity, data.data_version_id)
        from hc_data_platform.episode_store.download import complete_archive
        from hc_data_platform.episode_store.validation import validate_package
        from hc_data_platform.publishing.depth_evidence import file_hash

        path, temporary = complete_archive(package, tmp_path / "downloads")
        try:
            output = tmp_path / "offline"
            with zipfile.ZipFile(path) as archive:
                archive.extractall(output)
            downloaded = output / "episode"
            validate_package(downloaded, package.manifest.input)
            for key in ("head_rgb", "head_depth"):
                relative = f"videos/observation.images.{key}/chunk-000/file-000.mp4"
                assert file_hash(downloaded / relative) == file_hash(root / relative)
            values = pq.read_table(downloaded / "data").to_pylist()
            original = pq.read_table(root / "data").to_pylist()
            for key in ("action", "observation.state", "observation.extra.matrix"):
                assert [r[key] for r in values] == [r[key] for r in original]
            for entry in package.manifest.files:
                assert file_hash(downloaded / entry.path) == entry.sha256
            target = Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", str(tmp_path)))
            offline = target / "native-package-offline" / data.data_version_id
            shutil.copytree(downloaded, offline)
            receipt_path = downloaded / "hc/commit.json"
            body = receipt_path.read_bytes()
            tampered = json.loads(body)
            tampered["data"]["content_hash"] = "0" * 64
            receipt_path.write_text(json.dumps(tampered))
            with pytest.raises(ValueError, match="receipt/manifest/content"):
                validate_package(downloaded, package.manifest.input)
            receipt_path.write_bytes(body)
        finally:
            temporary.cleanup()
        with connect() as connection:
            task = connection.execute(
                "SELECT data_ref,base_lance_version FROM annotation.annotation_tasks "
                "WHERE organization_id=%s AND rollout_id=%s",
                (org, episode.episode_id),
            ).fetchone()
            assert task == (data.model_dump(mode="json"), None)
            assert (
                connection.execute(
                    "SELECT count(*) FROM lance_rollout_lineage WHERE organization_id=%s",
                    (org,),
                ).fetchone()[0]
                == 0
            )
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
                    annotation_id="result",
                    tag_id="success",
                    path=("success",),
                    start_step=0,
                    end_step=episode.frame_count,
                )
            ],
            expected_revision=0,
            if_match=task.etag,
            client_mutation_id="native-result",
        )
        submitted = runtime.annotation.submit(
            annotation_id,
            actor,
            expected_revision=revision.revision,
            if_match=runtime.annotation.get_task(annotation_id).etag,
        )
        runtime.annotation.review(
            annotation_id,
            replace(actor, subject_id="native-reviewer"),
            ReviewDecision.APPROVE,
            revision=revision.revision,
            if_match=submitted.etag,
        )
        # READY retry reuses the package and never creates another collection.
        assert runtime.activities.lerobot_pipeline.episode_ready(discovered.plan.episode_tasks[0])
        assert runtime.episode_packages.collections.get(dataset).version == 1
        evidence = {
            "source": "synthetic RGB-D, 6 frames, 25 Hz",
            "status": job.status.value,
            "workflow_id": job.workflow_id,
            "data": data.model_dump(mode="json"),
            "lance_writes": 0,
            "depth_requantized": False,
            "rgb_transcoded": False,
            "official_previews": True,
            "full_download_validated": True,
            "lost_commit_response_recovered": True,
            "parent_and_child_history_replayed": True,
            "tag_save_submit_review": True,
            "offline_package": str(offline),
            "authorization": "API test principal; not real login/role acceptance",
        }
        target = Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", str(tmp_path)))
        target.mkdir(parents=True, exist_ok=True)
        (target / "native-package-roundtrip.json").write_text(json.dumps(evidence, indent=2) + "\n")
        (target / "native-package-parent-history.json").write_text(history.to_json())
        (target / "native-package-child-history.json").write_text(child_history.to_json())
    finally:
        reset_request_context(token)
        runtime.episode_packages.close()
