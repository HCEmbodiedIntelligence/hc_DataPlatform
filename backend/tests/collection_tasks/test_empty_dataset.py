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
from tests.dashboard.test_dashboard_postgres import (
    actor,
    isolated_dsn,  # noqa: F401
    organization_for,
    request_scope,
)


@pytest.mark.integration
def test_task_dataset_opens_before_first_ingest_and_repair_preserves_metadata(isolated_dsn):  # noqa: F811
    project, region = "project-a", "cn-east"
    with request_scope(project, region):
        connect = psycopg_connection_factory(isolated_dsn)
        repo = PostgresCollectionTaskRepository(connect)
        service = CollectionTaskService(repo)
        task = service.create(
            organization_id=organization_for(project),
            project_id=project,
            command=CreateCollectionTask(name="Empty Dataset", type="ROBOT", scenario="test"),
            idempotency_key=str(uuid4()),
        ).record
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
