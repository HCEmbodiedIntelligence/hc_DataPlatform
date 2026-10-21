"""Frozen MCAP -> recording -> 15 Hz slice -> Temporal -> read/export, without labels."""

import asyncio
import io
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer, Worker

from hc_data_platform.capture_schemes.common import digest
from hc_data_platform.capture_schemes.processing import resolve_raw_snapshot
from hc_data_platform.capture_schemes.transactions import bind_raw_snapshot
from hc_data_platform.continuous_recordings.models import RecordingScope, SaveSliceDraftCommand
from hc_data_platform.continuous_recordings.processing import (
    PostgresContinuousEpisodeWorkflowInputResolver,
)
from hc_data_platform.continuous_recordings.registration import PostgresRecordingRegistration
from hc_data_platform.continuous_recordings.repository import PostgresContinuousRecordingRepository
from hc_data_platform.core.config import Settings
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.episode_store.collection_export import CollectionSource, write_collection
from hc_data_platform.episode_store.reader import ParquetStepReader
from hc_data_platform.ingest.raw_sources import PostgresRawSourceRepository
from hc_data_platform.lerobot_imports import mcap_processing
from hc_data_platform.lerobot_imports.models import (
    CommitLeRobotImportV1,
    CompleteLeRobotAssetV1,
    CreateLeRobotImportV1,
)
from hc_data_platform.lerobot_imports.processing import StartStoredProcessing
from hc_data_platform.lerobot_imports.service import LeRobotWebUploadService
from hc_data_platform.lerobot_imports.source_browser import OriginalSourceBrowser
from hc_data_platform.robot_ingest.recording_bridge import RecordingOutboxHandler
from hc_data_platform.runtime import build_runtime
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.workflow.activities import configure_activity_dependencies
from hc_data_platform.workflow.models import JobRecord
from hc_data_platform.workflow.temporal_workflows import ContinuousRecordingEpisodeWorkflow
from hc_data_platform.workflow.worker import discover_temporal_registrations
from tests.episode_store.test_commit_services import scoped
from tests.robot_ingest.test_raw_derivation_services import uploaded_raw  # noqa: F401

from .processing_fixture import snapshot
from .test_mcap_processing import config, contract, joint_source

