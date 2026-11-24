from uuid import uuid4

import pytest

from hc_data_platform.collection_tasks.dataset import ensure_task_dataset
from hc_data_platform.collection_tasks.models import CreateCollectionTask
from hc_data_platform.collection_tasks.postgres import PostgresCollectionTaskRepository
from hc_data_platform.collection_tasks.service import CollectionTaskService
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.dataset_registry.repository import PostgresDatasetPageRepository
from hc_data_platform.dataset_registry.service import DatasetPageService
from tests.collection_tasks.test_collection_task_postgres import (
    create_runtime_role,
    drop_runtime_role,
    restricted_connection_factory,
)
from tests.dashboard.test_dashboard_postgres import (
    actor,
    isolated_dsn,  # noqa: F401
    organization_for,
    request_scope,
)


@pytest.fixture
def task_role(isolated_dsn):  # noqa: F811
    name = f"empty_task_dataset_{uuid4().hex[:12]}"
    create_runtime_role(isolated_dsn, name)
    try:
        yield name
    finally:
        drop_runtime_role(isolated_dsn, name)


@pytest.mark.integration
@pytest.mark.parametrize("request_region", [None, "cn-east"])
def test_task_dataset_opens_before_first_ingest_and_repair_preserves_metadata(
    isolated_dsn, task_role, request_region  # noqa: F811
):
    project, region = "project-a", request_region or "global"
    with request_scope(project, request_region):
        connect = restricted_connection_factory(isolated_dsn, task_role)
        repo = PostgresCollectionTaskRepository(connect)
        service = CollectionTaskService(repo)
        task = service.create(
            organization_id=organization_for(project),
            project_id=project,
            command=CreateCollectionTask(name="Empty Dataset", type="ROBOT", scenario="test"),
            idempotency_key=str(uuid4()),
        ).record
        assert repo.create(task) == task
        with connect() as connection:
            cursor = connection.cursor()
            before = connection.execute(
                "SELECT current_setting('app.region_code', true)"
            ).fetchone()[0]
            assert not ensure_task_dataset(cursor, task, region_code=request_region)
            after = connection.execute(
                "SELECT current_setting('app.region_code', true)"
            ).fetchone()[0]
            assert after == before

    with request_scope(project, region):
        progress = service.progress(
            organization_for(project), project, task.collection_task_id, region
        )
        assert progress.received_package_count == 0
        assert progress.captured_duration_seconds is None
        assert progress.duration_observed_package_count == 0
        assert progress.duration_unknown_package_count == 0
        assert progress.captures.upload_count == 0
        connect = psycopg_connection_factory(isolated_dsn)
        datasets = DatasetPageService(PostgresDatasetPageRepository(connect))
        args = dict(
            auth=actor(project, region, capabilities=("dataset.read",)),
            organization_id=organization_for(project),
            project_id=project,
            region_code=region,
            dataset_id=task.dataset_id,
            request_id="empty-bootstrap",
        )
        result = datasets.bootstrap(**args).data
        assert result.summary.episode_count == "0"
        assert (
            datasets._repository.get_record(
                scope=result.scope, dataset_id=task.dataset_id
            ).metadata.asset_state
            == "EMPTY"
        )
        assert result.working_version_id is None
        assert result.current_ready_version is None
        with connect() as connection:
            assert not ensure_task_dataset(connection.cursor(), task, region_code=region)
        assert datasets.bootstrap(**args).data.dataset == result.dataset
        with pytest.raises(ProblemException, match="DATASET_NOT_FOUND"):
            datasets.bootstrap(**{**args, "dataset_id": "dataset_nonexistent"})
        with connect() as connection:
            count = connection.execute(
                "SELECT count(*) FROM core.audit_events "
                "WHERE resource_id=%s AND action='dataset.task_registered'",
                (task.dataset_id,),
            ).fetchone()[0]
            assert count == 1


@pytest.mark.integration
def test_duration_sums_only_this_tasks_ingested_episodes_in_the_working_version(
    isolated_dsn,  # noqa: F811
    task_role,
):
    from hc_data_platform.dataset_registry.models import DatasetPageScope
    from hc_data_platform.dataset_registry.projection_writer import DatasetProjectionWriter
    from tests.dataset_registry.test_dataset_version_detail_postgres_integration import (
        _compare_version,
        _review_bundle,
    )

    project, region = "project-a", "cn-east"
    org = organization_for(project)
    scope = DatasetPageScope(organization_id=org, project_id=project, region_code=region)
    with request_scope(project, region):
        service = CollectionTaskService(
            PostgresCollectionTaskRepository(restricted_connection_factory(isolated_dsn, task_role))
        )
        task = service.create(
            organization_id=org,
            project_id=project,
            command=CreateCollectionTask(
                name="Admission duration", scenario="test", target={"duration_seconds": 4}
            ),
            idempotency_key=str(uuid4()),
        ).record
        with psycopg_connection_factory(isolated_dsn)() as db, db.cursor() as cursor:
            for number in (1, 2):
                collection = f"duration_{number}"
                version_id = f"version_{collection}"
                shared = dict(scope=scope, dataset_id=task.dataset_id, version_id=version_id)
                cursor.execute(
                    """INSERT INTO episode_store.collections
                    (organization_id,project_id,region_code,dataset_id,collection_version_id,
                     version,content_hash,document) VALUES (%s,%s,%s,%s,%s,%s,%s,'{}')""",
                    (org, project, region, task.dataset_id, collection, number, "a" * 64),
                )
                version, _ = _compare_version()
                DatasetProjectionWriter._put_version(cursor, version.model_copy(update=shared))
                # Episode A is carried forward. The second snapshot also contains a
                # sibling, an Episode from a shared task, and a derived copy.
                rows = [("a", task.collection_task_id, "INGESTED", 1_250_000_000)]
                if number == 2:
                    rows.extend(
                        [
                            ("b", task.collection_task_id, "INGESTED", 2_750_000_000),
                            ("other", "other-task", "INGESTED", 10_000_000_000),
                            ("derived", task.collection_task_id, "DERIVED", 8_000_000_000),
                        ]
                    )
                for ordinal, (marker, owner, kind, duration) in enumerate(rows):
                    bundle = _review_bundle(version_id=version_id, marker=marker)
                    ref = bundle.episode.selected_revision.model_copy(update={"ordinal": ordinal})
                    episode = bundle.episode.model_copy(
                        update={
                            **shared,
                            "task": owner,
                            "change_type": kind,
                            "included": True,
                            "storage_region_code": region,
                            "selected_revision": ref,
                        }
                    )
                    revision = bundle.revision.model_copy(
                        update={
                            **shared,
                            "ordinal": ordinal,
                            "duration_ns": str(duration),
                        }
                    )
                    DatasetProjectionWriter._put_episode(cursor, episode)
                    DatasetProjectionWriter._put_revision(cursor, revision)
            cursor.execute(
                """INSERT INTO episode_store.collection_heads VALUES (%s,%s,%s,%s,%s)""",
                (org, project, region, task.dataset_id, collection),
            )
        progress = service.progress(org, project, task.collection_task_id, region)
        assert progress.captured_duration_seconds == 4
        assert progress.duration_observed_package_count == 2
        assert progress.duration_unknown_package_count == 0
        assert progress.attainment.duration_seconds.progress == 1
