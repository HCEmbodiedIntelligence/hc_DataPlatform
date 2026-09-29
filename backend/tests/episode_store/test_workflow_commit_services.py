"""Actual commit activity with real staged Arrow, media, PG/MinIO and projection.

The source inventory is synthetic. The writer workflow uses the isolated Temporal
service; full upload/ingestion and real sensor recording acceptance remain separate.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic import ValidationError
from temporalio import activity
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from temporalio.worker import Replayer, Worker

from hc_data_platform.aligned_media.models import AlignedMediaSelectorV1, AlignmentStagingArtifactV1
from hc_data_platform.aligned_media.service import AlignedMediaAuthorizationService
from hc_data_platform.alignment.arrow_writer import ArrowFragmentWriter
from hc_data_platform.alignment.models import AlignedFragmentManifestV1, AlignedRowV1
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.dataset_registry.ingest_projection import PostgresDatasetIngestProjector
from hc_data_platform.episode_store.bundle_plan import EpisodeBundlePlan
from hc_data_platform.ingest.models import ManifestCameraV1, ManifestTopicV1
from hc_data_platform.lance_catalog.adapters import LanceAdapter
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot
from hc_data_platform.publishing.depth_evidence import file_hash
from hc_data_platform.runtime import ArrowCatalogFragmentAdapter
from hc_data_platform.workflow import activities
from hc_data_platform.workflow.ingest_plan import PostgresIngestWorkflowInputResolver
from hc_data_platform.workflow.models import (
    AlignedBundleCommitActivityInput,
    AlignmentActivityInput,
    DatasetWriterWorkflowInput,
    JobStatus,
)
from hc_data_platform.workflow.names import COMMIT_ALIGNED_BUNDLE_ACTIVITY
from hc_data_platform.workflow.projection_store import S3ProjectionArtifactStore
from hc_data_platform.workflow.temporal_workflows import DatasetWriterWorkflow
from tests.dataset_registry.test_ingest_projection_postgres import (
    REGION,
    _manifest,
    _seed_manifest,
    _source,
)
from tests.episode_store.test_bundle_assets_services import forbidden
from tests.episode_store.test_commit_services import scoped


@pytest.fixture
def commit_input(prepared_bundle, services, tmp_path):
    packages, source, bundle, assets, _ = prepared_bundle
    repository, storage = services
    ident = source.identity
    schema = DatasetSchemaSnapshot.create(
        project_id=ident.project_id,
        dataset_id=ident.dataset_id,
        schema_snapshot_id=source.schema_snapshot_id,
        frequency_hz=30,
        fields={
            "/observation/state": "list<float64>",
            "/action": "list<float64>",
            **{topic: "json" for topic in bundle["feature_mapping"]},
        },
    )
    manifest = _manifest(
        project_id=ident.project_id,
        rollout_id=ident.rollout_id,
        data_package_id="data-" + uuid4().hex,
        sequence_no=1,
        source_sha256=source.source_sha256,
    ).model_copy(
        update={
            "task_id": "task-" + uuid4().hex,
            "start_time": datetime(1970, 1, 1, tzinfo=timezone.utc),
            "end_time": datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=133333),
            "cameras": [
                ManifestCameraV1(camera_id=feature, topic=topic)
                for topic, feature in bundle["feature_mapping"].items()
            ],
            "topics": [ManifestTopicV1(name=key, required=True) for key in schema.fields],
            "expected_topics": list(schema.fields),
            "actual_topics": list(schema.fields),
        }
    )
    session = str(uuid4())
    fingerprint = _seed_manifest(
        os.environ["HC_G1_TEST_POSTGRES_DSN"],
        organization_id=ident.organization_id,
        project_id=ident.project_id,
        dataset_id=ident.dataset_id,
        session_id=session,
        manifest=manifest,
    )
    raw = _source(
        organization_id=ident.organization_id,
        project_id=ident.project_id,
        session_id=session,
        manifest=manifest,
        manifest_fingerprint=fingerprint,
    )
    with scoped(source), repository.transaction() as cursor:
        cursor.execute(
            """INSERT INTO ingest.rollout_objects
            (project_id,region_code,rollout_id,object_key,manifest_key,source_sha256,
             crc64,file_size,status,committed_at,data_package_id)
            VALUES (%s,%s,%s,%s,%s,%s,0,128,'COMMITTED',clock_timestamp(),%s)""",
            (
                ident.project_id,
                ident.region_code,
                ident.rollout_id,
                raw.object_key,
                raw.manifest_key,
                source.source_sha256,
                raw.data_package_id,
            ),
        )
    writer = ArrowFragmentWriter(tmp_path / "arrow")
    writer.begin(rollout_id=ident.rollout_id, attempt_id=bundle["attempt_id"])
    for index in range(4):
        timestamp = index * 33_333_333
        values = {
            "/observation/state": [0.1 + index * 1e-12],
            "/action": [float(index)],
            **{
                key: {"source_frame_index": index, "source_pts_ns": timestamp}
                for key in bundle["feature_mapping"]
            },
        }
        writer.write_row(
            AlignedRowV1(
                rollout_id=ident.rollout_id,
                step_index=index,
                timestamp_ns=timestamp,
                sample_valid=True,
                modalities={
                    key: {
                        "value": value,
                        "source_timestamps_ns": (timestamp,),
                        "time_error_ns": 0,
                        "valid": True,
                        "strategy": "nearest",
                    }
                    for key, value in values.items()
                },
            )
        )
    uri = writer.commit(row_count=4, content_sha256="a" * 64, schema_sha256=schema.fingerprint)
    path = Path(uri.removeprefix("file://"))
    store = S3ProjectionArtifactStore(storage._client, storage._bucket, tmp_path / "localized")
    key = "g1-activity-fixtures/" + ident.organization_id + "/aligned.arrow"
    sha = file_hash(path)
    store.publish_file(key, path, sha256=sha)
    now = datetime.now(timezone.utc)
    staging = AlignmentStagingArtifactV1(
        object_key=key,
        content_sha256=sha,
        size_bytes=path.stat().st_size,
        row_count=4,
        alignment_version=bundle["alignment_version"],
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    staged = AlignedFragmentManifestV1(
        rollout_id=ident.rollout_id,
        source_sha256=source.source_sha256,
        attempt_id=bundle["attempt_id"],
        profile_id="g1-activity/1",
        converter_version=source.processor_version,
        frequency_hz=30,
        row_count=4,
        schema_sha256=schema.fingerprint,
        content_sha256="a" * 64,
        staging_uri="worker-local-uri-not-authorized-for-reads",
    )
    alignment = AlignmentActivityInput(
        organization_id=ident.organization_id,
        project_id=ident.project_id,
        region_code=ident.region_code,
        dataset_id=ident.dataset_id,
        schema_snapshot_id=schema.schema_snapshot_id,
        source=raw,
        profile={
            "profile_id": staged.profile_id,
            "converter_version": source.processor_version,
            "frequency_hz": 30,
            "required_modalities": frozenset(schema.fields),
        },
    )
    plan = EpisodeBundlePlan.freeze(
        input=source,
        artifacts=bundle["artifacts"],
        schema_snapshot=schema,
        metadata={"task": "synthetic RGB-D commit activity", "source": {"status": "fixture"}},
        alignment_sha256=sha,
        alignment_version=staging.alignment_version,
        alignment_profile=alignment.profile,
        frame_count=4,
        feature_mapping=bundle["feature_mapping"],
        depth_contracts=bundle["depth_contracts"],
        inventory=bundle["inventory"],
    )
    request = AlignedBundleCommitActivityInput(
        alignment=alignment,
        staged_manifest=staged,
        alignment_staging=staging,
        package_plan=plan,
        expected_camera_ids=tuple(bundle["feature_mapping"]),
        media_artifacts=bundle["artifacts"],
    )
    yield request, packages, store, assets


def input_options():
    return {
        "mcap_topics": True,
        "identity": {
            "project_id": "activity-project-" + uuid4().hex,
            "dataset_id": "dataset_activity_" + uuid4().hex,
            "region_code": REGION,
            "episode_id": "episode_activity",
            "rollout_id": "rollout-activity",
        },
    }


def configure_commit(monkeypatch, packages, store, services):
    repository, storage = services

    class NoLance:
        def __getattr__(self, name):
            raise AssertionError("package activity accessed the Lance catalog: " + name)

    legacy = NoLance()
    monkeypatch.setattr(LanceAdapter, "stage_attempt", forbidden)
    monkeypatch.setattr(LanceAdapter, "commit_staged", forbidden)
    projector = PostgresDatasetIngestProjector(repository.connections, episode_packages=packages)
    dependencies = activities.ActivityDependencies(
        catalog=legacy,
        catalog_fragments=ArrowCatalogFragmentAdapter(legacy),
        aligned_media_repository=packages.bundle_assets.media_repository,
        alignment_staging=store,
        episode_packages=packages,
        ingest_projection=PostgresIngestWorkflowInputResolver(
            repository.connections, storage, legacy
        ),
        dataset_ingest_projection=projector,
    )
    monkeypatch.setattr(activities, "_dependencies", dependencies)
    return projector


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("prepared_bundle", [input_options()], indirect=True)
async def test_commit_activity_rgbd_recovery_and_concurrency_without_lance(
    commit_input, monkeypatch, services, prepared_bundle
):
    request, packages, store, assets = commit_input
    repository, storage = services
    source = request.package_plan.input
    ident = source.identity
    projector = configure_commit(monkeypatch, packages, store, services)
    payloads = await pydantic_data_converter.encode([request])
    (decoded,) = await pydantic_data_converter.decode(payloads, [AlignedBundleCommitActivityInput])
    assert decoded == request
    assert decoded.expected_dataset_version is None
    assert "lance_version" not in decoded.package_plan.model_dump_json()
    calls, real_project = 0, projector.project

    def lose_response(**kwargs):
        nonlocal calls
        result = real_project(**kwargs)
        calls += 1
        if calls == 1:
            raise InterruptedError("lost activity response after durable product projection")
        return result

    monkeypatch.setattr(projector, "project", lose_response)
    with pytest.raises(InterruptedError):
        await ActivityEnvironment().run(activities.commit_aligned_bundle, decoded)
    with scoped(source):
        first = packages.files.open(ident, source.data_version_id)
        original_hash = first.receipt.data.content_hash
        artifact = decoded.media_artifacts[0]
        legacy_authorization = AlignedMediaAuthorizationService(
            repository=packages.bundle_assets.media_repository, store=prepared_bundle[4]
        )
        with pytest.raises(ProblemException) as rejected:
            legacy_authorization.authorize(
                artifact.scope,
                AlignedMediaSelectorV1(
                    project_id=ident.project_id,
                    dataset_id=ident.dataset_id,
                    rollout_id=ident.rollout_id,
                    dataset_version=artifact.dataset_version,
                    camera_id=artifact.camera_id,
                ),
            )
        assert rejected.value.problem.status == 425
    # Remove only this test's temporary Arrow, never the raw/video inputs. A late
    # retry must finish from the independent committed package after staging GC.
    storage._client.delete_object(Bucket=storage._bucket, Key=decoded.alignment_staging.object_key)
    assert storage.head(decoded.alignment_staging.object_key) is None
    monkeypatch.setattr(store, "local_file", forbidden)
    monkeypatch.setattr(packages.bundle_assets, "load", forbidden)
    monkeypatch.setattr(packages.committer, "commit", forbidden)
    # The retry reuses ready display derivatives; the grayscale depth primary
    # and source RGB are copied as before, never re-quantized/re-encoded.
    monkeypatch.setattr(packages.previews, "_encode", forbidden)
    reversed_receipts = decoded.model_copy(
        update={"media_artifacts": tuple(reversed(decoded.media_artifacts))}
    )
    results = await asyncio.gather(
        *(
            ActivityEnvironment().run(activities.commit_aligned_bundle, item)
            for item in (decoded, reversed_receipts, decoded, reversed_receipts)
        ),
        return_exceptions=True,
    )
    assert not [result for result in results if isinstance(result, BaseException)], results
    assert all(result == results[0] for result in results)
    result = results[0]
    assert result.derived_ready.data.content_hash == original_hash
    assert result.version.version == 1
    assert result.viewer_target.version_id == result.version.version_id
    assert "lance_version" not in result.model_dump_json()
    with scoped(source):
        package = packages.files.open(ident, source.data_version_id)
        assert (
            packages.collections.current(ident.dataset_id, project_id=ident.project_id).version == 1
        )
        for feature, video in assets.videos.items():
            entry = package.entries[f"videos/{feature}/chunk-000/file-000.mp4"]
            assert entry.sha256 == video.sha256
        assert packages.depth.read(
            ident, source.data_version_id, "observation.images.head_depth", 2, unit="m"
        ).values[1, 1] == pytest.approx(1.5, abs=0.002)
        rows = packages.steps.read_steps(ident, source.data_version_id, 0, 4).steps
        assert rows[3].modalities["/observation/state"] == [0.1 + 3e-12]
        with repository.transaction() as cursor:
            cursor.execute(
                "SELECT count(*) FROM lance_dataset_versions WHERE project_id=%s",
                (ident.project_id,),
            )
            assert cursor.fetchone()[0] == 0
    keys = storage._client.list_objects_v2(
        Bucket=storage._bucket, Prefix=source.package_prefix
    ).get("Contents", [])
    assert keys and all(".lance" not in entry["Key"] for entry in keys)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("prepared_bundle", [input_options()], indirect=True)
async def test_initial_package_commit_race_uses_ordered_media_leases(
    commit_input, monkeypatch, services
):
    request, packages, store, _ = commit_input
    configure_commit(monkeypatch, packages, store, services)
    barrier = Barrier(4)
    recover, encode = packages.recover_bundle, packages.previews._encode
    encodings = []

    def first_attempts(plan):
        result = recover(plan)
        assert result is None
        barrier.wait(timeout=10)
        return result

    def observed_encode(*args, **kwargs):
        encodings.append(1)
        return encode(*args, **kwargs)

    monkeypatch.setattr(packages, "recover_bundle", first_attempts)
    monkeypatch.setattr(packages.previews, "_encode", observed_encode)
    reverse = request.model_copy(
        update={"media_artifacts": tuple(reversed(request.media_artifacts))}
    )
    results = await asyncio.gather(
        *(
            ActivityEnvironment().run(activities.commit_aligned_bundle, item)
            for item in (request, reverse, request, reverse)
        ),
        return_exceptions=True,
    )
    assert not [result for result in results if isinstance(result, BaseException)], results
    assert all(result == results[0] for result in results)
    assert len(encodings) == 2  # Exactly one RGB and one official depth display derivative.
    with scoped(request.package_plan.input):
        versions = packages.repository.list_versions(request.package_plan.input.identity)
        assert len(versions) == 1


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("prepared_bundle", [input_options()], indirect=True)
async def test_real_temporal_package_writer_retry_and_history_replay(
    commit_input, monkeypatch, services
):
    request, packages, store, _ = commit_input
    namespace = os.environ["HC_TEMPORAL_NAMESPACE"]
    assert namespace == "chayi-g1", "this service test must only use the isolated namespace"
    client = await Client.connect(
        os.environ["HC_TEMPORAL_TARGET"],
        namespace=namespace,
        data_converter=pydantic_data_converter,
    )
    projector = configure_commit(monkeypatch, packages, store, services)
    real_project, attempts = projector.project, []

    def lose_response_once(**kwargs):
        result = real_project(**kwargs)
        attempts.append(activity.info().attempt)
        if len(attempts) == 1:
            raise InterruptedError("lost durable projection response; actual Temporal retry")
        return result

    monkeypatch.setattr(projector, "project", lose_response_once)
    queue = "chayi-g1-package-writer-" + uuid4().hex
    writer_input = DatasetWriterWorkflowInput(
        project_id=request.alignment.project_id,
        dataset_id=request.alignment.dataset_id,
        resource_id=request.alignment.source.rollout_id,
        commit=request,
    )
    # Production workflow/activity, dedicated queue on the real isolated service.
    async with Worker(
        client,
        task_queue=queue,
        workflows=[DatasetWriterWorkflow],
        activities=[activities.commit_aligned_bundle],
    ):
        handle = await client.start_workflow(
            DatasetWriterWorkflow.run,
            writer_input,
            id=queue,
            task_queue=queue,
            execution_timeout=timedelta(seconds=60),
        )
        try:
            result = await asyncio.wait_for(handle.result(), timeout=60)
        except BaseException:
            await handle.cancel()
            raise
        assert result.status is JobStatus.SUCCEEDED, result
        assert attempts == [1, 2]
        assert result.result["derived_ready"]["data"]["storage_format"] == "lerobot_v3"
        assert "lance_version" not in result.model_dump_json()
        history = await handle.fetch_history()
    await Replayer(
        workflows=[DatasetWriterWorkflow],
        data_converter=pydantic_data_converter,
    ).replay_workflow(history)
    scheduled = [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]
    assert scheduled == [COMMIT_ALIGNED_BUNDLE_ACTIVITY]
    with scoped(request.package_plan.input):
        assert len(packages.repository.list_versions(request.package_plan.input.identity)) == 1
    evidence = Path(os.environ["HC_G1_WRITER_EVIDENCE_ROOT"])
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "episode-package-writer-history.json").write_text(history.to_json())


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("prepared_bundle", [input_options()], indirect=True)
async def test_frozen_commit_plan_rejects_changed_configuration_scope_and_receipts(commit_input):
    request, packages, _, _ = commit_input
    plan = request.package_plan
    for field, value in (
        ("metadata", {"task": "changed"}),
        ("alignment_sha256", "0" * 64),
        ("media_inputs_sha256", "0" * 64),
        ("frame_count", 5),
    ):
        payload = plan.model_dump(mode="json")
        payload[field] = value
        with pytest.raises(ValidationError):
            EpisodeBundlePlan.model_validate(payload)
    payload = request.model_dump(mode="json")
    payload["expected_dataset_version"] = 1
    with pytest.raises(ValidationError, match="cannot reserve"):
        AlignedBundleCommitActivityInput.model_validate(payload)
    payload["expected_dataset_version"] = None
    payload["alignment"]["organization_id"] = "foreign"
    with pytest.raises(ValidationError, match="frozen bundle"):
        AlignedBundleCommitActivityInput.model_validate(payload)
    # A historical activity payload remains decodable without any new field.
    legacy = request.model_dump(mode="json", exclude={"package_plan"})
    legacy["expected_dataset_version"] = 1
    assert AlignedBundleCommitActivityInput.model_validate(legacy).package_plan is None
    mixed = request.model_dump(mode="json")
    mixed["fragment"] = {}
    with pytest.raises(ValidationError, match="cannot mix"):
        DatasetWriterWorkflowInput.model_validate(
            {
                "project_id": request.alignment.project_id,
                "dataset_id": request.alignment.dataset_id,
                "resource_id": request.alignment.source.rollout_id,
                "commit": mixed,
            }
        )
    # Worker-local mutations also fail before taking a media lease or writing data.
    plan.metadata["task"] = "changed in place"
    with pytest.raises(ApplicationError, match="configuration/schema"):
        await ActivityEnvironment().run(activities.commit_aligned_bundle, request)
    with scoped(plan.input):
        assert packages.repository.list_versions(plan.input.identity) == ()
