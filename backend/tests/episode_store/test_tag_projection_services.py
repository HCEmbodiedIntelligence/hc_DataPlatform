"""Real product SQL for generated Tag children; synthetic RGB-D source only."""

import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from hc_data_platform.alignment.models import AlignmentInputV1
from hc_data_platform.annotation.automation import AutomaticAnnotationRequest
from hc_data_platform.annotation.revision_files import SnapshotBusy
from hc_data_platform.core.context import (
    bind_request_context,
    current_request_context,
    reset_request_context,
)
from hc_data_platform.dataset_registry.ingest_projection import (
    DatasetIngestProjectionConflict,
    PostgresDatasetIngestProjector,
)
from hc_data_platform.dataset_registry.models import (
    DatasetPageEpisodeRecord,
    DatasetPageEpisodeRevision,
)
from hc_data_platform.episode_store.catalog_models import EpisodeDerivedReady
from hc_data_platform.episode_store.derivation import write_tag_episode
from hc_data_platform.episode_store.quality import read_episode_quality
from hc_data_platform.workflow.models import ContinuousEpisodeProjectionSourceV1
from tests.episode_store.test_annotation_bindings_services import actor
from tests.episode_store.test_annotation_revision_files_services import files  # noqa: F401
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_tag_derivation_services import selected  # noqa: F401

pytestmark = pytest.mark.parametrize(
    "prepared_bundle",
    [{"identity": {"dataset_id": "dataset_tag_projection", "episode_id": "episode_tag_parent"}}],
    indirect=True,
)


@pytest.fixture
def projected(selected):  # noqa: F811
    source, packages, freeze = selected
    identity = source.identity
    now = datetime.now(timezone.utc)
    task_id = "capture-" + uuid4().hex
    projector = PostgresDatasetIngestProjector(
        packages.repository.connections, episode_packages=packages
    )
    with scoped(source):
        package, _, plan = freeze("parent")
        collection = packages.collections.get(identity.dataset_id, version=1)
        with packages.repository.transaction() as cursor:
            cursor.execute(
                """INSERT INTO collection_tasks.collection_tasks
                (organization_id,project_id,collection_task_id,dataset_id,task_code,name,task_type,
                 scenario,description,status,version,create_fingerprint,created_at,updated_at)
                VALUES (%s,%s,%s,%s,
                 lpad(nextval('collection_tasks.task_code_sequence')::text,8,'0'),
                 'G1 Tag fixture','DATA_CAPTURE','test','','ACTIVE',1,%s,%s,%s)""",
                (
                    identity.organization_id,
                    identity.project_id,
                    task_id,
                    identity.dataset_id,
                    "8" * 64,
                    now,
                    now,
                ),
            )
        cameras = tuple(
            k
            for k, v in package.document("meta/info.json")["hc.feature_mapping"].items()
            if package.document("meta/info.json")["features"][v]["dtype"] == "video"
        )
        lineage = ContinuousEpisodeProjectionSourceV1(
            organization_id=identity.organization_id,
            project_id=identity.project_id,
            region_code=identity.region_code,
            recording_id="recording-fixture",
            recording_upload_id="upload-fixture",
            episode_id=identity.episode_id,
            rollout_id=identity.rollout_id,
            data_package_id="raw-fixture",
            collection_task_id=task_id,
            robot_id="fixture-robot",
            source_sha256=source.source_sha256,
            manifest_fingerprint="7" * 64,
            source_size_bytes=1000,
            started_at=now,
            ended_at=now + timedelta(seconds=1),
            camera_modality_keys=cameras,
        )
        projector.project_continuous_episode(
            source=lineage,
            alignment=AlignmentInputV1(
                rollout_id=identity.rollout_id,
                source_sha256=source.source_sha256,
                attempt_id="fixture",
                start_ns=0,
                end_ns=133_333_333,
                streams={
                    k: {"kind": "image" if k in cameras else "continuous", "samples": []}
                    for k in plan.schema_snapshot.fields
                },
            ),
            schema_snapshot_id=plan.schema_snapshot.schema_snapshot_id,
            frequency_hz=30,
            version=collection,
            ready=EpisodeDerivedReady(
                data=package.receipt.data,
                collection_version_id=collection.collection_version_id,
                project_id=identity.project_id,
                dataset_id=identity.dataset_id,
                rollout_id=identity.rollout_id,
                source_sha256=source.source_sha256,
                converter_version=source.processor_version,
                dataset_version=collection.version,
                step_count=package.receipt.data.frame_count,
                content_hash=collection.content_hash,
            ),
            media_artifacts=(),
        )
    return source, packages, freeze, projector, collection


