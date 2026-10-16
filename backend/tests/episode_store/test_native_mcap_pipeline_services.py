"""Bare MCAP -> durable recording -> slice -> QC -> LeRobot primary -> exports.

The legacy catalog is a trap: any Lance access fails this test.
"""

import hashlib
import io
import json
import os
from types import SimpleNamespace
from uuid import uuid4

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
from hc_data_platform.object_storage.checksums import crc64_ecma
from tests.continuous_recordings.test_mcap_native import synthetic_mcap
from tests.episode_store import test_recording_package_services as b
from tests.episode_store.test_complete_delivery_services import run_job
from tests.episode_store.test_detail_services import auth_for
from tests.robot_ingest.test_raw_derivation_services import uploaded_raw  # noqa


@pytest.mark.integration
@pytest.mark.parametrize("ingress", ["browser", "robot"])
def test_bare_mcap_to_slice_package_and_all_exports(
    uploaded_raw,  # noqa: F811
    tmp_path,
    monkeypatch,
    ingress,
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
        if ingress == "robot":
            robot_upload = upload_robot_mcap(
                repository, storage, old_upload, old_command, raw_bytes
            )
            raw_id = robot_upload.raw_source_id
        else:
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
            raw_id = grant.import_id
        browser = OriginalSourceBrowser(storage, service.raw_sources)
        raw = browser.source(*identity.scope, raw_id)
        monkeypatch.setattr(mcap_processing, "connections", lambda: repository.connections)
        # Target setup/authorization is exercised by the normal target validator tests;
        # this fixture's robot is not registered as a browser data source.
        monkeypatch.setattr(mcap_processing, "validate_target", lambda *args: None)
        monkeypatch.setattr(
            mcap_processing,
            "profile",
            lambda *args: McapProcessingConfig(depth_scale_m_per_unit=0.001),
        )
        if ingress == "robot":
            # Robot commit must create the durable event itself; no manual process call.
            progress = mcap_processing.progress(identity.scope, raw_id)
        else:
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
        if ingress == "robot":
            from hc_data_platform.lerobot_imports.recording_recovery import retry_recording_source

            assert retry_recording_source(*identity.scope, raw_id, factory=repository.connections)
            retried = mcap_processing.progress(identity.scope, raw_id)
        else:
            assert mcap_processing.retry_source(identity.scope, raw_id, repository.connections)
            retried = mcap_processing.progress(identity.scope, raw_id)
        assert retried["files"][0]["attempts"] == 1
        handler.adopt(event)  # Superseded attempts cannot restart or fail the retry.
        mcap_processing.mark_failed(repository.connections, event)
        assert (
            mcap_processing.progress(identity.scope, raw.raw_source_id)["files"][0]["status"]
            == "PENDING"
        )
        with repository.transaction() as cursor:
            cursor.execute(
                "SELECT envelope FROM core.outbox_events "
                "WHERE envelope->'payload'->>'mcap_processing_id'=%s "
                "AND envelope->'payload'->>'attempt'='1'",
                (processing_id,),
            )
            event = b.DomainEventEnvelope.model_validate(cursor.fetchone()[0])
        handler.adopt(event)
        handler.adopt(event)  # Delivery redelivery must not duplicate or re-encode.
        result = mcap_processing.progress(identity.scope, raw.raw_source_id)["files"][0]
        assert result["status"] == "READY", result
        if ingress == "robot":
            from hc_data_platform.collection_tasks.captures import read_captures
            from hc_data_platform.robot_ingest.processing_dispatch import mcap_result

            assert mcap_result(repository.connections, robot_upload)["status"] == "AWAITING_SLICE"
            with repository.transaction() as cursor:
                captures = read_captures(cursor, *identity.scope, old_command.collection_task_id)
            capture = next(c for c in captures if c.upload_id == robot_upload.upload_id)
            assert capture.recording_id == result["recording_id"]
            assert capture.stage == "AWAITING_SLICE" and capture.qc_outcome is None
        records = b.PostgresContinuousRecordingRepository(repository.connections)
        record = records.get(scope, result["recording_id"])
        assets = b.PostgresRecordingAssetRepository(repository.connections)
        upload = assets.get_upload(scope, record.recording_upload_id)
        # Both RGB and physical depth are committed MP4 media, not loose source images.

        declarations = {a.path: a for a in upload.command.assets}
        assert {c.codec for c in upload.command.recording_config.cameras} == {"h264", "hevc"}
        assert len([a for a in declarations.values() if a.role.value == "RAW_VIDEO"]) == 2
        assert any(p.endswith("_depth.mp4") for p in declarations)
        assert any(p.endswith("validity/flags.parquet") for p in declarations)
        assert any(p.endswith("report.json") for p in declarations)
        with repository.transaction() as cursor:
            cursor.execute(
                "SELECT source_document FROM ingest.mcap_processing_files WHERE processing_id=%s",
                (processing_id,),
            )
            original = cursor.fetchone()[0]
        assert b"".join(storage.read_chunks(original["object_key"])) == raw_bytes
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
            aligned_media_camera_count=len(plan.feature_mapping),
        )
        if ingress == "robot":
            with repository.transaction() as cursor:
                captures = read_captures(cursor, *identity.scope, old_command.collection_task_id)
            capture = next(c for c in captures if c.upload_id == robot_upload.upload_id)
            assert capture.stage == "READY" and capture.qc_outcome == "PASS"
            assert capture.ready_episode_count == 1
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
                    # Slice export copies original message payloads and clocks; it never
                    # reconstructs ROS data from the lossy RGB/depth preview videos.
                    expected = list(
                        make_reader(io.BytesIO(raw_bytes)).iter_messages(
                            start_time=1_000_000_000 + b.frame_time(1),
                            end_time=1_000_000_000 + b.frame_time(slice_end),
                        )
                    )

                    def evidence(rows):
                        return [
                            (
                                s.name,
                                s.encoding,
                                s.data,
                                c.topic,
                                c.message_encoding,
                                m.log_time,
                                m.publish_time,
                                m.sequence,
                                m.data,
                            )
                            for s, c, m in rows
                        ]

                    assert evidence(messages) == evidence(expected)
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
                        mapping = "collection/" + document["annotation_frame_mapping"]
                        assert mapping in archive.namelist()
                        import pyarrow.parquet as pq

                        assert (
                            pq.read_table(io.BytesIO(archive.read(mapping))).num_rows
                            == expected_frames
                        )
                if format == "original":
                    originals = [n for n in archive.namelist() if n.endswith("source.mcap")]
                    assert len(originals) == 1 and archive.read(originals[0]) == raw_bytes
                    document = json.loads(archive.read("collection/selections/000000.json"))
                    assert bool(document["annotations"]) == annotated


