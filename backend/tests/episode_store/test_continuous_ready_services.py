"""Real package/QC/task/preview readiness fence; source ledger is synthetic.

This exercises DB and service publication contracts, not capture/upload/slicing.
No outbox is inserted, so the live worker cannot turn fixture setup into ingestion.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from pydantic import ValidationError

from hc_data_platform.alignment.models import AlignmentInputV1
from hc_data_platform.annotation import AnnotationService
from hc_data_platform.annotation.automation import (
    AutomaticAnnotationRequest,
    AutomaticAnnotationTaskService,
    PostgresAutomaticAnnotationRepository,
)
from hc_data_platform.annotation.postgres import PostgresAnnotationRepository
from hc_data_platform.continuous_recordings.asset_models import (
    EpisodeProcessing,
    EpisodeProcessingStatus,
)
from hc_data_platform.continuous_recordings.models import RecordingScope
from hc_data_platform.continuous_recordings.processing import ContinuousEpisodeProcessingService
from hc_data_platform.continuous_recordings.repository import PostgresContinuousRecordingRepository
from hc_data_platform.dataset_registry.ingest_projection import PostgresDatasetIngestProjector
from hc_data_platform.episode_store.catalog_models import EpisodeDerivedReady
from hc_data_platform.episode_store.service import EpisodePackageService
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot
from hc_data_platform.workflow.models import (
    ContinuousEpisodeProjectionSourceV1,
    ContinuousEpisodeStateActivityInput,
)
from tests.episode_store.test_annotation_bindings_services import publish_schema
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_writer import build_rgbd_package, identity


def seed_ledger(repository, source):
    ident = source.identity
    recording, episode = ident.rollout_id.split(":")
    upload, task = uuid4(), "task-" + uuid4().hex
    now = datetime.now(timezone.utc)
    processing = EpisodeProcessing(
        scope=RecordingScope(
            organization_id=ident.organization_id,
            project_id=ident.project_id,
            region_code=ident.region_code,
        ),
        recording_id=recording,
        episode_id=episode,
        finalized_revision=1,
        start_offset_ns=0,
        end_offset_ns=133_333_333,
        workflow_id="continuous-ready-service/" + uuid4().hex,
        event_id=uuid4(),
        status=EpisodeProcessingStatus.ALIGNING,
        qc_report_id="test-qc-pass-" + uuid4().hex,
        alignment_attempt_id="test-alignment",
    )
    with repository.transaction() as cursor:
        cursor.execute(
            "INSERT INTO registry.organization_projects VALUES (%s,%s,'Synthetic READY test')",
            (ident.organization_id, ident.project_id),
        )
        cursor.execute(
            """INSERT INTO collection_tasks.collection_tasks
            (organization_id,project_id,collection_task_id,dataset_id,task_code,name,task_type,
             scenario,status,create_fingerprint,upload_region_code)
            VALUES (%s,%s,%s,%s,lpad(nextval('collection_tasks.task_code_sequence')::text,8,'0'),
                    'Synthetic READY','DATA_CAPTURE','software','ACTIVE',%s,%s)""",
            (
                ident.organization_id,
                ident.project_id,
                task,
                ident.dataset_id,
                "2" * 64,
                ident.region_code,
            ),
        )
        cursor.execute(
            """INSERT INTO ingest.recording_uploads
            (organization_id,project_id,region_code,upload_id,recording_id,manifest_sha256,status,
             upload_document,created_by,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,'COMMITTED','{"synthetic_ledger_only":true}',
                    'test',%s,%s)""",
            (*ident.scope, upload, recording, "3" * 64, now, now),
        )
        cursor.execute(
            """INSERT INTO ingest.continuous_recordings
            (organization_id,project_id,region_code,recording_id,recording_upload_id,rollout_id,
             data_package_id,collection_task_id,collection_job_id,robot_id,device_id,
             capture_started_at,capture_ended_at,duration_ns,source_sha256,manifest_fingerprint,
             status,current_revision,finalized_revision,recording_document,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,'test-parent',%s,'test-job','test-robot','test-device',
                    %s,%s,133333333,%s,%s,'SLICED',1,1,'{"synthetic_ledger_only":true}',%s,%s)""",
            (
                *ident.scope,
                recording,
                upload,
                recording,
                task,
                now,
                now + timedelta(seconds=1),
                "3" * 64,
                "4" * 64,
                now,
                now,
            ),
        )
        cursor.execute(
            """INSERT INTO ingest.recording_slice_revisions
            (organization_id,project_id,region_code,recording_id,revision,status,
             revision_document,created_by,created_at)
            VALUES (%s,%s,%s,%s,1,'FINALIZED','{"synthetic_ledger_only":true}','test',%s)""",
            (*ident.scope, recording, now),
        )
        cursor.execute(
            """INSERT INTO ingest.recording_episode_slices
            (organization_id,project_id,region_code,recording_id,revision,episode_id,ordinal,
             start_offset_ns,end_offset_ns,started_at,ended_at,episode_document)
            VALUES (%s,%s,%s,%s,1,%s,0,0,133333333,%s,%s,'{"synthetic_ledger_only":true}')""",
            (*ident.scope, recording, episode, now, now + timedelta(seconds=1)),
        )
        cursor.execute(
            """INSERT INTO ingest.recording_episode_processing
            (organization_id,project_id,region_code,recording_id,episode_id,finalized_revision,
             start_offset_ns,end_offset_ns,status,qc_report_id,alignment_attempt_id,
             episode_document,created_at,updated_at,workflow_id,event_id)
            VALUES (%s,%s,%s,%s,%s,1,0,133333333,'ALIGNING',%s,'test-alignment',
                    %s::jsonb,%s,%s,%s,%s)""",
            (
                *ident.scope,
                recording,
                episode,
                processing.qc_report_id,
                processing.model_dump_json(),
                now,
                now,
                processing.workflow_id,
                processing.event_id,
            ),
        )
    projection = ContinuousEpisodeProjectionSourceV1(
        organization_id=ident.organization_id,
        project_id=ident.project_id,
        region_code=ident.region_code,
        recording_id=recording,
        recording_upload_id=str(upload),
        episode_id=episode,
        rollout_id=ident.rollout_id,
        data_package_id="test-parent",
        collection_task_id=task,
        robot_id="test-robot",
        source_sha256=source.source_sha256,
        manifest_fingerprint="4" * 64,
        source_size_bytes=1,
        started_at=now,
        ended_at=now + timedelta(seconds=1),
        camera_modality_keys=tuple(f for f in source.required_features if ".images." in f),
    )
    return processing, projection


@pytest.mark.integration
def test_ready_requires_package_collection_qc_task_viewer_and_all_previews(
    services, tmp_path, monkeypatch
):
    repository, storage = services
    ident = identity().model_copy(
        update={
            "organization_id": "continuous-ready-" + uuid4().hex,
            "dataset_id": "dataset_ready",
            "episode_id": "episode_package",
            "rollout_id": "recording_parent:episode_cut",
        }
    )
    root, source, _, assets = build_rgbd_package(tmp_path, ident)
    schema = DatasetSchemaSnapshot.create(
        project_id=ident.project_id,
        dataset_id=ident.dataset_id,
        schema_snapshot_id=source.schema_snapshot_id,
        frequency_hz=30,
        fields={
            "/observation/state": "json",
            "action": "json",
            **{key: "json" for key in assets.videos},
        },
    )
    packages = EpisodePackageService(repository.connections, storage, tmp_path / "service")
    records = PostgresContinuousRecordingRepository(repository.connections)
    processor = ContinuousEpisodeProcessingService(
        connection_factory=repository.connections,
        repository=records,
        storage=storage,
        decoder=None,
        quality=None,
        alignment=None,
        fragment_writers=None,
        staging=None,
        staging_ttl=timedelta(hours=1),
        catalog_fragments=None,
        catalog=None,
        media_repository=None,
        dataset_projection=None,
    )
    try:
        with scoped(source):
            current, projection = seed_ledger(repository, source)
            packages.schemas.register_schema(schema)
            receipt = packages.committer.commit(root, source)
            collection = packages.collections.publish(receipt.data, schema)
            ready = EpisodeDerivedReady(
                data=receipt.data,
                collection_version_id=collection.collection_version_id,
                project_id=ident.project_id,
                dataset_id=ident.dataset_id,
                rollout_id=ident.rollout_id,
                source_sha256=source.source_sha256,
                converter_version=source.processor_version,
                dataset_version=collection.version,
                step_count=4,
                content_hash=collection.content_hash,
            )
            state = ContinuousEpisodeStateActivityInput(
                **current.scope.model_dump(),
                recording_id=current.recording_id,
                episode_id=current.episode_id,
                expected_status=EpisodeProcessingStatus.ALIGNING,
                new_status=EpisodeProcessingStatus.READY,
                dataset_id=ident.dataset_id,
                dataset_episode_id=ident.episode_id,
                dataset_version=collection.version,
                data_ref=ready.data,
                collection_version_id=collection.collection_version_id,
                annotation_task_id="not-created",
                aligned_media_camera_count=2,
            )
            with pytest.raises(psycopg.errors.CheckViolation, match="exact package"):
                processor.update_state(state)
            annotation = AnnotationService(PostgresAnnotationRepository(repository.connections))
            publish_schema(source, annotation)
            task = AutomaticAnnotationTaskService(
                PostgresAutomaticAnnotationRepository(repository.connections)
            ).ensure_task(
                AutomaticAnnotationRequest(
                    project_id=ident.project_id,
                    region_code=ident.region_code,
                    dataset_id=ident.dataset_id,
                    rollout_id=ident.rollout_id,
                    dataset_version=collection.version,
                    lance_version=None,
                    data_ref=ready.data,
                    dataset_schema_snapshot_id=source.schema_snapshot_id,
                    base_step_count=4,
                    source_workflow_id=current.workflow_id,
                )
            )
            state = state.model_copy(update={"annotation_task_id": task.task_id})
            alignment = AlignmentInputV1(
                rollout_id=ident.rollout_id,
                source_sha256=source.source_sha256,
                attempt_id=current.alignment_attempt_id,
                start_ns=0,
                end_ns=133_333_333,
                streams={
                    key: {"kind": "image" if key in assets.videos else "continuous", "samples": []}
                    for key in schema.fields
                },
            )
            PostgresDatasetIngestProjector(
                repository.connections, episode_packages=packages
            ).project_continuous_episode(
                source=projection,
                alignment=alignment,
                schema_snapshot_id=source.schema_snapshot_id,
                frequency_hz=30,
                version=collection,
                ready=ready,
                media_artifacts=(),
            )
            with pytest.raises(psycopg.errors.CheckViolation, match="exact package"):
                processor.update_state(state)  # Missing PASS QC even with task + viewer.
            with repository.transaction() as cursor:
                cursor.execute(
                    """INSERT INTO ingest.recording_episode_qc_reports
                    (organization_id,project_id,region_code,recording_id,episode_id,report_id,
                     report_sha256,status,report_document,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'PASS',%s::jsonb,%s)""",
                    (
                        *ident.scope,
                        current.recording_id,
                        current.episode_id,
                        current.qc_report_id,
                        "5" * 64,
                        json.dumps({"synthetic": True, "source_sha256": source.source_sha256}),
                        current.created_at,
                    ),
                )
            with pytest.raises(psycopg.errors.CheckViolation, match="camera previews"):
                processor.update_state(state)
            packages.previews.prepare_all(ready.data)
            # Model copies can bypass validation: the state service must revalidate.
            with pytest.raises(ValidationError, match="exactly one"):
                processor.update_state(state.model_copy(update={"lance_version": 1}))
            for change in (
                {"collection_version_id": "unknown-collection"},
                {"dataset_version": collection.version + 1},
                {"annotation_task_id": "another-task"},
                {"qc_report_id": "another-qc"},
                {"aligned_media_camera_count": 1},
                {"data_ref": ready.data.model_copy(update={"content_hash": "f" * 64})},
            ):
                with pytest.raises(psycopg.errors.CheckViolation):
                    processor.update_state(state.model_copy(update=change))
            barrier = Barrier(3)
            get = records.get_episode_processing

            def gated_get(*args):
                result = get(*args)
                if result.status is EpisodeProcessingStatus.ALIGNING:
                    barrier.wait(timeout=10)
                return result

            def retry(_):
                with scoped(source):
                    return processor.update_state(state).episode

            with monkeypatch.context() as patch:
                patch.setattr(records, "get_episode_processing", gated_get)
                with ThreadPoolExecutor(max_workers=3) as pool:
                    saved, *retries = pool.map(retry, range(3))
            assert all(value == saved for value in retries)
            assert saved.status is EpisodeProcessingStatus.READY and saved.lance_version is None
            assert saved.data_ref == ready.data
            assert processor.update_state(state).episode == saved  # Lost-response recovery.
            with repository.transaction() as cursor:
                cursor.execute(
                    """SELECT count(*) FROM ingest.recording_episode_dataset_version_reservations
                    WHERE organization_id=%s""",
                    (ident.organization_id,),
                )
                assert cursor.fetchone()[0] == 0
            # Direct SQL cannot remove the package fence or alter its source window.
            for mutation in (
                "data_ref=NULL,lance_version=1,collection_version_id=NULL",
                "start_offset_ns=1",
            ):
                with (
                    pytest.raises(psycopg.errors.CheckViolation, match="immutable"),
                    repository.transaction() as cursor,
                ):
                    cursor.execute(
                        "UPDATE ingest.recording_episode_processing SET "
                        + mutation
                        + " WHERE organization_id=%s",
                        (ident.organization_id,),
                    )
            with (
                pytest.raises(psycopg.errors.CheckViolation, match="document/window"),
                repository.transaction() as cursor,
            ):
                cursor.execute(
                    """UPDATE ingest.recording_episode_processing SET episode_document=
                    jsonb_set(episode_document,'{dataset_version}','999')
                    WHERE organization_id=%s""",
                    (ident.organization_id,),
                )
            foreign = source.model_copy(
                update={"identity": ident.model_copy(update={"organization_id": "another-org"})}
            )
            with (
                scoped(foreign),
                pytest.raises(psycopg.errors.InsufficientPrivilege, match="scope mismatch"),
                repository.transaction() as cursor,
            ):
                cursor.execute(
                    """UPDATE ingest.recording_episode_processing
                    SET updated_at=clock_timestamp() WHERE organization_id=%s""",
                    (ident.organization_id,),
                )
            if evidence_root := os.environ.get("HC_G1_WRITER_EVIDENCE_ROOT"):
                (Path(evidence_root) / "continuous-ready.json").write_text(
                    json.dumps(
                        {
                            "scope": (
                                "synthetic source ledger; real PG, MinIO, package, "
                                "annotation and SDK previews"
                            ),
                            "episode": saved.model_dump(mode="json"),
                            "source_sha256": source.source_sha256,
                            "simultaneous_ready_calls": 3,
                            "lance_reservations": 0,
                            "missing_qc_and_preview_refused": True,
                            "forged_references_and_cross_scope_refused": True,
                            "document_and_window_mutations_refused": True,
                            "full_recording_ingress": "NOT_YET_WIRED",
                        },
                        indent=2,
                    )
                    + "\n"
                )
    finally:
        packages.close()
