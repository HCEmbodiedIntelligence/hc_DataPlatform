"""Run actual worker services for the disposable browser acceptance fixture.

The browser owns slicing/finalization. This runner never targets a business DB.
"""

import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from hc_data_platform.episode_store.collection_delivery_models import RequestCollectionDelivery
from tests.episode_store import test_recording_package_services as b
from tests.episode_store.test_commit_services import services
from tests.episode_store.test_complete_delivery_services import run_job
from tests.episode_store.test_detail_services import auth_for


def run(action, evidence):
    assert os.environ["HC_G1_TEST_POSTGRES_DSN"].endswith("/hc_config")
    fixture = json.loads((evidence / "browser-fixture.json").read_text())
    repository, storage = services.__wrapped__()
    from tests.robot_processing.browser_fixture import ensure_model

    scope = b.RecordingScope(
        organization_id=fixture["organizationId"],
        project_id=fixture["projectId"],
        region_code=fixture["regionCode"],
    )
    identity = b.EpisodeIdentity(
        **scope.model_dump(),
        dataset_id=fixture["datasetId"],
        episode_id="browser",
        rollout_id="browser",
    )
    source = SimpleNamespace(identity=identity, schema_snapshot_id="browser-joints-schema")
    with b.scoped(source), TemporaryDirectory() as temp:
        ensure_model(
            repository, fixture["organizationId"], fixture["projectId"], fixture["modelVersionId"]
        )
        root = Path(temp)
        with repository.transaction() as cursor:
            cursor.execute(
                """SELECT processing_id,recording_id FROM ingest.mcap_processing_files WHERE
                organization_id=%s AND raw_source_id=%s ORDER BY created_at DESC LIMIT 1""",
                (identity.organization_id, fixture["sources"]["joints"]["raw"]),
            )
            processing_id, recording_id = cursor.fetchone()
            if not recording_id:
                cursor.execute(
                    "SELECT envelope FROM core.outbox_events WHERE "
                    "envelope->'payload'->>'mcap_processing_id'=%s LIMIT 1",
                    (processing_id,),
                )
                event = b.DomainEventEnvelope.model_validate(cursor.fetchone()[0])
        if not recording_id:
            b.RecordingOutboxHandler(
                repository.connections, storage, registrations=b.PostgresRecordingRegistration()
            ).adopt(event)
            with repository.transaction() as cursor:
                cursor.execute(
                    "SELECT recording_id FROM ingest.mcap_processing_files WHERE processing_id=%s",
                    (processing_id,),
                )
                recording_id = cursor.fetchone()[0]
        records = b.PostgresContinuousRecordingRepository(repository.connections)
        record = records.get(scope, recording_id)
        upload = b.PostgresRecordingAssetRepository(repository.connections).get_upload(
            scope, record.recording_upload_id
        )
        assert not upload.command.recording_config.cameras
        assert not any(a.role.value == "RAW_VIDEO" for a in upload.command.assets)
        annotation = b.AnnotationService(b.PostgresAnnotationRepository(repository.connections))
        if action == "prepare":
            b.publish_schema(source, annotation)
            profile = b.QualityProfileV1(
                profile_id="mcap-quality-joints",
                required_topics=frozenset(s.topic for s in upload.command.recording_config.sensors),
            )
            with repository.transaction() as cursor:
                cursor.execute(
                    """INSERT INTO
                    quality_profiles(project_id,profile_id,profile_version,schema_version,profile_sha256,profile_json)
                    VALUES(%s,%s,1,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                    (
                        identity.project_id,
                        profile.profile_id,
                        profile.schema_version,
                        profile.content_sha256(),
                        profile.content_bytes().decode(),
                    ),
                )
            (evidence / "browser-recording.json").write_text(
                json.dumps(dict(recording_id=recording_id, status=record.status.value))
            )
            print(recording_id)
            return
        assert action == "finish" and record.status.value == "SLICED", record.status
        revision = records.get_revision(scope, recording_id, record.current_revision)
        packages = b.EpisodePackageService(repository.connections, storage, root / "packages")
        staging = b.S3ProjectionArtifactStore(storage._client, storage._bucket, root / "staging")
        from hc_data_platform.registry.snapshot_queries import published_model_inputs
        from hc_data_platform.robot_assets.binding_queries import locked_model_binding
        from hc_data_platform.robotics.scene_snapshots import RobotSceneSnapshots

        processor = b.ContinuousEpisodeProcessingService(
            connection_factory=repository.connections,
            repository=records,
            storage=storage,
            decoder=b.RegisteredDecoderProbe({("json", "jsonschema"): lambda _, m: json.loads(m)}),
            quality=b.QualityEngine(),
            alignment=b.AlignmentEngine(),
            fragment_writers=b.ArrowFragmentWriterFactory(root / "arrow"),
            staging=staging,
            staging_ttl=b.timedelta(hours=1),
            catalog_fragments=b.ArrowCatalogFragmentAdapter(b.ForbiddenLegacy()),
            catalog=b.ForbiddenLegacy(),
            media_repository=b.ForbiddenLegacy(),
            dataset_projection=b.PostgresDatasetIngestProjector(
                repository.connections, episode_packages=packages
            ),
            episode_packages=packages,
            robot_scenes=RobotSceneSnapshots(
                repository.connections,
                binding_query=locked_model_binding,
                model_query=published_model_inputs,
            ),
        )
        results = []
        for cut in revision.slices:
            request = b.PostgresContinuousEpisodeWorkflowInputResolver(
                repository.connections, b.ForbiddenLegacy(), package_schemas=packages.schemas
            ).resolve(
                **scope.model_dump(),
                recording_id=recording_id,
                episode_id=cut.episode_id,
                finalized_revision=revision.revision,
            )

            def state(old, new, episode_id=cut.episode_id, **fields):
                ordered = [
                    b.Status.PENDING_QC,
                    b.Status.QC_RUNNING,
                    b.Status.PENDING_ALIGNMENT,
                    b.Status.ALIGNING,
                    b.Status.READY,
                ]
                current = records.get_episode_processing(scope, recording_id, episode_id)
                if current.status in ordered and ordered.index(current.status) >= ordered.index(
                    new
                ):
                    return
                return processor.update_state(
                    b.ContinuousEpisodeStateActivityInput(
                        **scope.model_dump(),
                        recording_id=recording_id,
                        episode_id=episode_id,
                        expected_status=old,
                        new_status=new,
                        **fields,
                    )
                )

            state(b.Status.PENDING_QC, b.Status.QC_RUNNING)
            qc = processor.qc(request)
            assert qc.status == "PASS" and qc.inspected_video_count == 0, qc
            state(b.Status.QC_RUNNING, b.Status.PENDING_ALIGNMENT, qc_report_id=qc.report_id)
            state(
                b.Status.PENDING_ALIGNMENT,
                b.Status.ALIGNING,
                alignment_attempt_id="continuous-" + request.projection.source_sha256[:24],
            )
            aligned = processor.align(request)
            plan = aligned.package_plan
            committed = processor.commit_bundle(
                workflow_input=request,
                alignment=aligned.alignment,
                staged_manifest=aligned.staged_manifest,
                alignment_staging=aligned.alignment_staging,
                expected_dataset_version=None,
                expected_camera_ids=(),
                media_artifacts=(),
                package_plan=plan,
            )
            data = committed.derived_ready.data
            task = b.AutomaticAnnotationTaskService(
                b.PostgresAutomaticAnnotationRepository(repository.connections)
            ).ensure_task(
                b.AutomaticAnnotationRequest(
                    project_id=identity.project_id,
                    region_code=identity.region_code,
                    dataset_id=data.identity.dataset_id,
                    rollout_id=data.identity.rollout_id,
                    dataset_version=committed.version.version,
                    lance_version=None,
                    data_ref=data,
                    dataset_schema_snapshot_id=request.schema_snapshot_id,
                    base_step_count=data.frame_count,
                    source_workflow_id=records.get_episode_processing(
                        scope, recording_id, cut.episode_id
                    ).workflow_id,
                )
            )
            state(
                b.Status.ALIGNING,
                b.Status.READY,
                alignment_attempt_id=aligned.staged_manifest.attempt_id,
                dataset_id=request.dataset_id,
                dataset_episode_id=plan.input.identity.episode_id,
                dataset_version=committed.version.version,
                data_ref=data,
                collection_version_id=committed.version.collection_version_id,
                annotation_task_id=task.task_id,
                aligned_media_camera_count=0,
            )
            from dataclasses import replace

            auth = replace(
                auth_for(data), capabilities=auth_for(data).capabilities | {"export.create"}
            )
            exports = []
            for format in ("lerobot", "mcap", "original"):
                job = packages.deliveries.request_collection(
                    RequestCollectionDelivery(
                        members=({"data": data},), modalities="rgbd", output_format=format
                    ),
                    "browser-joint-" + format,
                    auth,
                )
                run_job(packages.deliveries, job)
                ready = packages.deliveries.status(job.job_id, auth)
                assert ready.status == "READY", ready
                exports.append(dict(format=format, status=ready.status, job_id=job.job_id))
            results.append(
                dict(
                    episode_id=data.identity.episode_id,
                    frames=data.frame_count,
                    qc=qc.model_dump(mode="json"),
                    exports=exports,
                )
            )
        (evidence / "browser-pipeline.json").write_text(json.dumps(results, indent=2))
        print(json.dumps(results))


if __name__ == "__main__":
    run(sys.argv[1], Path(sys.argv[2]))