def upload_robot_mcap(
    repository,
    storage,
    old_upload,
    command,
    content,
    *,
    configure=True,
    asset_path="source.mcap",
    queued=True,
):
    from hc_data_platform.robot_ingest.models import (
        CompleteAssetCommand,
        CompletedPart,
        IssueCredentialCommand,
        RobotIngestAssetManifest,
        RobotIngestUploadManifest,
        UpdateRobotIngestIdentity,
    )
    from hc_data_platform.robot_ingest.repository import PostgresRobotIngestRepository
    from hc_data_platform.robot_ingest.service import RobotIngestService

    target = old_upload.target
    scope = (target.organization_id, target.project_id, target.region_code)
    auth = b.AuthContext(
        subject_id="mcap-robot-test",
        organization_ids=frozenset({scope[0]}),
        project_ids=frozenset({scope[1]}),
        region_codes=frozenset({scope[2]}),
        organization_scope_triples=frozenset({scope}),
        scope_pairs=frozenset({scope[1:]}),
        organization_scoped_capabilities=frozenset({(scope[0], scope[1], "ingest_source.manage")}),
    )
    service = RobotIngestService(
        PostgresRobotIngestRepository.from_dsn(
            os.environ["HC_G1_TEST_POSTGRES_DSN"], repository.connections
        ),
        storage,
        credential_hmac_key="mcap-robot-integration-key",
    )
    identity_args = dict(
        auth=auth,
        organization_id=scope[0],
        project_id=scope[1],
        ingest_identity_id=old_upload.ingest_identity_id,
    )
    existing = service.repository.get_identity(scope[0], old_upload.ingest_identity_id)
    service.update_identity(
        **identity_args,
        command=UpdateRobotIngestIdentity(
            allowed_formats=("MCAP",),
            allowed_transports=existing.allowed_transports,
            upload_policy=existing.upload_policy,
        ),
    )
    token = service.issue_credential(
        **identity_args, command=IssueCredentialCommand()
    ).credential.token
    if configure:
        with repository.transaction() as cursor:
            cursor.execute(
                "INSERT INTO ingest.mcap_robot_profiles VALUES (%s,%s,%s,%s,%s::jsonb,now())",
                (*scope, command.robot_id, '{"depth_scale_m_per_unit":0.001}'),
            )
    upload = service.create_upload(
        token=token,
        manifest=RobotIngestUploadManifest(
            client_upload_id=str(uuid4()),
            collection_task_id=command.collection_task_id,
            robot_id=command.robot_id,
            source_format="MCAP",
            source_format_version="0",
            capture_mode="CONTINUOUS",
            capture_started_at=command.capture_started_at,
            capture_ended_at=command.capture_ended_at,
            assets=(
                RobotIngestAssetManifest(
                    asset_id="mcap",
                    path=asset_path,
                    size_bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                    crc64=crc64_ecma(content),
                    media_type="application/mcap",
                ),
            ),
        ),
    ).data
    asset = upload.assets[0]
    part = storage.upload_part_stream(
        asset.object_key, asset.multipart_upload_id, 1, io.BytesIO(content), len(content)
    )
    service.complete_asset(
        token=token,
        upload_id=upload.upload_id,
        asset_id=asset.asset_id,
        command=CompleteAssetCommand(parts=(CompletedPart(part_number=1, etag=part.etag),)),
    )
    committed = service.commit_upload(token=token, upload_id=upload.upload_id).data
    assert (
        service.commit_upload(token=token, upload_id=upload.upload_id).data.raw_source_id
        == committed.raw_source_id
    )
    with repository.transaction() as cursor:
        cursor.execute(
            "SELECT count(*) FROM core.outbox_events WHERE organization_id=%s "
            "AND project_id=%s AND region_code=%s AND envelope->>'aggregate_id'=%s",
            (*scope, committed.raw_source_id),
        )
        assert cursor.fetchone()[0] == int(queued)
    return committed


