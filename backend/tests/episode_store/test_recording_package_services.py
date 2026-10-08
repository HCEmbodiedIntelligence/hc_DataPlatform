"""Real upload/adoption/slicing/QC/package services; explicitly synthetic input.

Run while the isolated worker is stopped so direct state transitions own the
new fixture. This does not constitute hardware or human acceptance.
"""

import asyncio
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer, Worker

from hc_data_platform.alignment.engine import AlignmentEngine
from hc_data_platform.annotation import AnnotationService
from hc_data_platform.annotation.automation import (
    AutomaticAnnotationRequest,
    AutomaticAnnotationTaskService,
    PostgresAutomaticAnnotationRepository,
)
from hc_data_platform.annotation.postgres import PostgresAnnotationRepository
from hc_data_platform.continuous_recordings.asset_models import EpisodeProcessingStatus as Status
from hc_data_platform.continuous_recordings.asset_repository import PostgresRecordingAssetRepository
from hc_data_platform.continuous_recordings.models import RecordingScope, SaveSliceDraftCommand
from hc_data_platform.continuous_recordings.openarm_grid import frame_time
from hc_data_platform.continuous_recordings.package import recording_files
from hc_data_platform.continuous_recordings.processing import (
    ContinuousEpisodeProcessingService,
    PostgresContinuousEpisodeWorkflowInputResolver,
)
from hc_data_platform.continuous_recordings.registration import PostgresRecordingRegistration
from hc_data_platform.continuous_recordings.repository import PostgresContinuousRecordingRepository
from hc_data_platform.continuous_recordings.service import ContinuousRecordingService
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.dataset_registry.ingest_projection import PostgresDatasetIngestProjector
from hc_data_platform.episode_store.models import EpisodeIdentity
from hc_data_platform.episode_store.service import EpisodePackageService
from hc_data_platform.episode_store.validation import parquet_rows
from hc_data_platform.quality.engine import QualityEngine
from hc_data_platform.quality.models import QualityProfileV1
from hc_data_platform.robot_ingest.recording_bridge import RecordingOutboxHandler
from hc_data_platform.runtime import ArrowCatalogFragmentAdapter, ArrowFragmentWriterFactory
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.verification.ports import RegisteredDecoderProbe
from hc_data_platform.workflow import activities
from hc_data_platform.workflow.continuous_episode_dispatch import ContinuousEpisodeOutboxHandler
from hc_data_platform.workflow.ingest_dispatch import IngestWorkflowPlanBlocked
from hc_data_platform.workflow.models import (
    ContinuousEpisodeStateActivityInput,
    JobRecord,
    JobStatus,
)
from hc_data_platform.workflow.postgres import PostgresWorkflowJobRepository
from hc_data_platform.workflow.projection_store import S3ProjectionArtifactStore
from hc_data_platform.workflow.service import TemporalWorkflowLauncher
from hc_data_platform.workflow.temporal_workflows import ContinuousRecordingEpisodeWorkflow
from tests.episode_store.test_annotation_bindings_services import publish_schema
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_depth_window import forbid_quantization
from tests.robot_ingest.test_raw_derivation_services import uploaded_raw  # noqa: F401


def forbidden(*args, **kwargs):
    raise AssertionError("continuous package path must not access Lance or lost temporary input")


class ForbiddenLegacy:
    def __getattr__(self, name):
        return forbidden


