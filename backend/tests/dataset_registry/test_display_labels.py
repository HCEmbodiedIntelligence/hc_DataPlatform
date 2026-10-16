from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from hc_data_platform.dataset_registry.display_labels import (
    DisplayLabel,
    DisplayReference,
    PostgresDisplayLabels,
)
from hc_data_platform.dataset_registry.models import DatasetPageScope
from hc_data_platform.dataset_registry.repository import InMemoryDatasetPageRepository
from hc_data_platform.dataset_registry.router import configure_dataset_page
from hc_data_platform.dataset_registry.service import DatasetPageService
from hc_data_platform.security.auth import AuthContext
from tests.dataset_registry.test_dataset_page_api import (
    ORGANIZATION_ID,
    PROJECT_ID,
    REGION_CODE,
    _app,
    _headers,
)


def test_lookup_is_bounded_scoped_and_checks_each_resource_permission():
    seen = []

    def reader(scope, references):
        seen.append((scope, references))
        return tuple(DisplayLabel(**ref.model_dump(), label="Readable name") for ref in references)

    service = DatasetPageService(
        InMemoryDatasetPageRepository(organization_projects=((ORGANIZATION_ID, PROJECT_ID),)),
        display_label_reader=reader,
    )
    configure_dataset_page(service)
    auth = AuthContext(
        subject_id="labels-test",
        organization_ids=frozenset({ORGANIZATION_ID}),
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        organization_scope_triples=frozenset({(ORGANIZATION_ID, PROJECT_ID, REGION_CODE)}),
        organization_scoped_capabilities=frozenset({(ORGANIZATION_ID, PROJECT_ID, "dataset.read")}),
    )
    current = {"value": auth}
    client = TestClient(_app(current))
    path = f"/api/v1/projects/{PROJECT_ID}/datasets:display-labels"
    refs = [{"kind": "dataset", "id": "dataset_same"}, {"kind": "robot", "id": "robot_secret"}]
    response = client.post(path, headers=_headers(), json={"references": refs})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert [item["id"] for item in response.json()["items"]] == ["dataset_same"]
    assert seen[0][0] == DatasetPageScope(
        organization_id=ORGANIZATION_ID, project_id=PROJECT_ID, region_code=REGION_CODE
    )
    assert [ref.id for ref in seen[0][1]] == ["dataset_same"]
    for headers in [
        _headers(**{"X-Organization-Id": "other"}),
        _headers(**{"X-Region-Code": "other"}),
    ]:
        assert client.post(path, headers=headers, json={"references": refs}).status_code == 403
    assert len(seen) == 1
    assert client.post(path, headers=_headers(), json={"references": refs * 51}).status_code == 422
    assert (
        client.post(
            path, headers=_headers(), json={"references": [{"kind": "episode", "id": "episode_1"}]}
        ).status_code
        == 422
    )
    current["value"] = replace(auth, organization_scoped_capabilities=frozenset())
    assert client.post(path, headers=_headers(), json={"references": refs}).json()["items"] == []
    assert len(seen) == 1


@pytest.mark.integration
def test_postgres_returns_task_numbers_description_source_and_excludes_other_scopes():
    from hc_data_platform.collection_tasks.models import CreateCollectionTask
    from hc_data_platform.collection_tasks.postgres import PostgresCollectionTaskRepository
    from hc_data_platform.collection_tasks.service import CollectionTaskService
    from hc_data_platform.core.dbapi import psycopg_connection_factory
    from tests.collection_tasks.test_collection_task_postgres import (
        database_dsn,
        ensure_organization_project,
        request_scope,
    )

    dsn = database_dsn()
    connections = psycopg_connection_factory(dsn)
    tasks = CollectionTaskService(PostgresCollectionTaskRepository(connections))
    own_project, other_project = "label-" + uuid4().hex, "label-" + uuid4().hex
    own_org = ensure_organization_project(dsn, own_project)
    ensure_organization_project(dsn, other_project)
    created = []
    for project in [own_project, other_project]:
        with request_scope(project, "global"):
            created.append(
                tasks.create(
                    organization_id=f"org-{project}",
                    project_id=project,
                    command=CreateCollectionTask(
                        name="任务名称",
                        type="capture",
                        scenario="test",
                        description="创建任务时的说明",
                    ),
                    idempotency_key=uuid4().hex,
                ).record
            )
    refs = tuple(
        ref
        for task in created
        for ref in (
            DisplayReference(kind="dataset", id=task.dataset_id),
            DisplayReference(kind="collection_task", id=task.collection_task_id),
        )
    )
    with request_scope(own_project, "global"):
        result = PostgresDisplayLabels(connections)(
            DatasetPageScope(organization_id=own_org, project_id=own_project, region_code="global"),
            refs,
        )
        assert len(result) == 2
        assert result[0].id == created[0].dataset_id
        assert result[0].description_label == "任务描述（创建时同步）"
        assert result[1].label == str(int(created[0].task_code))
        assert tasks.detail(own_org, own_project, created[0].collection_task_id) == created[0]
        region_result = PostgresDisplayLabels(connections)(
            DatasetPageScope(
                organization_id=own_org, project_id=own_project, region_code="different"
            ),
            refs[:1],
        )
        assert region_result == ()
