"""Real PG/MinIO RGB-D annotation lifecycle; synthetic input, not G1 acceptance."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from pydantic import ValidationError

from hc_data_platform.annotation import AnnotationActor, AnnotationService, ReviewDecision
from hc_data_platform.annotation.automation import (
    AutomaticAnnotationBlocked,
    AutomaticAnnotationRequest,
    AutomaticAnnotationTaskService,
    PostgresAutomaticAnnotationRepository,
)
from hc_data_platform.annotation.models import (
    AnnotationRevision,
    AnnotationTag,
    TagSchemaDocument,
    TagSchemaTarget,
)
from hc_data_platform.annotation.postgres import PostgresAnnotationRepository
from hc_data_platform.annotation.validation import revision_content_hash
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot, StepRecord
from hc_data_platform.publishing.adapters import ApprovedAnnotationSnapshotAdapter
from hc_data_platform.workflow.models import AutomaticAnnotationActivityInput
from tests.episode_store.test_commit_services import scoped


def actor(ident, name="editor"):
    return AnnotationActor(
        actor_id=name,
        project_ids=frozenset({ident.project_id}),
        capabilities=frozenset(
            {
                "data_schema.publish",
                "annotation_task.read",
                "annotation_task.claim",
                "annotation.edit",
                "annotation.save",
                "annotation.submit",
                "annotation.review",
            }
        ),
    )


@pytest.fixture
def target(prepared_bundle, request):
    packages, source, bundle, assets, _ = prepared_bundle
    ident = source.identity
    rows = tuple(
        StepRecord(
            rollout_id=ident.rollout_id,
            step_index=i,
            timestamp_ns=i * 33_333_333,
            modalities={
                "/observation/state": [float(i)],
                "action": [float(i)],
                **{key: {"frame_index": i} for key in assets.videos},
            },
            source_timestamps_ns={key: (i * 33_333_333,) for key in assets.videos},
            time_error_ns={key: 0 for key in assets.videos},
            valid={key: True for key in ("/observation/state", "action", *assets.videos)},
        )
        for i in range(4)
    )
    schema = DatasetSchemaSnapshot.create(
        project_id=ident.project_id,
        dataset_id=ident.dataset_id,
        schema_snapshot_id=source.schema_snapshot_id,
        frequency_hz=30,
        fields={
            "/observation/state": "list<float64>",
            "action": "list<float64>",
            **{key: "json" for key in assets.videos},
        },
    )
    with scoped(source):
        collection, ready = packages.write_from_aligned_bundle(
            source,
            rows,
            schema=schema,
            metadata={
                "task": "annotation RGB-D binding",
                **getattr(request, "param", {}).get("metadata", {}),
            },
            **bundle,
        )
    repository = PostgresAnnotationRepository(packages.repository.connections)
    service = AnnotationService(repository)
    automatic = AutomaticAnnotationTaskService(
        PostgresAutomaticAnnotationRepository(packages.repository.connections)
    )
    request = AutomaticAnnotationRequest(
        project_id=ident.project_id,
        region_code=ident.region_code,
        dataset_id=ident.dataset_id,
        rollout_id=ident.rollout_id,
        dataset_version=collection.version,
        lance_version=None,
        data_ref=ready.data,
        dataset_schema_snapshot_id=schema.schema_snapshot_id,
        base_step_count=4,
        source_workflow_id="annotation-service-evidence/" + uuid4().hex,
    )
    return packages, source, service, automatic, request


def publish_schema(source, service):
    ident = source.identity
    schema = service.create_tag_schema_version(
        project_id=ident.project_id,
        name="manual hierarchy",
        document=TagSchemaDocument(nodes=()),
        compatible_targets=(
            TagSchemaTarget(
                region_code=ident.region_code,
                dataset_id=ident.dataset_id,
                dataset_schema_snapshot_id=source.schema_snapshot_id,
            ),
        ),
        actor=actor(ident),
    )
    return service.publish_tag_schema_version(
        project_id=ident.project_id,
        schema_id=schema.schema_id,
        version=schema.version,
        actor=actor(ident),
    )


@pytest.mark.integration
def test_rgbd_automatic_task_retry_concurrency_edit_submit_review_and_restore(target, monkeypatch):
    packages, source, service, automatic, request = target
    with scoped(source):
        with pytest.raises(AutomaticAnnotationBlocked):
            automatic.ensure_task(request)
        publish_schema(source, service)
        # Durable write succeeds, response disappears. The same deterministic task recovers.
        create = automatic._repository.create_automatic

        def lost_response(*args):
            create(*args)
            raise InterruptedError("lost task response")

        monkeypatch.setattr(automatic._repository, "create_automatic", lost_response)
        with pytest.raises(InterruptedError):
            automatic.ensure_task(request)
        monkeypatch.setattr(automatic._repository, "create_automatic", create)

    def retry(_):
        with scoped(source):
            return automatic.ensure_task(request)

    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = list(pool.map(retry, range(4)))
    assert all(t == tasks[0] for t in tasks)
    task = tasks[0]
    assert task.base_lance_version is None and task.data_ref == request.data_ref
    assert task.creation_source.value == "SYSTEM_EPISODE"
    editor, reviewer = actor(source.identity), actor(source.identity, "reviewer")
    with scoped(source):
        claimed = service.claim(task.task_id, editor)
        parent = AnnotationTag(
            annotation_id="parent",
            tag_id="manual-parent",
            path=("manual-parent",),
            label="动作",
            start_step=0,
            end_step=2,
        )
        child = AnnotationTag(
            annotation_id="child",
            tag_id="manual-child",
            path=("manual-parent", "manual-child"),
            parent_annotation_id="parent",
            label="跨父区间",
            start_step=1,
            end_step=4,
        )
        saved = service.save_draft(
            task.task_id,
            editor,
            (),
            tags=(parent, child),
            expected_revision=0,
            if_match=claimed.etag,
            client_mutation_id="save-1",
        )
        assert (
            service.save_draft(
                task.task_id,
                editor,
                (),
                tags=(parent, child),
                expected_revision=0,
                if_match=claimed.etag,
                client_mutation_id="save-1",
            )
            == saved
        )
        assert service.get_draft(task.task_id, editor).data_ref == request.data_ref

        def submit(_):
            with scoped(source):
                return service.submit_for_review(
                    task.task_id,
                    editor,
                    expected_revision=1,
                    if_match=service.get_task(task.task_id).etag,
                    idempotency_key="submission-1",
                )

        with ThreadPoolExecutor(max_workers=4) as pool:
            submissions = list(pool.map(submit, range(4)))
        assert all(s == submissions[0] for s in submissions)
        submission = submissions[0]
        assert submission.base_lance_version is None and submission.data_ref == request.data_ref
        approval = service.review(
            task.task_id,
            reviewer,
            ReviewDecision.NEEDS_REVISION,
            revision=1,
            submission_id=submission.submission_id,
            if_match=service.get_task(task.task_id).etag,
        )
        assert approval is None
        history = service.get_history(task.task_id, editor)
        assert history.reviews[0].data_ref == request.data_ref
        assert history.revisions[1].tags == (parent, child)
        # New edits never change the fixed submission/review or the primary package.
        service.restore_revision(
            task.task_id,
            editor,
            target_revision=0,
            expected_revision=1,
            if_match=service.get_task(task.task_id).etag,
            client_mutation_id="restore-0",
        )
        updated = service.get_history(task.task_id, editor)
        assert updated.reviews == history.reviews and updated.submissions == history.submissions
        assert updated.revisions[:2] == history.revisions
        assert updated.revisions[-1].data_ref == request.data_ref
        submitted_again = service.submit_for_review(
            task.task_id,
            editor,
            expected_revision=2,
            if_match=service.get_task(task.task_id).etag,
            idempotency_key="submit-restored",
        )
        approval = service.review(
            task.task_id,
            reviewer,
            ReviewDecision.APPROVE,
            revision=2,
            submission_id=submitted_again.submission_id,
            if_match=service.get_task(task.task_id).etag,
        )
        assert approval.data_ref == request.data_ref and approval.base_lance_version is None
        with pytest.raises(ProblemException) as denied_export:
            ApprovedAnnotationSnapshotAdapter(annotations=service).get_approved_revision(
                project_id=source.identity.project_id,
                dataset_id=source.identity.dataset_id,
                rollout_id=source.identity.rollout_id,
                lance_version=str(request.dataset_version),
            )
        assert denied_export.value.problem.code == "ANNOTATION_DATA_BINDING_MISMATCH"
        assert (
            packages.repository.get(source.identity, request.data_ref.data_version_id).data
            == request.data_ref
        )
        with packages.repository.transaction() as cursor:
            cursor.execute(
                "SELECT count(*), min(attempts) FROM annotation.annotation_task_triggers "
                "WHERE trigger_id=%s",
                (request.trigger_id,),
            )
            assert cursor.fetchone() == (1, 6)  # blocked + lost response + four retries
            cursor.execute(
                "SELECT count(*) FROM lance_dataset_versions "
                "WHERE organization_id=%s AND project_id=%s",
                (source.identity.organization_id, source.identity.project_id),
            )
            assert cursor.fetchone()[0] == 0


@pytest.mark.integration
def test_exact_receipt_collection_scope_and_immutable_child_guards(target):
    packages, source, service, automatic, request = target
    with scoped(source):
        publish_schema(source, service)
        for changes in (
            {"content_hash": "f" * 64},
            {"package_prefix": "another/prefix"},
            {"version": 99},
        ):
            forged = request.data_ref.model_copy(update=changes)
            with pytest.raises(psycopg.errors.CheckViolation, match="exact collection"):
                automatic.ensure_task(replace(request, data_ref=forged))
        with pytest.raises(psycopg.errors.CheckViolation, match="exact collection"):
            automatic.ensure_task(replace(request, dataset_version=999))
        task = automatic.ensure_task(request)
        with (
            pytest.raises(psycopg.errors.CheckViolation, match="immutable"),
            packages.repository.transaction() as cursor,
        ):
            cursor.execute(
                "UPDATE annotation.annotation_tasks "
                "SET data_ref=jsonb_set(data_ref,'{content_hash}',%s::jsonb) WHERE task_id=%s",
                (json.dumps("e" * 64), task.task_id),
            )
        initial = service.get_revision(task.task_id, 0)
        altered = initial.model_copy(
            update={
                "revision": 1,
                "parent_revision": 0,
                "client_mutation_id": "forged",
                "data_ref": request.data_ref.model_copy(update={"content_hash": "e" * 64}),
            }
        )
        with (
            pytest.raises(psycopg.errors.CheckViolation, match="child data binding mismatch"),
            packages.repository.transaction() as cursor,
        ):
            PostgresAnnotationRepository._insert_revision(cursor, altered)
        for changes in (
            {"organization_id": "wrong-org"},
            {"region_code": "wrong-region"},
            {"project_id": "wrong-project"},
        ):
            wrong = source.model_copy(
                update={"identity": source.identity.model_copy(update=changes)}
            )
            with scoped(wrong):
                assert service._repository.get(task.task_id) is None
                with pytest.raises((PermissionError, psycopg.errors.InsufficientPrivilege)):
                    automatic.ensure_task(request)
    with pytest.raises(ValueError, match="exactly one"):
        replace(request, lance_version=1)
    with pytest.raises(ValidationError, match="Episode binding"):
        AutomaticAnnotationActivityInput(
            organization_id="wrong",
            project_id=request.project_id,
            region_code=request.region_code,
            rollout_id=request.rollout_id,
            dataset_id=request.dataset_id,
            dataset_version=request.dataset_version,
            lance_version=None,
            data_ref=request.data_ref,
            dataset_schema_snapshot_id=request.dataset_schema_snapshot_id,
            base_step_count=4,
            source_workflow_id=request.source_workflow_id,
        )


def test_legacy_hash_is_byte_compatible_and_missing_binding_is_rejected():
    values = {
        "base_lance_version": 7,
        "tag_schema_id": "schema",
        "tag_schema_version": 1,
        "tags": [],
        "operations": [],
    }
    expected = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert revision_content_hash(**values) == expected
    with pytest.raises(ValueError, match="exactly one"):
        revision_content_hash(**{**values, "base_lance_version": None})
    with pytest.raises(ValidationError, match="exactly one"):
        AnnotationRevision(
            task_id="invalid",
            revision=0,
            parent_revision=None,
            author_id="a",
            client_mutation_id="initial",
            base_lance_version=None,
            operations=(),
            content_hash=expected,
        )
