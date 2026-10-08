"""Actual PG/MinIO mixed collection, independent approval and atomic business release."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import psycopg
import pytest

from hc_data_platform.annotation import ReviewDecision
from hc_data_platform.core.context import (
    bind_request_context,
    current_request_context,
    reset_request_context,
)
from hc_data_platform.dataset_registry.migration_projection import read_projection
from hc_data_platform.episode_store.migration_annotations import MigrationAnnotations
from hc_data_platform.episode_store.models import digest
from hc_data_platform.episode_store.postgres import PackageConflict
from hc_data_platform.episode_store.release_models import PublishCollectionRelease
from hc_data_platform.episode_store.releases import CollectionReleases
from hc_data_platform.episode_store.service import EpisodePackageService
from tests.episode_store.test_annotation_bindings_services import actor
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_http_services import register_and_grant
from tests.episode_store.test_migration_annotations_services import (
    annotation_source,  # noqa: F401
    original_unchanged,
    prepare,
)  # noqa: F401
from tests.episode_store.test_migration_batches_services import batch_source  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def release_source(annotation_source, tmp_path):  # noqa: F811
    batches, plan, annotations, _, batch, _ = annotation_source
    ident = plan.legacy.identity
    service = EpisodePackageService(
        batches.packages.connections,
        batches.storage,
        tmp_path / "releases",
        legacy_catalog=batches.legacy,
    )
    with scoped(plan.source):
        prepared = prepare(annotation_source)
        applied = MigrationAnnotations(batches).apply(ident.dataset_id, prepared["mapping_id"])
        task_id = applied["plan"]["target_task_id"]
        editor = actor(ident, "release-editor")
        claimed = annotations.claim(task_id, editor)
        submission = annotations.submit_for_review(
            task_id,
            editor,
            expected_revision=1,
            if_match=claimed.etag,
            idempotency_key="release-submit",
        )
        annotations.review(
            task_id,
            actor(ident, "release-reviewer"),
            ReviewDecision.APPROVE,
            revision=1,
            submission_id=submission.submission_id,
            if_match=annotations.get_task(task_id).etag,
        )
        command = PublishCollectionRelease(
            dataset_version="g1-release",
            collection_version_id=batch["plan"]["collection"]["collection_version_id"],
        )
    yield annotation_source, service, command, task_id
    service.close()


def state(service, ident):
    with service.repository.transaction() as cursor:
        cursor.execute(
            """SELECT dataset_document FROM dataset_registry.datasets
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s""",
            (*ident.scope, ident.dataset_id),
        )
        dataset = cursor.fetchone()[0]
        cursor.execute(
            """SELECT count(*) FROM episode_store.collection_releases
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s""",
            (*ident.scope, ident.dataset_id),
        )
        releases = cursor.fetchone()[0]
        cursor.execute(
            """SELECT count(*) FROM dataset_registry.dataset_versions
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s
              AND version_scope='DATASET_RELEASE' """,
            (*ident.scope, ident.dataset_id),
        )
        return dataset, releases, cursor.fetchone()[0]


def test_release_freezes_complete_annotation_and_exact_package_without_lance(release_source):
    source, service, command, task_id = release_source
    batches, plan, annotations, _, _, _ = source
    ident = plan.legacy.identity
    with scoped(plan.source):
        original = state(service, ident)
        with service.repository.transaction() as cursor:
            before = read_projection(
                cursor, (*ident.scope, ident.dataset_id), "version_" + command.collection_version_id
            )
        preflight = service.releases.preflight(ident.dataset_id, command)
        assert preflight.eligible_episode_ids == (ident.episode_id,)
        assert len(preflight.excluded_episodes) == 1
        assert state(service, ident) == original
        command = command.model_copy(update={"expected_content_hash": preflight.content_hash})
        released = service.releases.publish(ident.dataset_id, command)
        assert service.releases.publish(ident.dataset_id, command) == released
        assert service.releases.get(ident.dataset_id, released.version_id) == released
        with service.repository.transaction() as cursor:
            cursor.execute(
                "SELECT document FROM episode_store.collection_releases WHERE dataset_id=%s",
                (ident.dataset_id,),
            )
            document = cursor.fetchone()[0]
            assert digest(document) == released.document_sha256
            member = document["content"]["members"][0]
            assert member["data"]["storage_format"] == "lerobot_v3"
            assert member["data"]["data_version_id"] == plan.source.data_version_id
            assert member["annotation"]["revision"]["tags"] == [
                t.model_dump(mode="json")
                for t in annotations.get_history(task_id, actor(ident)).revisions[1].tags
            ]
            assert member["review"]["decision"] == "APPROVE"
            assert member["review"]["reviewer_id"] != member["submission"]["submitted_by"]
            after = read_projection(cursor, (*ident.scope, ident.dataset_id), released.version_id)
            assert (
                read_projection(
                    cursor,
                    (*ident.scope, ident.dataset_id),
                    "version_" + command.collection_version_id,
                )
                == before
            )
            assert len(after["dataset_version_episodes"]) == 1
            previous = next(
                r
                for r in before["dataset_version_episode_revisions"]
                if r["episode_id"] == ident.episode_id
            )
            assert (
                after["dataset_version_episode_revisions"][0]["revision_document"]["streams"]
                == previous["revision_document"]["streams"]
            )
            assert after["dataset_versions"][0]["version_scope"] == "DATASET_RELEASE"
            assert after["dataset_version_episodes"][0]["review_status"] is None
        assert state(service, ident)[1:] == (1, 1)
        assert (
            state(service, ident)[0]["current_ready_version"]["version_id"] == released.version_id
        )
        original_unchanged(source)


@pytest.mark.parametrize(
    "stage", ["release_object_verified", "release_projection_written", "release_before_commit"]
)
def test_release_interruption_is_atomic_and_retry_preserves_old_history(release_source, stage):
    source, service, command, _ = release_source
    _, plan, _, _, _, _ = source
    ident = plan.legacy.identity

    def fail(current):
        if current == stage:
            raise RuntimeError("injected publication interruption")

    with scoped(plan.source):
        preflight = service.releases.preflight(ident.dataset_id, command)
        command = command.model_copy(update={"expected_content_hash": preflight.content_hash})
        before = state(service, ident)
        with pytest.raises(RuntimeError, match="injected publication"):
            CollectionReleases(service, checkpoint=fail).publish(ident.dataset_id, command)
        assert state(service, ident) == before
        result = service.releases.publish(ident.dataset_id, command)
        assert result.content_hash == preflight.content_hash
        assert state(service, ident)[1:] == (1, 1)
        original_unchanged(source)


def test_concurrent_release_returns_one_receipt_and_one_business_version(release_source):
    source, service, command, _ = release_source
    _, plan, _, _, _, _ = source
    ident = plan.legacy.identity
    with scoped(plan.source):
        command = command.model_copy(
            update={
                "expected_content_hash": service.releases.preflight(
                    ident.dataset_id, command
                ).content_hash
            }
        )

    def publish(_):
        with scoped(plan.source):
            return service.releases.publish(ident.dataset_id, command)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(publish, range(3)))
    assert results[0] == results[1] == results[2]
    with scoped(plan.source):
        assert state(service, ident)[1:] == (1, 1)
        with service.repository.transaction() as cursor:
            cursor.execute(
                """SELECT count(*) FROM core.audit_events WHERE action='dataset.collection_released'
                AND resource_id=%s AND organization_id=%s""",
                (results[0].version_id, ident.organization_id),
            )
            assert cursor.fetchone()[0] == 1


def test_changed_annotation_rejects_preflight_and_release_name_stays_immutable(release_source):
    source, service, command, task_id = release_source
    _, plan, annotations, _, _, _ = source
    ident = plan.legacy.identity
    with scoped(plan.source):
        approved = annotations.get_task(task_id)
        preflight = service.releases.preflight(ident.dataset_id, command)
        command = command.model_copy(update={"expected_content_hash": preflight.content_hash})
        released = service.releases.publish(ident.dataset_id, command)
        annotations.save_draft(
            task_id,
            actor(ident, "release-editor"),
            (),
            expected_revision=1,
            if_match=approved.etag,
            client_mutation_id="later-edit",
        )
        assert service.releases.publish(ident.dataset_id, command) == released
        stale = command.model_copy(update={"dataset_version": "later-release"})
        with pytest.raises(PackageConflict):
            service.releases.publish(ident.dataset_id, stale)
        with pytest.raises(PackageConflict):
            service.releases.publish(
                ident.dataset_id, command.model_copy(update={"expected_content_hash": "0" * 64})
            )
        assert service.releases.get(ident.dataset_id, released.version_id) == released
        original_unchanged(source)


def test_archive_cannot_be_new_working_release_but_committed_release_survives_rollback(
    release_source,
):
    source, service, command, _ = release_source
    batches, plan, _, _, batch, _ = source
    ident = plan.legacy.identity
    with scoped(plan.source):
        command = command.model_copy(
            update={
                "expected_content_hash": service.releases.preflight(
                    ident.dataset_id, command
                ).content_hash
            }
        )
        released = service.releases.publish(ident.dataset_id, command)
        # Working collection rollback is independent of the immutable business release.
        rolled_back = batches.rollback(ident.dataset_id, batch["batch_id"])
        assert rolled_back["status"] == "ROLLED_BACK"
        assert service.releases.get(ident.dataset_id, released.version_id) == released
        assert (
            state(service, ident)[0]["current_ready_version"]["version_id"] == released.version_id
        )
        with pytest.raises(PackageConflict):
            service.releases.preflight(ident.dataset_id, command)
        assert service.releases.publish(ident.dataset_id, command) == released


def test_business_name_can_equal_another_release_id_without_aliasing(release_source):
    source, service, command, _ = release_source
    _, plan, _, _, _, _ = source
    ident = plan.legacy.identity
    with scoped(plan.source):
        command = command.model_copy(
            update={
                "expected_content_hash": service.releases.preflight(
                    ident.dataset_id, command
                ).content_hash
            }
        )
        first = service.releases.publish(ident.dataset_id, command)
        other = command.model_copy(
            update={"dataset_version": first.version_id, "expected_content_hash": None}
        )
        other = other.model_copy(
            update={
                "expected_content_hash": service.releases.preflight(
                    ident.dataset_id, other
                ).content_hash
            }
        )
        second = service.releases.publish(ident.dataset_id, other)
        assert second.version_id != first.version_id
        assert service.releases.publish(ident.dataset_id, other) == second
        assert service.releases.get(ident.dataset_id, first.version_id) == first
        assert service.releases.get(ident.dataset_id, second.version_id) == second
        assert state(service, ident)[1:] == (2, 2)


@pytest.mark.parametrize("field", ["organization_id", "project_id", "region_code"])
def test_release_is_unavailable_outside_exact_scope(release_source, field):
    source, service, command, _ = release_source
    _, plan, _, _, _, _ = source
    ident = plan.legacy.identity
    with scoped(plan.source):
        command = command.model_copy(
            update={
                "expected_content_hash": service.releases.preflight(
                    ident.dataset_id, command
                ).content_hash
            }
        )
        service.releases.publish(ident.dataset_id, command)
        token = bind_request_context(replace(current_request_context(), **{field: "foreign"}))
        try:
            with pytest.raises(KeyError):
                service.releases.get(ident.dataset_id, command.dataset_version)
            with pytest.raises(PackageConflict):
                service.releases.preflight(ident.dataset_id, command)
        finally:
            reset_request_context(token)


def test_release_database_row_is_immutable(release_source):
    source, service, command, _ = release_source
    _, plan, _, _, _, _ = source
    ident = plan.legacy.identity
    with scoped(plan.source):
        command = command.model_copy(
            update={
                "expected_content_hash": service.releases.preflight(
                    ident.dataset_id, command
                ).content_hash
            }
        )
        service.releases.publish(ident.dataset_id, command)
        for sql in (
            "UPDATE episode_store.collection_releases SET content_hash=%s WHERE dataset_id=%s",
            "DELETE FROM episode_store.collection_releases WHERE content_hash=%s AND dataset_id=%s",
        ):
            with pytest.raises(psycopg.Error), service.repository.transaction() as cursor:
                cursor.execute(sql, (command.expected_content_hash, ident.dataset_id))


def test_real_gateway_publication_requires_ordinary_scope_and_both_capabilities(release_source):
    source, service, command, _ = release_source
    _, plan, _, _, _, _ = source
    ident = plan.legacy.identity
    with (
        scoped(plan.source),
        httpx.Client(base_url="http://gateway:8080", timeout=45, trust_env=False) as client,
    ):
        principal, headers = register_and_grant(
            client,
            service.repository,
            ident,
            capabilities=[
                "dataset_version.read",
                "dataset_version.publish",
                "annotation_task.read",
            ],
        )
        path = (
            f"/api/v1/projects/{ident.project_id}/regions/{ident.region_code}/datasets/"
            f"{ident.dataset_id}/collection-releases"
        )
        response = client.post(
            path + "/preflight", json=command.model_dump(mode="json"), headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        command = command.model_copy(
            update={"expected_content_hash": response.json()["content_hash"]}
        )
        response = client.post(path, json=command.model_dump(mode="json"), headers=headers)
        assert response.status_code == 201, response.text
        released = response.json()
        assert (
            client.post(path, json=command.model_dump(mode="json"), headers=headers).json()
            == released
        )
        assert client.get(path + "/" + released["version_id"], headers=headers).json() == released
        assert client.get(path + "/" + released["version_id"]).status_code == 401
        assert (
            client.get(
                path + "/" + released["version_id"],
                headers={**headers, "X-Organization-Id": "foreign"},
            ).status_code
            == 403
        )
        for capability in ("dataset_version.publish", "annotation_task.read"):
            with service.repository.transaction() as cursor:
                cursor.execute(
                    """UPDATE access_control.capability_grants SET active=false
                    WHERE principal_id=%s AND capability_key=%s""",
                    (principal, capability),
                )
            assert (
                client.post(path, json=command.model_dump(mode="json"), headers=headers).status_code
                == 403
            )
        assert client.post("/api/v1/auth/session:logout", headers=headers).status_code == 204
