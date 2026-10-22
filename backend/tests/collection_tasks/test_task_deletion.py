"""Permanent deletion exercises real immutable packages, DB fences and object storage."""

import json
from uuid import uuid4

import psycopg
import pytest

from hc_data_platform.collection_tasks.deletion import PostgresTaskDeletion, TaskObjectPurger
from hc_data_platform.collection_tasks.deletion_plan import locators, registered_locators
from hc_data_platform.collection_tasks.models import CreateCollectionTask
from hc_data_platform.collection_tasks.postgres import PostgresCollectionTaskRepository
from hc_data_platform.collection_tasks.service import CollectionTaskService
from hc_data_platform.core.errors import ProblemException
from tests.episode_store.test_commit_services import EpisodePackageCommitter, scoped

pytest_plugins = ["tests.episode_store.test_commit_services"]


def test_unrelated_external_storage_does_not_block_scoped_object_reference_check():
    document = {"staging_uri": "file:///tmp/unrelated.arrow", "object_key": "raw/shared/file"}
    assert locators(document, "bucket", ignore_external=True) == ({"raw/shared/file"}, set())
    with pytest.raises(ProblemException) as blocked:
        locators(document, "bucket")
    assert blocked.value.problem.code == "TASK_DELETE_EXTERNAL_STORAGE"


def test_task_metadata_cannot_supply_deletion_locators():
    assert registered_locators(
        "dataset_registry.datasets",
        {
            "dataset_document": {
                "object_key": "raw/other/org/keep.mcap",
                "object_prefix": "raw/other/org/",
            }
        },
        "bucket",
    ) == (set(), set())


@pytest.mark.integration
def test_package_delete_is_scoped_resumable_and_fences_stale_writers(services, package):
    repository, storage = services
    root, source = package
    scope = source.identity.scope
    with scoped(source):
        with repository.connections() as c:
            c.execute(
                "INSERT INTO registry.organization_projects VALUES (%s,%s,'Deletion test')",
                scope[:2],
            )
        tasks = CollectionTaskService(PostgresCollectionTaskRepository(repository.connections))
        task = tasks.create(
            organization_id=scope[0],
            project_id=scope[1],
            command=CreateCollectionTask(name="Delete fixture", type="capture", scenario="test"),
            idempotency_key=uuid4().hex,
        ).record
        keep = tasks.create(
            organization_id=scope[0],
            project_id=scope[1],
            command=CreateCollectionTask(name="Keep fixture", type="capture", scenario="test"),
            idempotency_key=uuid4().hex,
        ).record
        with repository.connections() as c:
            c.execute(
                "UPDATE collection_tasks.collection_tasks SET upload_region_code=%s "
                "WHERE collection_task_id=%s",
                (scope[2], task.collection_task_id),
            )
        source = source.model_copy(
            update={
                "identity": source.identity.model_copy(update={"dataset_id": task.dataset_id}),
                "task_id": task.collection_task_id,
            }
        )
        (root / "hc/source.json").write_text(
            json.dumps(
                {"identity": source.identity.model_dump(), "source_sha256": source.source_sha256}
            )
        )
        receipt = EpisodePackageCommitter(repository, storage).commit(root, source)
        key = receipt.data.package_prefix + "/meta/info.json"
        assert storage.head(key) is not None
        with pytest.raises(psycopg.Error, match="immutable"), repository.connections() as c:
            c.execute("DELETE FROM episode_store.versions WHERE dataset_id=%s", (task.dataset_id,))

        class FlakyObjects(TaskObjectPurger):
            broken = True

            def purge(self, manifest):
                if self.broken:
                    raise OSError("simulated provider outage")
                super().purge(manifest)

        objects = FlakyObjects(storage._client, storage._bucket)
        service = PostgresTaskDeletion(repository.connections, objects)
        with pytest.raises(ProblemException) as stale:
            service.delete(scope, task.collection_task_id, '"v99"', "delete-fixture")
        assert stale.value.problem.status == 412
        with pytest.raises(ProblemException) as failed:
            service.delete(scope, task.collection_task_id, '"v1"', "delete-fixture")
        assert failed.value.problem.code == "TASK_DELETE_CLEANUP_PENDING"
        assert storage.head(key) is not None
        assert tasks.detail(*scope[:2], task.collection_task_id).status.value == "CANCELLED"
        with (
            pytest.raises(psycopg.Error, match="COLLECTION_TASK_DELETED"),
            repository.connections() as c,
        ):
            c.execute(
                "UPDATE collection_tasks.collection_tasks SET status='ACTIVE' "
                "WHERE collection_task_id=%s",
                (task.collection_task_id,),
            )
        objects.broken = False
        result = service.delete(scope, task.collection_task_id, '"v1"', "delete-fixture")
        assert result.deleted_objects > 0 and result.deleted_bytes > 0
        assert storage.head(key) is None
        assert repository.current(source.identity) is None
        assert tasks.detail(*scope[:2], keep.collection_task_id).name == "Keep fixture"
        assert service.delete(scope, task.collection_task_id, '"v1"', "delete-fixture") == result
        with pytest.raises(ProblemException):
            tasks.detail(*scope[:2], task.collection_task_id)
        with pytest.raises(psycopg.Error, match="COLLECTION_TASK_DELETED"):
            EpisodePackageCommitter(repository, storage).commit(root, source)
        with repository.connections() as c:
            assert (
                c.execute(
                    "SELECT count(*) FROM core.audit_events WHERE resource_id=%s "
                    "AND action='collection_task.deleted'",
                    (task.collection_task_id,),
                ).fetchone()[0]
                == 1
            )
