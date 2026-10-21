"""Real isolated PostgreSQL/MinIO/Temporal; no production endpoints or data retries."""

import asyncio
import copy
import io
import json
import os
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer, Worker

from hc_data_platform.capture_schemes.processing import resolve_raw_snapshot, save_raw_snapshot
from hc_data_platform.capture_schemes.transactions import bind_raw_snapshot
from hc_data_platform.core.config import Settings
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.episode_store.collection_export import CollectionSource, write_collection
from hc_data_platform.episode_store.reader import ParquetStepReader
from hc_data_platform.ingest.raw_sources import PostgresRawSourceRepository
from hc_data_platform.lerobot_imports.committed import discover_committed_source
from hc_data_platform.lerobot_imports.models import CommitLeRobotImportV1, CompleteLeRobotAssetV1
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

from .processing_fixture import make_source, output_for, snapshot


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("variant", [0, 1, 2, "optional"])
async def test_frozen_x1_without_annotation_roundtrip(tmp_path, monkeypatch, variant):
    if os.getenv("HC_CAPTURE_PROCESSING_INFRA") != "1":
        pytest.skip("explicit isolated infrastructure required")
    suffix = uuid4().hex
    org, project, region = "processing-" + suffix, "p", "global"
    dataset, task, robot = "dataset_capture_" + suffix, "task-" + suffix, "x1"
    queue = "capture-" + suffix
    scope = (org, project, region)
    settings = Settings(
        environment="test",
        postgres_dsn=os.environ["HC_TEST_POSTGRES_DSN"],
        auth_abuse_enabled=False,
        object_store_endpoint="http://minio:9000",
        object_store_public_endpoint="http://minio:9000",
        object_store_access_key="minioadmin",
        object_store_secret_key="minioadmin",
        object_store_bucket="processing-tests",
        lance_root_uri=str(tmp_path / "lance"),
        alignment_staging_root=str(tmp_path / "alignment"),
        aligned_media_staging_root=str(tmp_path / "media"),
        media_temporal_task_queue=queue,
        robot_model_asset_root=str(tmp_path / "models"),
    )
    monkeypatch.setenv("HC_MEDIA_TEMPORAL_TASK_QUEUE", queue)
    s3, _, storage = _s3(settings)
    if not any(b["Name"] == settings.object_store_bucket for b in s3.list_buckets()["Buckets"]):
        s3.create_bucket(Bucket=settings.object_store_bucket)
    runtime = build_runtime(settings, include_media=True)
    connect = psycopg_connection_factory(settings.postgres_dsn)
    repository = PostgresRawSourceRepository(connect, capture_snapshot_writer=bind_raw_snapshot)
    actor = AuthContext(
        subject_id="test",
        organization_ids=frozenset({org}),
        project_ids=frozenset({project}),
        region_codes=frozenset({region}),
        organization_scope_triples=frozenset({scope, (org, project, None)}),
        scope_pairs=frozenset({(project, region), (project, None)}),
        capabilities=frozenset({"upload.manage", "upload.read"}),
    )
    token = bind_request_context(
        RequestContext(
            organization_id=org, project_id=project, region_code=region, subject_id="test"
        )
    )
    try:
        with connect() as db:
            db.execute(
                "INSERT INTO registry.organization_projects VALUES(%s,%s,'processing fixture')",
                (org, project),
            )
            db.execute(
                """INSERT INTO collection_tasks.collection_tasks
                       (organization_id,project_id,collection_task_id,dataset_id,task_code,name,
                        task_type,scenario,create_fingerprint)
                       VALUES(%s,%s,%s,%s,lpad(nextval('collection_tasks.task_code_sequence')::text,8,'0'),
                              'processing fixture','test','test',%s)""",
                (org, project, task, dataset, "f" * 64),
            )
        optional = variant == "optional"
        index = 0 if optional else variant
        info = make_source(tmp_path / "source", index, count=167 if index < 2 else 30)
        if optional:
            table_path = tmp_path / "source/data/chunk-000/file-000.parquet"
            table = pq.read_table(table_path)
            for name, dtype, values in (
                ("observation.effort", pa.float64(), [[0, 99999]] + [[0, 2]] * 166),
                ("observation.effort_valid", pa.bool_(), [[True, False]] + [[True, True]] * 166),
            ):
                table = table.append_column(name, pa.array(values, type=pa.list_(dtype, 2)))
                info["features"][name] = {"dtype": str(dtype), "shape": [2], "names": ["x", "y"]}
            pq.write_table(table, table_path)
            (tmp_path / "source/meta/info.json").write_text(json.dumps(info))
        original = build_native_source(
            tmp_path / "source", dataset_id=dataset, collection_task_id=task, robot_id=robot
        )
        output = output_for(info)
        if optional:
            from hc_data_platform.capture_schemes.models import OutputContract

            document = output.model_dump(mode="json")
            document["features"]["observation.effort"].update(
                required=False, validity_feature="observation.effort_valid"
            )
            document["features"]["observation.effort_valid"]["role"] = "validity"
            document["features"]["observation.absent"] = {
                "dtype": "float64",
                "shape": [2],
                "names": ["x", "y"],
                "role": "numeric",
                "required": False,
            }
            document["features"]["observation.images.optional"] = {
                "dtype": "video",
                "shape": [400, 640, 3],
                "role": "rgb",
                "required": False,
            }
            output = OutputContract(**document)
        frozen = snapshot(
            output,
            scope=scope,
            raw={
                "dataset_id": dataset,
                "collection_task_id": task,
                "robot_id": robot,
                "source_format": "LEROBOT_V3",
            },
        )
        service = LeRobotWebUploadService(
            storage,
            raw_sources=repository,
            capture_plans=SimpleNamespace(prepare=lambda *args, **kwargs: frozen),
        )
        args = dict(auth=actor, organization_id=org, project_id=project, region_code=region)
        grant = service.begin(**args, manifest=original.manifest)
        for asset in grant.assets:
            content = original.files[asset.path].read_bytes()
            service.upload_part(
                **args,
                import_id=grant.import_id,
                dataset_id=dataset,
                path=asset.path,
                multipart_upload_id=asset.multipart_upload_id,
                part_number=1,
                body=io.BytesIO(content),
                size=len(content),
            )
            service.complete_asset(
                **args,
                import_id=grant.import_id,
                command=CompleteLeRobotAssetV1(
                    dataset_id=dataset,
                    path=asset.path,
                    multipart_upload_id=asset.multipart_upload_id,
                    size=len(content),
                    part_count=1,
                ),
            )
        service.commit(
            **args,
            import_id=grant.import_id,
            command=CommitLeRobotImportV1(manifest=original.manifest),
        )
        raw = repository.get_source(
            organization_id=org,
            project_id=project,
            region_code=region,
            raw_source_id=grant.import_id,
        )
        with connect() as db:
            assert resolve_raw_snapshot(db, scope, raw.raw_source_id) == frozen
            save_raw_snapshot(db, scope, raw.raw_source_id, frozen)
            save_raw_snapshot(db, scope, raw.raw_source_id, frozen)
            assert resolve_raw_snapshot(db, scope, raw.raw_source_id) == frozen
        altered = copy.deepcopy(frozen)
        altered["mapping_version"] += 1
        with pytest.raises(ProblemException), connect() as db:
            save_raw_snapshot(db, scope, raw.raw_source_id, altered)
        with connect() as db:
            assert resolve_raw_snapshot(db, scope, raw.raw_source_id) == frozen
        discovered = discover_committed_source(storage, raw)
        configure_activity_dependencies(runtime.activities)
        temporal = await Client.connect("temporal:7233", data_converter=pydantic_data_converter)
        workflows, activities = discover_temporal_registrations()
        async with Worker(
            temporal,
            task_queue=queue,
            workflows=workflows,
            activities=[*activities, create_aligned_media],
        ):
            handle = await temporal.start_workflow(
                LeRobotImportWorkflow.run,
                LeRobotImportWorkflowInput(task=discovered.plan.episode_tasks[0], episode_count=1),
                id="capture-test-" + suffix,
                task_queue=queue,
                result_type=JobRecord,
            )
            result = await asyncio.wait_for(handle.result(), 180)
        assert result.status.value == "SUCCEEDED", result.model_dump(mode="json")
        episodes = repository.list_episodes(
            organization_id=org,
            project_id=project,
            region_code=region,
            raw_source_id=raw.raw_source_id,
        )
        assert len(episodes) == 1 and episodes[0].status.value == "READY"
        data = episodes[0].data_ref
        assert data.frame_count == info["total_frames"]
        package = runtime.episode_packages.files.open(data.identity, data.data_version_id)
        assert package.document("meta/info.json")["fps"] == 15
        window = ParquetStepReader(runtime.episode_packages.files).read_steps(
            data.identity, data.data_version_id, 0, 2
        )
        assert [step.step_index for step in window.steps] == [0, 1]
        manifest, _ = write_collection(
            tmp_path / "export", [CollectionSource(package)], modalities="rgb"
        )
        assert manifest["total_frames"] == info["total_frames"]
        exported = json.loads((tmp_path / "export/meta/info.json").read_text())
        assert exported["fps"] == 15
        from hc_data_platform.capture_schemes.plans import check_dataset

        incompatible = copy.deepcopy(frozen["output"])
        incompatible["features"]["action"]["names"].reverse()
        with pytest.raises(ProblemException) as conflict, connect() as db:
            check_dataset(db, scope, dataset, task, incompatible, freeze=True)
        assert conflict.value.problem.code == "CAPTURE_DATASET_INCOMPATIBLE"
        if optional:
            from hc_data_platform.capture_schemes.schema import native_topic

            step = window.steps[0]
            assert step.modalities[native_topic("observation.effort")] == [0, 99999]
            assert step.axis_validity[native_topic("observation.effort")] == (True, False)
            assert step.presence[native_topic("observation.absent")] is False
            assert step.modalities[native_topic("observation.absent")] is None
            assert step.sample_valid  # Only optional axes are unavailable.
            assert not list((tmp_path / "export").rglob("*.mp4"))
            stats = json.loads((tmp_path / "export/meta/stats.json").read_text())
            assert stats["observation.effort"]["mean"] == [0, 2]
            exported_data = pq.read_table(tmp_path / "export/data/chunk-000/file-000.parquet")
            assert json.loads(exported_data["hc.axis_validity.observation.effort"][0].as_py()) == [
                True,
                False,
            ]
            assert exported["hc.capture_presence"]["observation.absent"] is False
        with connect() as db:
            assert (
                db.execute(
                    "SELECT count(*) FROM annotation.annotation_tasks WHERE organization_id=%s",
                    (org,),
                ).fetchone()[0]
                == 0
            )
        await Replayer(workflows=workflows, data_converter=pydantic_data_converter).replay_workflow(
            await handle.fetch_history()
        )
        if variant == 0:
            from hc_data_platform.annotation.delivery_queries import PostgresAnnotationDeliveries
            from hc_data_platform.lerobot_imports import configuration
            from hc_data_platform.lerobot_imports.processing import get_progress
            from hc_data_platform.workflow.annotation_retry import stored_request
            from hc_data_platform.workflow.temporal_workflows import RetryEpisodeAnnotationWorkflow

            monkeypatch.setenv("HC_POSTGRES_DSN", settings.postgres_dsn)
            monkeypatch.setattr(
                "hc_data_platform.lerobot_imports.processing.connections", lambda: connect
            )
            before_annotation = runtime.lerobot_uploads.delivery.enrich(
                scope, get_progress(*scope, raw.raw_source_id)
            )
            assert before_annotation.readable_episodes == 1
            assert before_annotation.annotation_counts == {"PENDING_CONFIGURATION": 1}

            deliveries = PostgresAnnotationDeliveries(connect)
            delivery_args = dict(
                scope=scope, dataset_id=dataset, episode_ids=(data.identity.rollout_id,)
            )
            assert deliveries.for_episodes(**delivery_args) == ()

            monkeypatch.setattr(configuration, "connections", lambda: connect)
            monkeypatch.setattr(configuration, "get_annotation_service", lambda: runtime.annotation)
            label_actor = replace(
                actor, capabilities=actor.capabilities | {"data_schema.read", "data_schema.publish"}
            )
            assert not configuration.processing_configuration(project, region, dataset).configured
            configured = configuration.configure_labels(
                label_actor, org, project, region, dataset, ["synthetic action"]
            )
            assert (
                configured.configured and configured.schema_snapshot_id == data.schema_snapshot_id
            )
            with connect() as db:
                original_workflow = db.execute(
                    """SELECT workflow_id FROM workflow.jobs WHERE organization_id=%s
                    AND project_id=%s AND resource_id=%s AND status='SUCCEEDED'
                    AND result->>'annotation_status'='PENDING_CONFIGURATION'""",
                    (org, project, data.identity.rollout_id),
                ).fetchone()[0]
                retry = stored_request(db, scope, original_workflow)
                versions_before = db.execute(
                    "SELECT count(*) FROM episode_store.versions WHERE organization_id=%s", (org,)
                ).fetchone()[0]
            async with Worker(
                temporal, task_queue=queue, workflows=workflows, activities=activities
            ):
                retried = await temporal.execute_workflow(
                    RetryEpisodeAnnotationWorkflow.run,
                    retry,
                    id="annotation-only-" + suffix,
                    task_queue=queue,
                    result_type=JobRecord,
                )
            assert retried.result["annotation_status"] == "CREATED"
            after_annotation = runtime.lerobot_uploads.delivery.enrich(
                scope, get_progress(*scope, raw.raw_source_id)
            )
            assert after_annotation.readable_episodes == 1
            assert after_annotation.annotation_counts == {"CREATED": 1}
            delivered = deliveries.for_episodes(**delivery_args)
            assert len(delivered) == 1 and delivered[0].status == "DRAFT"
            assert delivered[0].data_ref == data and delivered[0].lance_version is None
            assert deliveries.for_episodes(**{**delivery_args, "dataset_id": "other"}) == ()
            with pytest.raises(ValueError, match="scope differs"):
                deliveries.for_episodes(**{**delivery_args, "scope": (org, "other", region)})
            with connect() as db:
                assert (
                    db.execute(
                        "SELECT count(*) FROM episode_store.versions WHERE organization_id=%s",
                        (org,),
                    ).fetchone()[0]
                    == versions_before
                )
                assert (
                    db.execute(
                        "SELECT count(*) FROM annotation.annotation_tasks WHERE organization_id=%s",
                        (org,),
                    ).fetchone()[0]
                    == 1
                )
                assert (
                    db.execute(
                        "SELECT result->>'annotation_status' FROM workflow.jobs "
                        "WHERE workflow_id=%s",
                        (original_workflow,),
                    ).fetchone()[0]
                    == "PENDING_CONFIGURATION"
                )
    finally:
        reset_request_context(token)