@pytest.mark.integration
def test_robot_mcap_missing_mapping_settles_and_retries_original(
    uploaded_raw,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    from hc_data_platform.collection_tasks.captures import read_captures
    from hc_data_platform.lerobot_imports.recording_recovery import retry_recording_source
    from hc_data_platform.robot_ingest.processing_dispatch import mcap_result

    repository, storage, old, _, command, _ = uploaded_raw
    scope = (old.target.organization_id, old.target.project_id, old.target.region_code)
    monkeypatch.setattr(mcap_processing, "connections", lambda: repository.connections)
    content = synthetic_mcap(tmp_path / "source.mcap").read_bytes()
    upload = upload_robot_mcap(repository, storage, old, command, content, configure=False)

    def queued_event():
        progress = mcap_processing.progress(scope, upload.raw_source_id)["files"][0]
        with repository.transaction() as cursor:
            cursor.execute(
                "SELECT envelope FROM core.outbox_events "
                "WHERE envelope->'payload'->>'mcap_processing_id'=%s ORDER BY occurred_at DESC",
                (progress["processing_id"],),
            )
            return b.DomainEventEnvelope.model_validate(cursor.fetchone()[0])

    handler = b.RecordingOutboxHandler(
        repository.connections, storage, registrations=b.PostgresRecordingRegistration()
    )
    old_event = queued_event()
    with pytest.raises(ValueError, match="MCAP_DEPTH_UNITS_REQUIRED"):
        handler.adopt(old_event)
    status = mcap_result(repository.connections, upload)
    assert status["terminal"] and status["status"] == "FAILED"
    assert status["error_code"] == "MCAP_DEPTH_UNITS_REQUIRED"
    with repository.transaction() as cursor:
        item = next(c for c in read_captures(cursor, *scope) if c.upload_id == upload.upload_id)
    assert item.stage == "BLOCKED" and item.qc_outcome is None
    assert not retry_recording_source(
        scope[0], scope[1], "foreign-region", upload.raw_source_id, factory=repository.connections
    )
    # This robot came through robot ingest, without a separate browser data-source row.
    for config, expected in (
        ({"state_topic": "/absent"}, "MCAP_MAPPING_INVALID"),
        ({}, "MCAP_DEPTH_UNITS_REQUIRED"),
    ):
        mcap_processing.profile(scope, command.robot_id, config)
        assert retry_recording_source(*scope, upload.raw_source_id, factory=repository.connections)
        event = queued_event()
        assert event.event_id != old_event.event_id
        with pytest.raises(ValueError, match=expected):
            handler.adopt(event)
        assert mcap_result(repository.connections, upload)["error_code"] == expected
    mcap_processing.profile(scope, command.robot_id, {"depth_scale_m_per_unit": 0.001})
    assert retry_recording_source(*scope, upload.raw_source_id, factory=repository.connections)
    current = queued_event()
    assert current.event_id != old_event.event_id
    handler.adopt(old_event)  # A superseded configuration cannot run after repair.
    assert mcap_processing.progress(scope, upload.raw_source_id)["files"][0]["status"] == "PENDING"
    handler.adopt(current)
    assert mcap_result(repository.connections, upload)["status"] == "AWAITING_SLICE"
    asset = upload.assets[0]
    assert b"".join(storage.read_chunks(asset.object_key)) == content


@pytest.mark.integration
def test_unsupported_mcap_layout_retains_receipt_and_reports_failure(
    uploaded_raw,  # noqa: F811
    tmp_path,
):
    from hc_data_platform.collection_tasks.captures import read_captures

    repository, storage, old, _, command, _ = uploaded_raw
    scope = (old.target.organization_id, old.target.project_id, old.target.region_code)
    content = synthetic_mcap(tmp_path / "source.mcap").read_bytes()
    upload = upload_robot_mcap(
        repository,
        storage,
        old,
        command,
        content,
        configure=False,
        asset_path="unsupported.bin",
        queued=False,
    )
    assert upload.state.value == "COMMITTED"
    assert upload.processing_status.value == "FAILED"
    with repository.transaction() as cursor:
        item = next(c for c in read_captures(cursor, *scope) if c.upload_id == upload.upload_id)
    assert item.stage == "FAILED" and item.error_code == "ROBOT_PROCESSOR_UNAVAILABLE"
    assert item.received and item.qc_outcome is None
    assert b"".join(storage.read_chunks(upload.assets[0].object_key)) == content