async def run_temporal(processor, staging, repository, packages, ledger, monkeypatch):
    assert os.environ["HC_TEMPORAL_NAMESPACE"] == "chayi-g1"
    client = await Client.connect(
        os.environ["HC_TEMPORAL_TARGET"],
        namespace="chayi-g1",
        data_converter=pydantic_data_converter,
    )
    captured, attempts = {}, []
    real_align, real_commit = processor.align, processor.commit_bundle

    def align(value):
        captured["aligned"] = real_align(value)
        return captured["aligned"]

    def commit(**kwargs):
        captured["committed"] = real_commit(**kwargs)
        attempts.append(activity.info().attempt)
        if len(attempts) == 1:
            raise InterruptedError("lost committed recording package response")
        return captured["committed"]

    queue = "chayi-g1-recording-package-" + uuid4().hex
    with repository.transaction() as cursor:
        cursor.execute(
            "SELECT envelope FROM core.outbox_events WHERE event_id=%s", (ledger.event_id,)
        )
        event = DomainEventEnvelope.model_validate(cursor.fetchone()[0])
    handler = ContinuousEpisodeOutboxHandler(
        TemporalWorkflowLauncher("unused", task_queue=queue, client=client),
        PostgresContinuousEpisodeWorkflowInputResolver(
            repository.connections, ForbiddenLegacy(), package_schemas=packages.schemas
        ),
    )
    with monkeypatch.context() as patch:
        patch.setattr(processor, "align", align)
        patch.setattr(processor, "commit_bundle", commit)
        patch.setattr(
            activities,
            "_dependencies",
            activities.ActivityDependencies(
                continuous_episode_processing=processor,
                alignment_staging=staging,
                workflow_jobs=PostgresWorkflowJobRepository(repository.connections),
                annotation_tasks=AutomaticAnnotationTaskService(
                    PostgresAutomaticAnnotationRepository(repository.connections)
                ),
            ),
        )
        async with Worker(
            client,
            task_queue=queue,
            workflows=[ContinuousRecordingEpisodeWorkflow],
            activities=activities.ALL_ACTIVITIES,
        ):
            started = await handler(event)
            assert started.workflow_id == ledger.workflow_id
            handle = client.get_workflow_handle(ledger.workflow_id)
            try:
                result = JobRecord.model_validate(
                    await asyncio.wait_for(handle.result(), timeout=85)
                )
            except BaseException:
                await handle.cancel()
                raise
            assert result.status is JobStatus.SUCCEEDED, result
            assert result.result["training_eligible"] is None
            assert result.result["model_depth_training"] == "NOT_VALIDATED"
            assert attempts == [1, 2]
            history = await handle.fetch_history()
            # A delivered launch response can be lost after the whole workflow
            # finishes. Terminal redelivery must settle from exact scoped evidence.
            terminal = await handler(event)
            assert terminal["status"] == "READY"
            assert terminal["data_ref"] == result.result["derived_ready"]["data"]
            wrong_revision = event.model_copy(
                update={
                    "payload": {
                        **event.payload,
                        "finalized_revision": ledger.finalized_revision + 1,
                    }
                }
            )
            with pytest.raises(IngestWorkflowPlanBlocked, match="scoped ledger"):
                await handler(wrong_revision)
            with pytest.raises(ValueError, match="event lineage"):
                await handler(event.model_copy(update={"organization_id": "another-org"}))
    await Replayer(
        workflows=[ContinuousRecordingEpisodeWorkflow], data_converter=pydantic_data_converter
    ).replay_workflow(history)
    captured["history"] = history.to_json()
    return captured