def records(packages, source, version):
    with packages.repository.transaction() as cursor:
        cursor.execute(
            """SELECT e.episode_document,r.revision_document
            FROM dataset_registry.dataset_version_episodes e
            JOIN dataset_registry.dataset_version_episode_revisions r
            USING(organization_id,project_id,region_code,dataset_id,version_id,episode_id)
            WHERE e.organization_id=%s AND e.project_id=%s AND e.region_code=%s
              AND e.dataset_id=%s AND e.version_id=%s ORDER BY e.ordinal""",
            (*source.identity.scope, source.identity.dataset_id, version.version_id),
        )
        return tuple(
            (
                DatasetPageEpisodeRecord.model_validate(e),
                DatasetPageEpisodeRevision.model_validate(r),
            )
            for e, r in cursor.fetchall()
        )


@pytest.mark.integration
@pytest.mark.parametrize("tag", ["parent", "child", "tail"])
def test_tag_product_transaction_retry_and_fresh_annotation(
    projected,
    target,
    tmp_path,
    monkeypatch,
    tag,
    files,  # noqa: F811
):
    source, packages, freeze, projector, parent_collection = projected
    with scoped(source):
        revision_number = 1
        if tag == "tail":
            _, task, revision_files, auth = files
            annotation = target[2]
            history = annotation.get_history(task.task_id, auth)
            tags = tuple(
                t.model_copy(update={"start_step": 2}) if t.annotation_id == "child" else t
                for t in history.revisions[1].tags
            )
            annotation.save_draft(
                task.task_id,
                actor(source.identity),
                (),
                tags=tags,
                expected_revision=1,
                if_match=history.task.etag,
                client_mutation_id="tail-selection",
            )
            for _ in range(100):
                state = revision_files.status(task.task_id, 2, auth)
                if state.status == "READY":
                    break
                try:
                    revision_files.generate(
                        task.task_id, 2, state.generation, state.revision_content_hash, auth
                    )
                except SnapshotBusy:
                    time.sleep(0.1)
            assert state.status == "READY"
            revision_number = 2
        package, snapshot, plan = freeze("child" if tag == "tail" else tag, revision_number)
        original = records(packages, source, parent_collection)
        root = tmp_path / "derived"
        write_tag_episode(root, package, snapshot, plan)
        receipt = packages.committer.commit(root, plan.input)
        version, ready = packages._publish_committed(receipt, plan.input, plan.schema_snapshot)

    def project():
        with scoped(source):
            return projector.project_tag_episode(
                plan=plan,
                parent_collection_version_id=parent_collection.collection_version_id,
                version=version,
                ready=ready,
            )

    with monkeypatch.context() as fault:

        def interrupted(*args, **kwargs):
            raise InterruptedError("synthetic projection transaction failure")

        fault.setattr(projector, "_put_schema", interrupted)
        with pytest.raises(InterruptedError):
            project()
    with scoped(source):
        assert records(packages, source, version) == ()
        assert records(packages, source, parent_collection) == original
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = tuple(pool.map(lambda _: project(), range(3)))
    assert len(set(results)) == 1
    assert results[0].episode_id == plan.input.identity.episode_id
    with scoped(source):
        projected_records = records(packages, source, version)
        assert len(projected_records) == 2
        episode, revision = projected_records[1]
        assert episode.change_type == "DERIVED" and episode.review_status is None
        assert episode.review_finding_count == "0" and not episode.has_finding
        assert episode.task == original[0][0].task and episode.robot_id == original[0][0].robot_id
        assert int(revision.started_at_ns) == plan.start_timestamp_ns
        end = plan.next_parent_timestamp_ns or 133_333_333
        assert int(revision.duration_ns) == end - plan.start_timestamp_ns
        if tag == "tail":
            assert math.ceil((end - plan.start_timestamp_ns) * 30 / 10**9) == 3
            assert (
                plan.frame_count == 2
            )  # Exact parent selection overrides rounded duration inference.
        assert {s.kind for s in revision.streams} >= {"RGB_VIDEO", "DEPTH"}
        assert all(
            s.package_binding.data == ready.data and s.package_binding.end_step == plan.frame_count
            for s in revision.streams
        )
        assert "lance_version" not in revision.model_dump_json()
        assert records(packages, source, parent_collection) == original
        assert (
            read_episode_quality(
                packages.files.open(ready.data.identity, ready.data.data_version_id)
            ).status
            == "PASS"
        )
        with packages.repository.transaction() as cursor:
            cursor.execute(
                """SELECT capacity_document->>'source_bytes'
                FROM dataset_registry.dataset_version_capacity_facts
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND dataset_id=%s AND version_id=%s""",
                (*source.identity.scope, source.identity.dataset_id, version.version_id),
            )
            assert cursor.fetchone()[0] == "1000"
        annotation, automatic = target[2:4]
        request = AutomaticAnnotationRequest(
            project_id=ready.project_id,
            region_code=source.identity.region_code,
            dataset_id=ready.dataset_id,
            rollout_id=ready.rollout_id,
            dataset_version=version.version,
            lance_version=None,
            data_ref=ready.data,
            dataset_schema_snapshot_id=ready.data.schema_snapshot_id,
            base_step_count=ready.step_count,
            source_workflow_id="tag:" + plan.input.data_version_id,
        )
        child = automatic.ensure_task(request)
        assert automatic.ensure_task(request) == child
        assert child.task_id != plan.annotation.binding.task_id
        assert child.data_ref == ready.data and child.current_revision == 0
        assert child.approved_review_id is None and child.current_submission_id is None
        assert (
            annotation.get_history(
                plan.annotation.binding.task_id, actor(source.identity)
            ).task.current_revision
            == plan.annotation.binding.revision
        )
        evidence = Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", str(tmp_path))) / "tag-projections"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / (ready.data.data_version_id + ".json")).write_text(
            json.dumps(
                {
                    "parent": plan.parent.model_dump(mode="json"),
                    "child": ready.data.model_dump(mode="json"),
                    "target": results[0].model_dump(mode="json"),
                    "tag": plan.annotation_id,
                    "annotation_revision": plan.annotation.binding.revision,
                    "frames": [plan.start_step, plan.end_step],
                    "window_ns": [plan.start_timestamp_ns, end],
                    "rounded_duration_frame_count": math.ceil(
                        (end - plan.start_timestamp_ns) * 30 / 10**9
                    ),
                    "transaction_rollback_and_concurrent_retry": True,
                    "parent_product_and_annotation_unchanged": True,
                    "child_annotation_task": child.task_id,
                    "child_annotation_revision": child.current_revision,
                    "inherited_approvals": False,
                    "raw_bytes_before_and_after": [1000, 1000],
                    "child_quality": "PASS",
                    "synthetic_input": True,
                    "http_queue_ui_verified": False,
                    "human_acceptance": False,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )


@pytest.mark.integration
def test_tag_projection_rejects_wrong_scope_and_commit(projected, tmp_path):
    source, packages, freeze, projector, parent_collection = projected
    with scoped(source):
        package, snapshot, plan = freeze("parent")
        root = tmp_path / "derived"
        write_tag_episode(root, package, snapshot, plan)
        receipt = packages.committer.commit(root, plan.input)
        version, ready = packages._publish_committed(receipt, plan.input, plan.schema_snapshot)
        args = dict(
            plan=plan,
            parent_collection_version_id=parent_collection.collection_version_id,
            version=version,
            ready=ready,
        )
        for field, value in (
            ("ready", ready.model_copy(update={"content_hash": "0" * 64})),
            ("ready", ready.model_copy(update={"source_sha256": "0" * 64})),
            ("plan", plan.model_copy(update={"display_name": "forged"})),
        ):
            with pytest.raises((DatasetIngestProjectionConflict, KeyError)):
                projector.project_tag_episode(**{**args, field: value})
        for updates in (
            {"organization_id": "other-tenant"},
            {"project_id": "other-project"},
            {"region_code": "other-region"},
            {"service_identity": False},
        ):
            token = bind_request_context(replace(current_request_context(), **updates))
            try:
                with pytest.raises(PermissionError):
                    projector.project_tag_episode(**args)
            finally:
                reset_request_context(token)
        assert records(packages, source, version) == ()
