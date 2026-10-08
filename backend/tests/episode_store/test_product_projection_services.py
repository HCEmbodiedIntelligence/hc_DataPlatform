"""Immutable product snapshots from committed RGB-D collections (synthetic input)."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from hc_data_platform.alignment.models import AlignmentInputV1
from hc_data_platform.dataset_registry.ingest_projection import (
    DatasetIngestProjectionConflict,
    PostgresDatasetIngestProjector,
)
from hc_data_platform.dataset_registry.models import DatasetPageEpisodeRevision
from hc_data_platform.episode_store.catalog_models import EpisodeDerivedReady
from hc_data_platform.episode_store.service import EpisodePackageService
from hc_data_platform.ingest.models import ManifestCameraV1, ManifestTopicV1
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot
from hc_data_platform.workflow.models import (
    AlignedBundleCommitActivityOutput,
    ContinuousEpisodeProjectionSourceV1,
)
from tests.dataset_registry.test_ingest_projection_postgres import (
    REGION,
    _manifest,
    _seed_manifest,
    _source,
)
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_writer import build_rgbd_package, identity


@pytest.mark.integration
@pytest.mark.parametrize("ingress", ["continuous", "mcap"])
def test_project_rgbd_append_reprocess_retry_and_atomic_recovery(
    services, tmp_path, monkeypatch, ingress
):
    repository, storage = services
    run = uuid4().hex
    task_id = "task-projection-" + run
    ident = identity().model_copy(
        update={
            "organization_id": "projection-" + run,
            "project_id": "projection-project-" + run,
            "region_code": REGION,
            "dataset_id": "dataset_projection_" + run,
            "episode_id": "episode_explicit_first",
            "rollout_id": "rollout-explicit-first",
        }
    )
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    root, source, _, assets = build_rgbd_package(first_dir, ident, mcap_topics=ingress == "mcap")
    camera_topics = {
        key: "/camera/" + key.removeprefix("observation.images.") + "/image"
        if ingress == "mcap"
        else key
        for key in assets.videos
    }
    schema = DatasetSchemaSnapshot.create(
        project_id=ident.project_id,
        dataset_id=ident.dataset_id,
        schema_snapshot_id="schema-1",
        frequency_hz=30,
        fields={
            "/observation/state": "list<float64>",
            "/action" if ingress == "mcap" else "action": "list<float64>",
            **{key: "json" for key in camera_topics.values()},
        },
    )
    service = EpisodePackageService(repository.connections, storage, tmp_path / "service")
    projector = PostgresDatasetIngestProjector(repository.connections, episode_packages=service)
    now = datetime.now(timezone.utc)
    sources = {}

    def publish(package_root, package_source):
        if ingress == "mcap" and package_source.identity.rollout_id not in sources:
            manifest = _manifest(
                project_id=ident.project_id,
                rollout_id=package_source.identity.rollout_id,
                data_package_id="data-" + uuid4().hex,
                sequence_no=len(sources) + 1,
                source_sha256=package_source.source_sha256,
            ).model_copy(
                update={
                    "task_id": task_id,
                    "cameras": [
                        ManifestCameraV1(camera_id=key, topic=topic, encoding="jpeg")
                        for key, topic in camera_topics.items()
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
            sources[package_source.identity.rollout_id] = _source(
                organization_id=ident.organization_id,
                project_id=ident.project_id,
                session_id=session,
                manifest=manifest,
                manifest_fingerprint=fingerprint,
            )
        receipt = service.committer.commit(package_root, package_source)
        version = service.collections.publish(receipt.data, schema)
        return version, EpisodeDerivedReady(
            data=receipt.data,
            collection_version_id=version.collection_version_id,
            project_id=ident.project_id,
            dataset_id=ident.dataset_id,
            rollout_id=package_source.identity.rollout_id,
            source_sha256=package_source.source_sha256,
            converter_version=package_source.processor_version,
            dataset_version=version.version,
            step_count=receipt.data.frame_count,
            content_hash=version.content_hash,
        )

    def project(package_source, publication):
        version, ready = publication
        facts = ContinuousEpisodeProjectionSourceV1(
            organization_id=ident.organization_id,
            project_id=ident.project_id,
            region_code=ident.region_code,
            recording_id="recording-parent",
            recording_upload_id="upload-parent",
            episode_id=package_source.identity.episode_id,
            rollout_id=package_source.identity.rollout_id,
            data_package_id="raw-parent-package",
            collection_task_id=task_id,
            robot_id="fixture-robot",
            source_sha256=package_source.source_sha256,
            manifest_fingerprint="7" * 64,
            source_size_bytes=1000,
            started_at=now,
            ended_at=now + timedelta(seconds=1),
            camera_modality_keys=tuple(camera_topics.values()),
        )
        alignment = AlignmentInputV1(
            rollout_id=facts.rollout_id,
            source_sha256=facts.source_sha256,
            attempt_id="fixture-alignment",
            start_ns=0,
            end_ns=133_333_333,
            streams={
                key: {
                    "kind": "image" if key in camera_topics.values() else "continuous",
                    "samples": [],
                }
                for key in schema.fields
            },
        )
        with scoped(package_source):
            if ingress == "mcap":
                return projector.project(
                    source=sources[package_source.identity.rollout_id],
                    alignment=alignment,
                    schema_snapshot_id=schema.schema_snapshot_id,
                    frequency_hz=30,
                    version=version,
                    ready=ready,
                    media_artifacts=(),
                )
            return projector.project_continuous_episode(
                source=facts,
                alignment=alignment,
                schema_snapshot_id=schema.schema_snapshot_id,
                frequency_hz=30,
                version=version,
                ready=ready,
                media_artifacts=(),
            )

    def revisions(version_id):
        with repository.transaction() as cursor:
            cursor.execute(
                """SELECT revision_document FROM dataset_registry.dataset_version_episode_revisions
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s
                  AND version_id=%s ORDER BY ordinal""",
                (*ident.scope, ident.dataset_id, version_id),
            )
            return tuple(
                DatasetPageEpisodeRevision.model_validate(row[0]) for row in cursor.fetchall()
            )

    try:
        with scoped(source), repository.transaction() as cursor:
            cursor.execute(
                """INSERT INTO registry.organization_projects
                (organization_id,project_id,display_name) VALUES (%s,%s,'G1 product projection')""",
                (ident.organization_id, ident.project_id),
            )
            cursor.execute(
                """INSERT INTO collection_tasks.collection_tasks
                (organization_id,project_id,collection_task_id,dataset_id,task_code,name,task_type,
                 scenario,description,status,version,create_fingerprint,created_at,updated_at)
                VALUES (%s,%s,%s,%s,
                        lpad(nextval('collection_tasks.task_code_sequence')::text,8,'0'),
                        'G1 product fixture','DATA_CAPTURE','test','','ACTIVE',1,%s,%s,%s)""",
                (
                    ident.organization_id,
                    ident.project_id,
                    task_id,
                    ident.dataset_id,
                    "8" * 64,
                    now,
                    now,
                ),
            )
        with scoped(source):
            first = publish(root, source)
        with ThreadPoolExecutor(max_workers=4) as pool:
            targets = tuple(pool.map(lambda _: project(source, first), range(4)))
        assert len(set(targets)) == 1
        target = targets[0]
        assert target.episode_id == ident.episode_id
        assert target.version_id == first[0].version_id
        output = AlignedBundleCommitActivityOutput(
            version=first[0], derived_ready=first[1], viewer_target=target
        )
        assert (
            AlignedBundleCommitActivityOutput.model_validate_json(output.model_dump_json())
            == output
        )
        assert "lance_version" not in output.model_dump_json()
        with pytest.raises(ValidationError, match="viewer target"):
            AlignedBundleCommitActivityOutput(
                version=first[0],
                derived_ready=first[1],
                viewer_target=target.model_copy(update={"version_id": "version_lance_1"}),
            )
        with scoped(source):
            original = revisions(target.version_id)
        assert len(original) == 1
        assert original[0].content_sha256 == first[1].data.content_hash
        bindings = {s.channel_path: s for s in original[0].streams}
        assert bindings[camera_topics["observation.images.head_depth"]].kind == "DEPTH"
        assert bindings[camera_topics["observation.images.head_rgb"]].kind == "RGB_VIDEO"
        assert bindings["/observation/state"].package_binding.value_kind == "VECTOR"
        assert all(s.package_binding.data == first[1].data for s in original[0].streams)
        assert "lance_version" not in original[0].model_dump_json()

        second_ident = ident.model_copy(
            update={
                "episode_id": "episode_explicit_second",
                "rollout_id": "rollout-explicit-second",
            }
        )
        second_dir = tmp_path / "second"
        second_dir.mkdir()
        second_root, second_source, _, _ = build_rgbd_package(
            second_dir, second_ident, mcap_topics=ingress == "mcap"
        )
        with scoped(second_source):
            second = publish(second_root, second_source)
        with monkeypatch.context() as fault:

            def interrupted(*args, **kwargs):
                raise InterruptedError("synthetic product projection transaction failure")

            fault.setattr(projector.projections, "_put_schema", interrupted)
            with pytest.raises(InterruptedError):
                project(second_source, second)
        with scoped(source):
            assert revisions(second[0].version_id) == ()
            assert revisions(target.version_id) == original
        second_target = project(second_source, second)
        changed_source = source.model_copy(update={"configuration_sha256": "3" * 64})
        with scoped(changed_source):
            third = publish(root, changed_source)
        third_target = project(changed_source, third)
        assert third[0].version == 3 and len(third[0].episodes) == 2
        assert third_target.episode_id == target.episode_id
        assert third_target.revision_id != target.revision_id
        with scoped(source):
            updated = revisions(third_target.version_id)
            assert [r.episode_id for r in updated] == [ident.episode_id, second_ident.episode_id]
            assert updated[0].streams[0].package_binding.data == third[1].data
            assert updated[1].streams[0].package_binding.data == second[1].data
            assert revisions(target.version_id) == original
            assert len(revisions(second_target.version_id)) == 2
            with repository.transaction() as cursor:
                cursor.execute(
                    """SELECT capacity_document->>'source_bytes'
                    FROM dataset_registry.dataset_version_capacity_facts
                    WHERE organization_id=%s AND dataset_id=%s AND version_id=%s""",
                    (ident.organization_id, ident.dataset_id, third_target.version_id),
                )
                assert cursor.fetchone()[0] == ("1000" if ingress == "continuous" else "384")
        assert project(source, first) == target
        forged = third[1].model_copy(update={"data": first[1].data})
        with pytest.raises(DatasetIngestProjectionConflict):
            project(changed_source, (third[0], forged))
    finally:
        service.close()