@pytest.mark.integration
@pytest.mark.parametrize("execution", ["direct", "temporal"])
def test_uploaded_recording_slice_qc_package_ready_recovery(
    uploaded_raw,  # noqa: F811
    tmp_path,
    monkeypatch,
    execution,
):
    repository, storage, upload, marker, original_command, event = uploaded_raw
    identity = EpisodeIdentity(
        organization_id=event.organization_id,
        project_id=event.project_id,
        region_code=event.region_code,
        dataset_id="dataset_raw_pinning",
        episode_id="episode_cut",
        rollout_id=original_command.recording_id + ":episode_cut",
    )
    source = SimpleNamespace(identity=identity, schema_snapshot_id="recording-rgbd-schema")
    scope = RecordingScope(
        organization_id=identity.organization_id,
        project_id=identity.project_id,
        region_code=identity.region_code,
    )
    auth = AuthContext(
        subject_id="synthetic-slicer",
        organization_ids=frozenset({identity.organization_id}),
        project_ids=frozenset({identity.project_id}),
        region_codes=frozenset({identity.region_code}),
        organization_scope_triples=frozenset({identity.scope}),
        scope_pairs=frozenset({(identity.project_id, identity.region_code)}),
        organization_scoped_capabilities=frozenset(
            (identity.organization_id, identity.project_id, cap)
            for cap in ("upload.read", "upload.manage")
        ),
    )
    records = PostgresContinuousRecordingRepository(repository.connections)
    packages = EpisodePackageService(repository.connections, storage, tmp_path / "packages")
    staging = S3ProjectionArtifactStore(storage._client, storage._bucket, tmp_path / "staging")
    from hc_data_platform.registry.snapshot_queries import published_model_inputs
    from hc_data_platform.robot_assets.binding_queries import locked_model_binding
    from hc_data_platform.robotics.scene_snapshots import RobotSceneSnapshots

    processor = ContinuousEpisodeProcessingService(
        connection_factory=repository.connections,
        repository=records,
        storage=storage,
        decoder=RegisteredDecoderProbe(
            {("json", "jsonschema"): lambda _schema, message: json.loads(message)}
        ),
        quality=QualityEngine(),
        alignment=AlignmentEngine(),
        fragment_writers=ArrowFragmentWriterFactory(tmp_path / "arrow"),
        staging=staging,
        staging_ttl=timedelta(hours=1),
        catalog_fragments=ArrowCatalogFragmentAdapter(ForbiddenLegacy()),
        catalog=ForbiddenLegacy(),
        media_repository=ForbiddenLegacy(),
        dataset_projection=PostgresDatasetIngestProjector(
            repository.connections, episode_packages=packages
        ),
        episode_packages=packages,
        robot_scenes=RobotSceneSnapshots(
            repository.connections,
            binding_query=locked_model_binding,
            model_query=published_model_inputs,
        ),
    )
    try:
        with scoped(source):
            RecordingOutboxHandler(
                repository.connections, storage, registrations=PostgresRecordingRegistration()
            ).adopt(event)
            forbid_quantization(monkeypatch)
            record = records.get(scope, original_command.recording_id)
            assert record is not None
            service = ContinuousRecordingService(
                records, None, PostgresRecordingAssetRepository(repository.connections), storage
            )
            args = dict(
                auth=auth,
                **scope.model_dump(),
                recording_id=record.recording_id,
                actor_id=auth.subject_id,
            )
            draft = service.save_draft(
                **args,
                command=SaveSliceDraftCommand.model_validate(
                    {
                        "slices": [
                            {
                                "episode_id": "episode_cut",
                                "start_offset_ns": str(frame_time(1)),
                                "end_offset_ns": str(frame_time(7)),
                                "title": "Synthetic RGB-D cut",
                            }
                        ]
                    }
                ),
                if_match=record.etag,
                request_id="draft-recording-package",
            )
            finalized = service.finalize(
                **args,
                expected_draft_revision=draft.revision.revision,
                if_match=draft.recording.etag,
                request_id="finalize-recording-package",
            )
            annotation = AnnotationService(PostgresAnnotationRepository(repository.connections))
            publish_schema(source, annotation)
            assets = PostgresRecordingAssetRepository(repository.connections)
            persisted_upload = assets.get_upload(scope, record.recording_upload_id)
            config = persisted_upload.command.recording_config
            topics = frozenset(
                [c.modality_key for c in config.cameras] + [s.topic for s in config.sensors]
            )
            profile = QualityProfileV1(
                profile_id="recording-package-software", required_topics=topics
            )
            with repository.transaction() as cursor:
                cursor.execute(
                    """INSERT INTO quality_profiles
                    (project_id,profile_id,profile_version,schema_version,profile_sha256,profile_json)
                    VALUES (%s,%s,1,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                    (
                        identity.project_id,
                        profile.profile_id,
                        profile.schema_version,
                        profile.content_sha256(),
                        profile.content_bytes().decode(),
                    ),
                )
            request = PostgresContinuousEpisodeWorkflowInputResolver(
                repository.connections, ForbiddenLegacy(), package_schemas=packages.schemas
            ).resolve(
                **scope.model_dump(),
                recording_id=record.recording_id,
                episode_id="episode_cut",
                finalized_revision=finalized.revision.revision,
            )
            assert (
                request.storage_format == "lerobot_v3" and request.expected_dataset_version is None
            )

            def state(expected, new, **fields):
                return processor.update_state(
                    ContinuousEpisodeStateActivityInput(
                        **scope.model_dump(),
                        recording_id=record.recording_id,
                        episode_id="episode_cut",
                        expected_status=expected,
                        new_status=new,
                        **fields,
                    )
                ).episode

            captured = {}
            if execution == "temporal":
                ledger = records.get_episode_processing(scope, record.recording_id, "episode_cut")
                captured = asyncio.run(
                    run_temporal(processor, staging, repository, packages, ledger, monkeypatch)
                )
                aligned = captured["aligned"]
            else:
                state(Status.PENDING_QC, Status.QC_RUNNING)
                qc = processor.qc(request)
                assert qc.status == "PASS", qc
                state(Status.QC_RUNNING, Status.PENDING_ALIGNMENT, qc_report_id=qc.report_id)
                state(
                    Status.PENDING_ALIGNMENT,
                    Status.ALIGNING,
                    alignment_attempt_id="continuous-" + request.projection.source_sha256[:24],
                )
                aligned = processor.align(request)
            plan = aligned.package_plan
            assert plan is not None and plan.frame_count == 6
            assert len(plan.videos) == 6 and len(plan.input.required_depth_features) == 3
            clock = plan.metadata["source"]["clock"]
            assert clock["capture_to_source_grid_offset_ns"] == 26_032_068
            assert aligned.alignment.data.start_ns == clock["source_grid_origin_ns"] + frame_time(1)
            # Worker-local authorization remains enforced before any input read.
            forged = request.model_copy(update={"end_offset_ns": frame_time(8)})
            with (
                pytest.raises(PermissionError, match="window differs"),
                recording_files(forged, repository.connections, storage),
            ):
                pytest.fail("forged window accepted")
            forged = request.model_copy(update={"assets": request.assets[:-1]})
            with (
                pytest.raises(PermissionError, match="assets differ"),
                recording_files(forged, repository.connections, storage),
            ):
                pytest.fail("incomplete inventory accepted")

            commit_args = dict(
                workflow_input=request,
                alignment=aligned.alignment,
                staged_manifest=aligned.staged_manifest,
                alignment_staging=aligned.alignment_staging,
                expected_dataset_version=None,
                expected_camera_ids=tuple(plan.feature_mapping),
                media_artifacts=(),
                package_plan=plan,
            )
            committed = processor.commit_bundle(**commit_args)
            ready, collection = committed.derived_ready, committed.version
            # Lost response + vanished temporary Arrow must recover committed evidence.
            with monkeypatch.context() as patch:
                patch.setattr(staging, "local_file", forbidden)

                def retry(_):
                    with scoped(source):
                        return processor.commit_bundle(**commit_args)

                with ThreadPoolExecutor(max_workers=3) as pool:
                    retries = list(pool.map(retry, range(3)))
            assert all(item == committed for item in retries)
            ledger = records.get_episode_processing(scope, record.recording_id, "episode_cut")
            task = AutomaticAnnotationTaskService(
                PostgresAutomaticAnnotationRepository(repository.connections)
            ).ensure_task(
                AutomaticAnnotationRequest(
                    project_id=identity.project_id,
                    region_code=identity.region_code,
                    dataset_id=request.dataset_id,
                    rollout_id=request.projection.rollout_id,
                    dataset_version=collection.version,
                    lance_version=None,
                    data_ref=ready.data,
                    dataset_schema_snapshot_id=request.schema_snapshot_id,
                    base_step_count=6,
                    source_workflow_id=ledger.workflow_id,
                )
            )
            saved = state(
                Status.ALIGNING,
                Status.READY,
                alignment_attempt_id=aligned.staged_manifest.attempt_id,
                dataset_id=request.dataset_id,
                dataset_episode_id=plan.input.identity.episode_id,
                dataset_version=collection.version,
                data_ref=ready.data,
                collection_version_id=collection.collection_version_id,
                annotation_task_id=task.task_id,
                aligned_media_camera_count=6,
            )
            assert saved.lance_version is None and saved.data_ref == ready.data
            package = packages.files.open(plan.input.identity, plan.input.data_version_id)
            with package.file("hc/recording/source_frames.jsonl") as file:
                rows = [json.loads(line) for line in file.read_text().splitlines()]
                assert [row["source_frame"] for row in rows] == list(range(1, 7))
            with package.file("hc/modalities.json") as file:
                modalities = json.loads(file.read_bytes())
            assert len(modalities["depth"]) == 3
            for feature, depth in modalities["depth"].items():
                with package.file(depth["evidence_path"] + "/frames.parquet") as file:
                    rows = list(parquet_rows(file))
                assert [row["parent_frame_index"] for row in rows] == list(range(1, 7))
                assert [row["target_timestamp_ns"] for row in rows] == [
                    plan.windows[feature].target_time(i) for i in range(6)
                ]
            with repository.transaction() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM ingest.recording_episode_dataset_version_reservations "
                    "WHERE organization_id=%s",
                    (identity.organization_id,),
                )
                assert cursor.fetchone()[0] == 0
            evidence = {
                "synthetic_input": True,
                "input": request.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
                "ready": saved.model_dump(mode="json"),
            }
            evidence_dir = Path(os.environ.get("HC_G1_WRITER_EVIDENCE_ROOT", str(tmp_path)))
            destination = evidence_dir / "continuous-package" / identity.organization_id
            destination.mkdir(parents=True)
            (destination / "services.json").write_text(json.dumps(evidence, indent=2))
            if "history" in captured:
                (destination / "temporal-history.json").write_text(captured["history"])
            from hc_data_platform.episode_store.quality import read_episode_quality

            qc = read_episode_quality(package)
            assert qc.evidence_status == "AVAILABLE"
            assert qc.evidence.report_sha256 == qc.report.content_sha256
            for entry in package.manifest.files:
                target = destination / "episode" / entry.path
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.file(entry.path) as file:
                    shutil.copyfile(file, target)
            (destination / "manifest.json").write_text(package.manifest.model_dump_json(indent=2))
    finally:
        packages.close()
