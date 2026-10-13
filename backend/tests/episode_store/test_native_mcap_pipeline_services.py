"""Bare MCAP -> durable recording -> slice -> QC -> LeRobot primary -> exports.

The legacy catalog is a trap: any Lance access fails this test.
"""

import io
import json
from types import SimpleNamespace

import pytest

from hc_data_platform.continuous_recordings.mcap_native import McapProcessingConfig
from hc_data_platform.episode_store.collection_delivery_models import RequestCollectionDelivery
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
from tests.continuous_recordings.test_mcap_native import synthetic_mcap
from tests.episode_store import test_recording_package_services as b
from tests.episode_store.test_complete_delivery_services import run_job
from tests.episode_store.test_detail_services import auth_for
from tests.robot_ingest.test_raw_derivation_services import uploaded_raw  # noqa


@pytest.mark.integration
def test_bare_mcap_to_slice_package_and_all_exports(
    uploaded_raw,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    repository, storage, old_upload, _, old_command, _ = uploaded_raw
    target = old_upload.target
    identity = b.EpisodeIdentity(
        organization_id=target.organization_id,
        project_id=target.project_id,
        region_code=target.region_code,
        dataset_id="dataset_raw_pinning",
        episode_id="episode_mcap_cut",
        rollout_id="placeholder",
    )
    source = SimpleNamespace(identity=identity, schema_snapshot_id="mcap-schema")
    scope = b.RecordingScope(
        organization_id=identity.organization_id,
        project_id=identity.project_id,
        region_code=identity.region_code,
    )
    auth = b.AuthContext(
        subject_id="mcap-test",
        organization_ids=frozenset({identity.organization_id}),
        project_ids=frozenset({identity.project_id}),
        region_codes=frozenset({identity.region_code}),
        organization_scope_triples=frozenset({identity.scope}),
        scope_pairs=frozenset({identity.scope[1:]}),
        capabilities=frozenset({"upload.read", "upload.manage"}),
    )
    with b.scoped(source):
        service = LeRobotWebUploadService(
            storage, raw_sources=PostgresRawSourceRepository(repository.connections)
        )
        raw_bytes = synthetic_mcap(tmp_path / "source.mcap").read_bytes()
        slice_end, expected_frames = 10, 9
        manifest = CreateLeRobotImportV1(
            dataset_id=identity.dataset_id,
            source_format="MCAP",
            files=({"path": "source.mcap", "size": len(raw_bytes), "part_count": 1},),
        )
        grant = service.begin(auth=auth, **scope.model_dump(), manifest=manifest)
        asset = grant.assets[0]
        service.upload_part(
            auth=auth,
            **scope.model_dump(),
            import_id=grant.import_id,
            dataset_id=manifest.dataset_id,
            path=asset.path,
            multipart_upload_id=asset.multipart_upload_id,
            part_number=1,
            body=io.BytesIO(raw_bytes),
            size=len(raw_bytes),
        )
        service.complete_asset(
            auth=auth,
            **scope.model_dump(),
            import_id=grant.import_id,
            command=CompleteLeRobotAssetV1(
                dataset_id=manifest.dataset_id,
                path=asset.path,
                multipart_upload_id=asset.multipart_upload_id,
                size=len(raw_bytes),
                part_count=1,
            ),
        )
        service.commit(
            auth=auth,
            **scope.model_dump(),
            import_id=grant.import_id,
            command=CommitLeRobotImportV1(manifest=manifest),
        )
        browser = OriginalSourceBrowser(storage, service.raw_sources)
        raw = browser.source(*identity.scope, grant.import_id)
        monkeypatch.setattr(mcap_processing, "connections", lambda: repository.connections)
        # Target setup/authorization is exercised by the normal target validator tests;
        # this fixture's robot is not registered as a browser data source.
        monkeypatch.setattr(mcap_processing, "validate_target", lambda *args: None)
        monkeypatch.setattr(
            mcap_processing,
            "profile",
            lambda *args: McapProcessingConfig(depth_scale_m_per_unit=0.001),
        )
        progress = mcap_processing.start(
            identity.scope,
            raw,
            browser.manifest(raw),
            StartStoredProcessing(
                collection_task_id=old_command.collection_task_id, robot_id=old_command.robot_id
            ),
        )
        processing_id = progress["files"][0]["processing_id"]
        with repository.transaction() as cursor:
            cursor.execute(
                "SELECT envelope FROM core.outbox_events "
                "WHERE envelope->'payload'->>'mcap_processing_id'=%s",
                (processing_id,),
            )
            event = b.DomainEventEnvelope.model_validate(cursor.fetchone()[0])
        handler = b.RecordingOutboxHandler(
            repository.connections, storage, registrations=b.PostgresRecordingRegistration()
        )
        with monkeypatch.context() as fault:

            def fail(*args):
                raise InterruptedError("test interruption before recording preparation")

            fault.setattr(mcap_processing, "prepare", fail)
            with pytest.raises(InterruptedError):
                handler.adopt(event)
        assert (
            mcap_processing.progress(identity.scope, raw.raw_source_id)["files"][0]["status"]
            == "FAILED"
        )
        retried = mcap_processing.start(
            identity.scope,
            raw,
            browser.manifest(raw),
            StartStoredProcessing(
                collection_task_id=old_command.collection_task_id, robot_id=old_command.robot_id
            ),
        )
        assert retried["files"][0]["attempts"] == 1
        handler.adopt(event)
        handler.adopt(event)  # Delivery redelivery must not duplicate or re-encode.
        result = mcap_processing.progress(identity.scope, raw.raw_source_id)["files"][0]
        assert result["status"] == "READY", result
        records = b.PostgresContinuousRecordingRepository(repository.connections)
        record = records.get(scope, result["recording_id"])
        assets = b.PostgresRecordingAssetRepository(repository.connections)
        upload = assets.get_upload(scope, record.recording_upload_id)
        packages = b.EpisodePackageService(repository.connections, storage, tmp_path / "packages")
        staging = b.S3ProjectionArtifactStore(
            storage._client, storage._bucket, tmp_path / "staging"
        )
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
            fragment_writers=b.ArrowFragmentWriterFactory(tmp_path / "arrow"),
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
        recording_service = b.ContinuousRecordingService(records, None, assets, storage)
        args = dict(
            auth=auth,
            **scope.model_dump(),
            recording_id=record.recording_id,
            actor_id=auth.subject_id,
        )
        draft = recording_service.save_draft(
            **args,
            command=b.SaveSliceDraftCommand.model_validate(
                {
                    "slices": [
                        {
                            "episode_id": "episode_mcap_cut",
                            "start_offset_ns": str(b.frame_time(1)),
                            "end_offset_ns": str(b.frame_time(slice_end)),
                            "title": "MCAP cut",
                        }
                    ]
                }
            ),
            if_match=record.etag,
            request_id="mcap-draft",
        )
        finalized = recording_service.finalize(
            **args,
            expected_draft_revision=draft.revision.revision,
            if_match=draft.recording.etag,
            request_id="mcap-finalize",
        )
        annotation = b.AnnotationService(b.PostgresAnnotationRepository(repository.connections))
        b.publish_schema(source, annotation)
        config = upload.command.recording_config
        profile = b.QualityProfileV1(
            profile_id="mcap-quality",
            required_topics=frozenset(
                [c.modality_key for c in config.cameras] + [s.topic for s in config.sensors]
            ),
        )
        with repository.transaction() as cursor:
            cursor.execute(
                "INSERT INTO quality_profiles(project_id,profile_id,profile_version,"
                "schema_version,profile_sha256,profile_json) "
                "VALUES (%s,%s,1,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING",
                (
                    identity.project_id,
                    profile.profile_id,
                    profile.schema_version,
                    profile.content_sha256(),
                    profile.content_bytes().decode(),
                ),
            )
        request = b.PostgresContinuousEpisodeWorkflowInputResolver(
            repository.connections, b.ForbiddenLegacy(), package_schemas=packages.schemas
        ).resolve(
            **scope.model_dump(),
            recording_id=record.recording_id,
            episode_id="episode_mcap_cut",
            finalized_revision=finalized.revision.revision,
        )
        assert request.storage_format == "lerobot_v3"

        def state(old, new, **fields):
            return processor.update_state(
                b.ContinuousEpisodeStateActivityInput(
                    **scope.model_dump(),
                    recording_id=record.recording_id,
                    episode_id="episode_mcap_cut",
                    expected_status=old,
                    new_status=new,
                    **fields,
                )
            )

        state(b.Status.PENDING_QC, b.Status.QC_RUNNING)
        qc = processor.qc(request)
        assert qc.status == "PASS", qc
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
            expected_camera_ids=tuple(plan.feature_mapping),
            media_artifacts=(),
            package_plan=plan,
        )
        data = committed.derived_ready.data
        assert data.frame_count == expected_frames
        from dataclasses import replace

        export_auth = replace(
            auth_for(data), capabilities=auth_for(data).capabilities | {"export.create"}
        )
        import zipfile

        from mcap.reader import make_reader

        from hc_data_platform.annotation.models import AnnotationTag
        from hc_data_platform.annotation.revision_files import (
            AnnotationRevisionFileService,
            PostgresRevisionFiles,
        )
        from hc_data_platform.annotation.selections import AnnotationSelections
        from tests.episode_store.test_annotation_bindings_services import actor

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
                    scope, record.recording_id, "episode_mcap_cut"
                ).workflow_id,
            )
        )
        claimed = annotation.claim(task.task_id, actor(data.identity))
        annotation.save_draft(
            task.task_id,
            actor(data.identity),
            (),
            tags=(
                AnnotationTag(
                    annotation_id="pick",
                    tag_id="p",
                    path=("p",),
                    label="抓取",
                    start_step=0,
                    end_step=2,
                ),
            ),
            expected_revision=0,
            if_match=claimed.etag,
            client_mutation_id="save-current",
        )
        revision_files = AnnotationRevisionFileService(
            annotation, PostgresRevisionFiles(repository.connections), storage
        )
        annotation_auth = replace(
            export_auth,
            capabilities=export_auth.capabilities | {"annotation_task.read", "annotation.save"},
        )
        current = revision_files.status(task.task_id, 1, annotation_auth)
        revision_files.generate(
            task.task_id, 1, current.generation, current.revision_content_hash, annotation_auth
        )
        packages.deliveries.annotations = AnnotationSelections(revision_files, packages.files)
        selection = {
            "task_id": task.task_id,
            "revision": 1,
            "allow_unreviewed": True,
            "track": {"track_id": "task-segments", "label": "任务分段", "annotation_ids": ["pick"]},
        }
        for format, annotated in [
            (f, a) for f in ("lerobot", "mcap", "original") for a in (False, True)
        ]:
            body = RequestCollectionDelivery(
                members=({"data": data, "annotation": selection if annotated else None},),
                modalities="rgbd",
                output_format=format,
            )
            job = packages.deliveries.request_collection(
                body, "mcap-pipeline-" + format + str(annotated), annotation_auth
            )
            run_job(packages.deliveries, job)
            ready = packages.deliveries.status(job.job_id, annotation_auth)
            assert ready.status == "READY", ready
            row = packages.deliveries.repository.get_job(job.job_id)
            content = b"".join(storage.read_chunks(row["artifact_key"]))
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if format == "mcap":
                    mcap = io.BytesIO(archive.read("collection/episode-000000.mcap"))
                    reader = make_reader(mcap, validate_crcs=True)
                    messages = list(reader.iter_messages())
                    assert {c.topic for _, c, _ in messages} == {
                        "/color",
                        "/depth",
                        "/joint_states",
                        "/joint_cmd",
                    }
                    assert len(messages) == expected_frames * 4
                    if annotated:
                        document = json.loads(
                            next(
                                a.data
                                for a in reader.iter_attachments()
                                if a.name == "hc/annotation.json"
                            )
                        )
                        assert document["annotations"]["revision"]["tags"][0]["label"] == "抓取"
                        assert "annotation_frame_mapping" in document
                if format == "original":
                    originals = [n for n in archive.namelist() if n.endswith("source.mcap")]
                    assert len(originals) == 1 and archive.read(originals[0]) == raw_bytes
                    document = json.loads(archive.read("collection/selections/000000.json"))
                    assert bool(document["annotations"]) == annotated