pytest_plugins = ["tests.episode_store.test_commit_services"]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("include_camera", [False, True])
async def test_mcap_temporal_without_annotation(
    uploaded_raw,  # noqa: F811
    tmp_path,
    monkeypatch,
    include_camera,
):
    if os.getenv("HC_CAPTURE_PROCESSING_INFRA") != "1":
        pytest.skip("explicit isolated infrastructure required")
    repository, storage, old_upload, _, command, _ = uploaded_raw
    scope = (
        old_upload.target.organization_id,
        old_upload.target.project_id,
        old_upload.target.region_code,
    )
    org, project, region = scope
    dataset, task, robot = (
        old_upload.target.dataset_id,
        command.collection_task_id,
        command.robot_id,
    )
    actor = AuthContext(
        subject_id="capture-mcap-test",
        organization_ids=frozenset({org}),
        project_ids=frozenset({project}),
        region_codes=frozenset({region}),
        organization_scope_triples=frozenset({scope}),
        scope_pairs=frozenset({(project, region)}),
        capabilities=frozenset({"upload.read", "upload.manage"}),
    )
    target = RecordingScope(organization_id=org, project_id=project, region_code=region)
    identity = SimpleNamespace(organization_id=org, project_id=project, region_code=region)
    with scoped(SimpleNamespace(identity=identity)):
        settings = Settings(
            environment="test",
            postgres_dsn=os.environ["HC_TEST_POSTGRES_DSN"],
            auth_abuse_enabled=False,
            object_store_endpoint="http://minio:9000",
            object_store_public_endpoint="http://minio:9000",
            object_store_access_key="minio",
            object_store_secret_key="minio-local-only",
            object_store_bucket="hc-data-local",
            lance_root_uri=str(tmp_path / "lance"),
            alignment_staging_root=str(tmp_path / "align"),
            aligned_media_staging_root=str(tmp_path / "media"),
            robot_model_asset_root=str(tmp_path / "models"),
        )
        runtime = build_runtime(settings, include_media=True)
        output, configuration = contract(), config()
        if include_camera:
            configuration["cameras"][0]["required"] = True
            output.features["observation.images.head"] = output.features[
                "observation.images.head"
            ].model_copy(update={"required": True})
        frozen = snapshot(
            output,
            scope=scope,
            raw={
                "dataset_id": dataset,
                "collection_task_id": task,
                "robot_id": robot,
                "source_format": "MCAP",
            },
        )
        frozen["effective_adapter"] = {
            "source_format": "MCAP",
            "parser_version": "native-mcap-recording/2",
            "configuration": configuration,
        }
        frozen["snapshot_sha256"] = digest(
            {k: v for k, v in frozen.items() if k != "snapshot_sha256"}
        )
        service = LeRobotWebUploadService(
            storage,
            raw_sources=PostgresRawSourceRepository(
                repository.connections, capture_snapshot_writer=bind_raw_snapshot
            ),
            capture_plans=SimpleNamespace(prepare=lambda *args, **kwargs: frozen),
        )
        body = joint_source(tmp_path / "x1.mcap", include_camera=include_camera).read_bytes()
        manifest = CreateLeRobotImportV1(
            dataset_id=dataset,
            collection_task_id=task,
            robot_id=robot,
            source_format="MCAP",
            files=({"path": "x1.mcap", "size": len(body), "part_count": 1},),
        )
        args = dict(auth=actor, **target.model_dump())
        grant = service.begin(**args, manifest=manifest)
        asset = grant.assets[0]
        service.upload_part(
            **args,
            import_id=grant.import_id,
            dataset_id=dataset,
            path=asset.path,
            multipart_upload_id=asset.multipart_upload_id,
            part_number=1,
            body=io.BytesIO(body),
            size=len(body),
        )
        service.complete_asset(
            **args,
            import_id=grant.import_id,
            command=CompleteLeRobotAssetV1(
                dataset_id=dataset,
                path=asset.path,
                multipart_upload_id=asset.multipart_upload_id,
                size=len(body),
                part_count=1,
            ),
        )
        service.commit(
            **args, import_id=grant.import_id, command=CommitLeRobotImportV1(manifest=manifest)
        )
        browser = OriginalSourceBrowser(storage, service.raw_sources)
        raw = browser.source(*scope, grant.import_id)
        with repository.connections() as db:
            assert resolve_raw_snapshot(db, scope, raw.raw_source_id) == frozen
        monkeypatch.setattr(mcap_processing, "connections", lambda: repository.connections)
        monkeypatch.setattr(mcap_processing, "validate_target", lambda *args: None)
        progress = mcap_processing.start(
            scope,
            raw,
            browser.manifest(raw),
            StartStoredProcessing(collection_task_id=task, robot_id=robot),
        )
        processing_id = progress["files"][0]["processing_id"]
        with repository.transaction() as cursor:
            cursor.execute(
                """SELECT envelope FROM core.outbox_events
                WHERE envelope->'payload'->>'mcap_processing_id'=%s""",
                (processing_id,),
            )
            event = DomainEventEnvelope.model_validate(cursor.fetchone()[0])
        RecordingOutboxHandler(
            repository.connections, storage, registrations=PostgresRecordingRegistration()
        ).adopt(event)
        result = mcap_processing.progress(scope, raw.raw_source_id)["files"][0]
        assert result["status"] == "READY", result
        records = PostgresContinuousRecordingRepository(repository.connections)
        recording = records.get(target, result["recording_id"])
        args = dict(
            auth=actor,
            **target.model_dump(),
            recording_id=recording.recording_id,
            actor_id=actor.subject_id,
        )
        draft = runtime.continuous_recordings.save_draft(
            **args,
            command=SaveSliceDraftCommand.model_validate(
                {
                    "slices": [
                        {
                            "episode_id": "episode_capture_all",
                            "start_offset_ns": "0",
                            "end_offset_ns": str(recording.duration_ns),
                            "title": "synthetic 167 frames",
                        }
                    ]
                }
            ),
            if_match=recording.etag,
            request_id="capture-draft",
        )
        finalized = runtime.continuous_recordings.finalize(
            **args,
            expected_draft_revision=draft.revision.revision,
            if_match=draft.recording.etag,
            request_id="capture-finalize",
        )
        request = PostgresContinuousEpisodeWorkflowInputResolver(
            repository.connections,
            runtime.catalog,
            package_schemas=runtime.episode_packages.schemas,
        ).resolve(
            **target.model_dump(),
            recording_id=recording.recording_id,
            episode_id="episode_capture_all",
            finalized_revision=finalized.revision.revision,
        )
        assert request.alignment_profile.frequency_hz == 15
        configure_activity_dependencies(runtime.activities)
        queue = "mcap-capture-" + uuid4().hex
        client = await Client.connect("temporal:7233", data_converter=pydantic_data_converter)
        workflows, activities = discover_temporal_registrations()
        ledger = records.get_episode_processing(
            target, recording.recording_id, "episode_capture_all"
        )
        async with Worker(client, task_queue=queue, workflows=workflows, activities=activities):
            handle = await client.start_workflow(
                ContinuousRecordingEpisodeWorkflow.run,
                request,
                id=ledger.workflow_id,
                task_queue=queue,
                result_type=JobRecord,
            )
            result = await asyncio.wait_for(handle.result(), 120)
        assert result.status.value == "SUCCEEDED", result
        assert result.result["annotation_status"] == "PENDING_CONFIGURATION"
        ready = records.get_episode_processing(
            target, recording.recording_id, "episode_capture_all"
        )
        assert ready.status.value == "READY" and ready.annotation_task_id is None
        data = ready.data_ref
        assert data.frame_count == 167
        window = ParquetStepReader(runtime.episode_packages.files).read_steps(
            data.identity, data.data_version_id, 0, 2
        )
        assert len(window.steps[0].modalities["/observation/state"]["values"]) == 29
        assert len(window.steps[0].modalities["/action"]["values"]) == 19
        package = runtime.episode_packages.files.open(data.identity, data.data_version_id)
        write_collection(tmp_path / "export", [CollectionSource(package)], modalities="rgb")
        assert json.loads((tmp_path / "export/meta/info.json").read_text())["total_frames"] == 167
        assert bool(list((tmp_path / "export").rglob("*.mp4"))) is include_camera
        await Replayer(workflows=workflows, data_converter=pydantic_data_converter).replay_workflow(
            await handle.fetch_history()
        )
