"""Actual isolated DB/MinIO, RGB-D batch and annotation lifecycle; synthetic data."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import httpx
import psycopg
import pytest

from hc_data_platform.annotation import AnnotationService, ReviewDecision
from hc_data_platform.annotation.models import AnnotationOperation, AnnotationTag, OperationKind
from hc_data_platform.annotation.postgres import PostgresAnnotationRepository
from hc_data_platform.annotation.service import AnnotationNotFoundError, normalize_operations
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.episode_store.detail import read_detail
from hc_data_platform.episode_store.migration_annotations import (
    MigrationAnnotations,
    read_annotation_migration,
    source_snapshot,
)
from hc_data_platform.episode_store.models import digest
from hc_data_platform.episode_store.postgres import PackageConflict
from hc_data_platform.episode_store.service import EpisodePackageService
from tests.episode_store.test_annotation_bindings_services import actor, publish_schema
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_detail_services import auth_for
from tests.episode_store.test_http_services import register_and_grant
from tests.episode_store.test_migration_batches_services import batch_source  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def annotation_source(batch_source):  # noqa: F811 -- imported pytest fixture
    batches, plans, roots, _, _ = batch_source
    plan = plans[0]
    ident, dataset = plan.legacy.identity, plan.legacy.identity.dataset_id
    annotations = AnnotationService(PostgresAnnotationRepository(batches.packages.connections))
    with scoped(plan.source):
        schema = publish_schema(plan.source, annotations)
        task = annotations.create_task(
            task_id="old-task-" + uuid4().hex,
            project_id=ident.project_id,
            region_code=ident.region_code,
            dataset_id=dataset,
            rollout_id=ident.rollout_id,
            dataset_version=plan.legacy.dataset_version,
            base_lance_version=plan.legacy.dataset_version,
            base_step_count=4,
            source_workflow_id="original-workflow-" + uuid4().hex,
            tag_schema_id=schema.schema_id,
            tag_schema_version=schema.version,
        )
        editor = actor(ident)
        claimed = annotations.claim(task.task_id, editor)
        tags = (
            AnnotationTag(
                annotation_id="parent",
                tag_id="manual-parent",
                path=("manual-parent",),
                label="父区间",
                start_step=0,
                end_step=3,
            ),
            AnnotationTag(
                annotation_id="child",
                tag_id="manual-child",
                path=("manual-parent", "manual-child"),
                label="交叉子区间",
                parent_annotation_id="parent",
                start_step=1,
                end_step=4,
            ),
        )
        operations = (
            AnnotationOperation(
                operation_id="old-exclude", kind=OperationKind.EXCLUDE, start_step=0, end_step=2
            ),
            AnnotationOperation(
                operation_id="old-restore", kind=OperationKind.RESTORE, start_step=1, end_step=2
            ),
        )
        annotations.save_draft(
            task.task_id,
            editor,
            operations,
            tags=tags,
            expected_revision=0,
            if_match=claimed.etag,
            client_mutation_id="source-save",
        )
        submission = annotations.submit_for_review(
            task.task_id,
            editor,
            expected_revision=1,
            if_match=annotations.get_task(task.task_id).etag,
            idempotency_key="source-submit",
        )
        annotations.review(
            task.task_id,
            actor(ident, "old-reviewer"),
            ReviewDecision.APPROVE,
            revision=1,
            submission_id=submission.submission_id,
            if_match=annotations.get_task(task.task_id).etag,
        )
        prepared = batches.prepare(dataset, "annotation-batch", plans, roots)
        batch = batches.publish(dataset, prepared["batch_id"], roots)
        with batches.packages.transaction() as cursor:
            snapshot = source_snapshot(cursor, (*ident.scope, dataset), task.task_id)
    return batches, plan, annotations, task.task_id, batch, snapshot


def prepare(source):
    batches, plan, _, task_id, batch, _ = source
    return MigrationAnnotations(batches).prepare(
        plan.legacy.identity.dataset_id, batch["batch_id"], task_id, 1, plan.legacy
    )


def original_unchanged(source):
    batches, plan, _, task_id, _, snapshot = source
    with batches.packages.transaction() as cursor:
        assert (
            source_snapshot(
                cursor, (*plan.legacy.identity.scope, plan.legacy.identity.dataset_id), task_id
            )
            == snapshot
        )


def test_complete_mapping_copies_tags_exclusions_but_keeps_review_independent(annotation_source):
    batches, plan, annotations, task_id, _, snapshot = annotation_source
    dataset = plan.legacy.identity.dataset_id
    with scoped(plan.source):
        service = MigrationAnnotations(batches)
        prepared = prepare(annotation_source)
        assert prepared["status"] == "PREPARED"
        assert prepared["plan"]["source_snapshot_sha256"] == digest(snapshot)
        assert prepare(annotation_source) == prepared
        applied = service.apply(dataset, prepared["mapping_id"])
        assert service.apply(dataset, prepared["mapping_id"]) == applied
        assert prepare(annotation_source) == applied
        original_unchanged(annotation_source)
        task = annotations.get_task(applied["plan"]["target_task_id"])
        assert task.status.value == "DRAFT" and task.current_revision == 1
        assert (
            task.base_lance_version is None
            and task.data_ref.data_version_id == plan.source.data_version_id
        )
        assert task.current_submission_id is None and task.approved_review_id is None
        assert task.assignee_id is None
        old = annotations.get_history(task_id, actor(plan.legacy.identity))
        new = annotations.get_history(task.task_id, actor(plan.legacy.identity))
        assert new.revisions[1].tags == old.revisions[1].tags
        assert normalize_operations(new.revisions, 1) == normalize_operations(old.revisions, 1)
        assert new.revisions[1].content_hash != old.revisions[1].content_hash
        assert not new.reviews and not new.submissions
        body, checksum = read_annotation_migration(
            batches.packages, batches.storage, task.data_ref, prepared["mapping_id"]
        )
        document = json.loads(body)
        assert digest(document) == checksum == applied["document_sha256"]
        assert document["plan"]["source_snapshot"] == snapshot
        assert document["application"]["review_automatically_transferred"] is False
        with batches.packages.transaction() as cursor:
            cursor.execute(
                "SELECT revision,status FROM annotation.revision_files "
                "WHERE task_id=%s ORDER BY revision",
                (task.task_id,),
            )
            assert cursor.fetchall() == [(0, "PENDING"), (1, "PENDING")]


def test_overview_separates_historical_mapping_from_current_review(annotation_source, tmp_path):
    batches, plan, _, task_id, _, _ = annotation_source
    ident = plan.legacy.identity
    service = EpisodePackageService(
        batches.packages.connections, batches.storage, tmp_path / "view"
    )
    try:
        with scoped(plan.source):
            prepared = prepare(annotation_source)
            applied = MigrationAnnotations(batches).apply(ident.dataset_id, prepared["mapping_id"])
            data = service.repository.get(ident, plan.source.data_version_id).data
            package = service.files.open(ident, data.data_version_id)
            view = read_detail(service, package, auth_for(data))
            assert view.annotation.status == "DRAFT"
            assert view.annotation.task_id == applied["plan"]["target_task_id"]
            assert view.annotation.latest_submission is view.annotation.latest_approval is None
            assert view.annotation.current_approved_review_id is None
            (mapping,) = view.annotation.historical_migrations
            assert mapping.source_task_id == task_id and mapping.source_revision == 1
            assert mapping.source_data == plan.legacy and mapping.source_status == "APPROVED"
            assert mapping.source_submission_count == mapping.source_review_count == 1
            assert (
                mapping.target_revision_content_hash
                == view.annotation.current_revision.content_hash
            )
            assert mapping.download.allowed and not mapping.review_automatically_transferred
            hidden = read_detail(service, package, auth_for(data, annotations=False))
            assert hidden.annotation.availability == "FORBIDDEN"
            assert not hidden.annotation.historical_migrations
            without_export = replace(
                auth_for(data),
                capabilities=frozenset({"dataset_version.read", "annotation_task.read"}),
            )
            assert (
                not read_detail(service, package, without_export)
                .annotation.historical_migrations[0]
                .download.allowed
            )
            original_unchanged(annotation_source)
    finally:
        service.close()


def test_mapping_json_route_uses_real_session_exact_data_and_both_capabilities(annotation_source):
    batches, plan, _, _, _, _ = annotation_source
    ident = plan.legacy.identity
    with scoped(plan.source):
        prepared = prepare(annotation_source)
        applied = MigrationAnnotations(batches).apply(ident.dataset_id, prepared["mapping_id"])
        data = batches.packages.get(ident, plan.source.data_version_id).data
        with httpx.Client(base_url="http://gateway:8080", timeout=45, trust_env=False) as client:
            principal, headers = register_and_grant(
                client,
                batches.packages,
                ident,
                capabilities=["dataset_version.read", "annotation_task.read", "export.download"],
            )
            base = (
                f"/api/v1/projects/{ident.project_id}/regions/{ident.region_code}/datasets/"
                f"{ident.dataset_id}/episodes/{ident.episode_id}/data-versions/{data.data_version_id}"
            )
            path = base + "/annotation-migrations/" + prepared["mapping_id"]
            response = client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            assert len(response.content) == applied["document_size"]
            assert response.headers["x-hc-sha256"] == applied["document_sha256"]
            assert response.headers["cache-control"] == "private, no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["content-disposition"].startswith("attachment;")
            assert response.json()["plan"] == applied["plan"]
            assert client.get(path).status_code == 401
            assert (
                client.get(path, headers={**headers, "X-Organization-Id": "foreign"}).status_code
                == 403
            )
            assert (
                client.get(
                    base + "/annotation-migrations/am_" + "0" * 64, headers=headers
                ).status_code
                == 404
            )
            view = client.get(base + "/overview", headers=headers)
            assert view.status_code == 200, view.text
            assert (
                view.json()["annotation"]["historical_migrations"][0]["mapping_id"]
                == prepared["mapping_id"]
            )
            for capability in ("export.download", "annotation_task.read"):
                with batches.packages.transaction() as cursor:
                    cursor.execute(
                        "UPDATE access_control.capability_grants SET active=false "
                        "WHERE principal_id=%s AND capability_key=%s",
                        (principal, capability),
                    )
                assert client.get(path, headers=headers).status_code == 403
                view = client.get(base + "/overview", headers=headers)
                assert view.status_code == 200, view.text
                annotation = view.json()["annotation"]
                if capability == "export.download":
                    assert not annotation["historical_migrations"][0]["download"]["allowed"]
                else:
                    assert annotation["availability"] == "FORBIDDEN"
                    assert not annotation["historical_migrations"]
            assert client.post("/api/v1/auth/session:logout", headers=headers).status_code == 204
        original_unchanged(annotation_source)


def test_mapping_download_refuses_missing_or_same_size_corrupt_bytes(
    annotation_source, monkeypatch
):
    batches, plan, _, _, _, _ = annotation_source
    with scoped(plan.source):
        prepared = prepare(annotation_source)
        applied = MigrationAnnotations(batches).apply(
            plan.legacy.identity.dataset_id, prepared["mapping_id"]
        )
        data = batches.packages.get(plan.legacy.identity, plan.source.data_version_id).data
        original_head = batches.storage.head
        monkeypatch.setattr(batches.storage, "head", lambda key: None)
        with pytest.raises(PackageConflict, match="missing"):
            read_annotation_migration(
                batches.packages, batches.storage, data, prepared["mapping_id"]
            )
        monkeypatch.setattr(batches.storage, "head", original_head)
        monkeypatch.setattr(
            batches.storage, "read_chunks", lambda key: iter([b"x" * applied["document_size"]])
        )
        with pytest.raises(PackageConflict, match="hash"):
            read_annotation_migration(
                batches.packages, batches.storage, data, prepared["mapping_id"]
            )


@pytest.mark.parametrize(
    "stage",
    [
        "annotation_mapping_object_verified",
        "annotation_mapping_draft_written",
        "annotation_mapping_database_committed",
    ],
)
def test_interruption_and_lost_response_resume_same_mapping_and_draft(annotation_source, stage):
    batches, plan, annotations, _, _, _ = annotation_source
    dataset = plan.legacy.identity.dataset_id
    with scoped(plan.source):
        prepared = prepare(annotation_source)

        def interrupt(point):
            if point == stage:
                raise InterruptedError(point)

        with pytest.raises(InterruptedError):
            MigrationAnnotations(batches, checkpoint=interrupt).apply(
                dataset, prepared["mapping_id"]
            )
        status = MigrationAnnotations(batches).get(dataset, prepared["mapping_id"])
        assert (status["status"] == "APPLIED") == (stage == "annotation_mapping_database_committed")
        applied = MigrationAnnotations(batches).apply(dataset, prepared["mapping_id"])
        assert applied["status"] == "APPLIED"
        assert annotations.get_task(applied["plan"]["target_task_id"]).status.value == "DRAFT"
        original_unchanged(annotation_source)


def test_concurrent_apply_creates_one_task_two_revisions_and_one_receipt(annotation_source):
    batches, plan, _, _, _, _ = annotation_source
    dataset = plan.legacy.identity.dataset_id
    with scoped(plan.source):
        prepared = prepare(annotation_source)

    def apply(_):
        with scoped(plan.source):
            return MigrationAnnotations(batches).apply(dataset, prepared["mapping_id"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(apply, range(4)))
    assert all(r == results[0] for r in results)
    with scoped(plan.source), batches.packages.transaction() as cursor:
        cursor.execute(
            "SELECT count(*) FROM annotation.annotation_tasks WHERE task_id=%s",
            (prepared["plan"]["target_task_id"],),
        )
        assert cursor.fetchone()[0] == 1
        cursor.execute(
            "SELECT count(*) FROM annotation.annotation_revisions WHERE task_id=%s",
            (prepared["plan"]["target_task_id"],),
        )
        assert cursor.fetchone()[0] == 2
        original_unchanged(annotation_source)


def test_new_task_uses_normal_edit_submit_review_and_old_history_stays_frozen(annotation_source):
    batches, plan, annotations, _, _, _ = annotation_source
    ident = plan.legacy.identity
    with scoped(plan.source):
        prepared = prepare(annotation_source)
        applied = MigrationAnnotations(batches).apply(ident.dataset_id, prepared["mapping_id"])
        task_id = applied["plan"]["target_task_id"]
        editor = actor(ident, "new-editor")
        claimed = annotations.claim(task_id, editor)
        tags = annotations.get_history(task_id, editor).revisions[1].tags
        edited = annotations.save_draft(
            task_id,
            editor,
            (),
            tags=tags,
            expected_revision=1,
            if_match=claimed.etag,
            client_mutation_id="new-save",
        )
        assert (
            edited.revision == 2 and edited.data_ref.data_version_id == plan.source.data_version_id
        )
        submission = annotations.submit_for_review(
            task_id,
            editor,
            expected_revision=2,
            if_match=annotations.get_task(task_id).etag,
            idempotency_key="new-submit",
        )
        annotations.review(
            task_id,
            actor(ident, "new-reviewer"),
            ReviewDecision.APPROVE,
            revision=2,
            submission_id=submission.submission_id,
            if_match=annotations.get_task(task_id).etag,
        )
        assert annotations.get_task(task_id).status.value == "APPROVED"
        assert (
            MigrationAnnotations(batches).apply(ident.dataset_id, prepared["mapping_id"]) == applied
        )
        original_unchanged(annotation_source)


def test_changed_original_annotation_rejects_stale_plan_without_new_draft(annotation_source):
    batches, plan, annotations, old_task, _, _ = annotation_source
    with scoped(plan.source):
        prepared = prepare(annotation_source)
        annotations.save_draft(
            old_task,
            actor(plan.legacy.identity),
            (),
            tags=(),
            expected_revision=1,
            if_match=annotations.get_task(old_task).etag,
            client_mutation_id="changed-old",
        )
        with pytest.raises(PackageConflict, match="history changed"):
            MigrationAnnotations(batches).apply(
                plan.legacy.identity.dataset_id, prepared["mapping_id"]
            )
        with pytest.raises(AnnotationNotFoundError):
            annotations.get_task(prepared["plan"]["target_task_id"])


@pytest.mark.parametrize("change", ["hash", "physical_version", "frame_count"])
def test_source_data_equivalence_is_verified_not_assumed(annotation_source, change):
    batches, plan, _, task_id, batch, _ = annotation_source
    changed = plan.legacy.model_copy(
        update={
            "hash": {"content_hash": "f" * 64},
            "physical_version": {"lance_version": 999999},
            "frame_count": {"frame_count": 5},
        }[change]
    )
    with scoped(plan.source), pytest.raises(PackageConflict):
        MigrationAnnotations(batches).prepare(
            plan.legacy.identity.dataset_id, batch["batch_id"], task_id, 1, changed
        )


@pytest.mark.parametrize("service_identity", [False, True])
def test_operator_identity_and_full_tenant_scope_are_required(annotation_source, service_identity):
    batches, plan, _, task_id, batch, _ = annotation_source
    ident = plan.legacy.identity
    token = bind_request_context(
        RequestContext(
            organization_id=ident.organization_id if not service_identity else "other-tenant",
            project_id=ident.project_id,
            region_code=ident.region_code,
            subject_id="bad-scope",
            service_identity=service_identity,
        )
    )
    try:
        with pytest.raises(PermissionError):
            MigrationAnnotations(batches).prepare(
                ident.dataset_id, batch["batch_id"], task_id, 1, plan.legacy
            )
    finally:
        reset_request_context(token)


def test_rollback_retains_mapping_and_archive_draft_without_reactivation(annotation_source):
    batches, plan, annotations, _, batch, _ = annotation_source
    dataset = plan.legacy.identity.dataset_id
    with scoped(plan.source):
        batches.rollback(dataset, batch["batch_id"])
        prepared = prepare(annotation_source)
        applied = MigrationAnnotations(batches).apply(dataset, prepared["mapping_id"])
        assert (
            annotations.get_task(applied["plan"]["target_task_id"]).data_ref.data_version_id
            == plan.source.data_version_id
        )
        assert batches.collections.current(dataset) is None
        assert batches.packages.current(plan.legacy.identity) is None
        original_unchanged(annotation_source)


def test_mapping_json_cannot_be_read_for_another_data_version(annotation_source):
    batches, plan, annotations, _, _, _ = annotation_source
    with scoped(plan.source):
        prepared = prepare(annotation_source)
        applied = MigrationAnnotations(batches).apply(
            plan.legacy.identity.dataset_id, prepared["mapping_id"]
        )
        data = annotations.get_task(applied["plan"]["target_task_id"]).data_ref
        with pytest.raises(KeyError):
            read_annotation_migration(
                batches.packages,
                batches.storage,
                data.model_copy(update={"data_version_id": "dv_" + "f" * 64}),
                prepared["mapping_id"],
            )
        token = bind_request_context(
            RequestContext(
                organization_id="other",
                project_id=data.identity.project_id,
                region_code=data.identity.region_code,
                subject_id="reader",
            )
        )
        try:
            with pytest.raises(PermissionError):
                read_annotation_migration(
                    batches.packages, batches.storage, data, prepared["mapping_id"]
                )
        finally:
            reset_request_context(token)


def test_original_later_revision_hash_uses_full_operation_history(annotation_source):
    batches, plan, annotations, old_task, batch, _ = annotation_source
    ident = plan.legacy.identity
    with scoped(plan.source):
        editor = actor(ident)
        tags = annotations.get_history(old_task, editor).revisions[1].tags
        annotations.save_draft(
            old_task,
            editor,
            (
                AnnotationOperation(
                    operation_id="restore-later",
                    kind=OperationKind.RESTORE,
                    start_step=0,
                    end_step=1,
                ),
            ),
            tags=tags,
            expected_revision=1,
            if_match=annotations.get_task(old_task).etag,
            client_mutation_id="later-save",
        )
        with batches.packages.transaction() as cursor:
            before = source_snapshot(cursor, (*ident.scope, ident.dataset_id), old_task)
        service = MigrationAnnotations(batches)
        prepared = service.prepare(ident.dataset_id, batch["batch_id"], old_task, 2, plan.legacy)
        applied = service.apply(ident.dataset_id, prepared["mapping_id"])
        history = annotations.get_history(applied["plan"]["target_task_id"], editor)
        assert history.revisions[1].tags == tags
        assert not normalize_operations(history.revisions, 1)
        with batches.packages.transaction() as cursor:
            assert source_snapshot(cursor, (*ident.scope, ident.dataset_id), old_task) == before


@pytest.mark.parametrize("mutation", ["plan", "result", "delete"])
def test_database_mapping_guards_preserve_immutable_receipt(annotation_source, mutation):
    batches, plan, _, _, _, _ = annotation_source
    with scoped(plan.source):
        prepared = prepare(annotation_source)
        MigrationAnnotations(batches).apply(plan.legacy.identity.dataset_id, prepared["mapping_id"])
        with pytest.raises(psycopg.errors.CheckViolation), batches.packages.transaction() as cursor:
            if mutation == "delete":
                cursor.execute(
                    "DELETE FROM episode_store.annotation_migrations WHERE mapping_id=%s",
                    (prepared["mapping_id"],),
                )
            else:
                cursor.execute(
                    f"UPDATE episode_store.annotation_migrations SET {mutation}='{{}}'::jsonb "
                    "WHERE mapping_id=%s",
                    (prepared["mapping_id"],),
                )
