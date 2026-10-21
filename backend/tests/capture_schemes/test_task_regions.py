"""Task creation preserves the authorized Region through the real HTTP/DB path."""

from dataclasses import replace

import psycopg
import pytest
from fastapi.testclient import TestClient

from hc_data_platform.capture_schemes import plans
from hc_data_platform.capture_schemes.components import CaptureServices
from hc_data_platform.collection_tasks.models import CreateCollectionTask
from hc_data_platform.collection_tasks.router import get_collection_task_service
from hc_data_platform.collection_tasks.router import router as task_router
from hc_data_platform.core.errors import ProblemException

from .test_application_dependencies import Samples, application, identity
from .test_entrypoint_transactions import create_task, request_scope, seed_scheme, task_service
from .test_transactions import environment, selection  # noqa: F401


def test_http_creation_requires_and_preserves_authorized_capture_region(environment):  # noqa: F811
    dsn, scope, _ = environment
    ref = seed_scheme(dsn, scope)
    services = CaptureServices(lambda: psycopg.connect(dsn), Samples("unused"), lambda _: True)
    region_scope = (*scope[:2], "cn-east")
    scoped_identity = identity(region_scope, "upload.manage", "upload.read")
    app = application(scoped_identity, services)
    app.include_router(task_router)
    app.dependency_overrides[get_collection_task_service] = lambda: task_service(dsn)
    path = f"/api/v1/projects/{scope[1]}/collection-tasks"
    command = CreateCollectionTask(
        name="Regional task",
        type="ROBOT_CAPTURE",
        scenario="integration",
        capture_plan=selection(ref, "robot-1"),
    ).model_dump(mode="json", exclude_none=True)
    headers = {"X-Organization-Id": scope[0], "Idempotency-Key": "regional-http-task"}
    project_identity = replace(
        scoped_identity,
        organization_scope_triples=scoped_identity.organization_scope_triples
        | {(*scope[:2], None)},
    )
    missing_region_app = application(project_identity, services)
    missing_region_app.include_router(task_router)
    with TestClient(missing_region_app) as project_client:
        missing = project_client.post(path, headers=headers, json=command)
        assert missing.status_code == 422 and missing.json()["code"] == "CAPTURE_REGION_REQUIRED"
    with TestClient(app) as client:
        missing = client.post(path, headers=headers, json=command)
        assert missing.status_code == 403
        wrong = client.post(path, headers={**headers, "X-Region-Code": "cn-west"}, json=command)
        assert wrong.status_code == 403
        accepted = client.post(path, headers={**headers, "X-Region-Code": "cn-east"}, json=command)
        assert accepted.status_code == 201, accepted.text
        task = accepted.json()
        replay = client.post(path, headers={**headers, "X-Region-Code": "cn-east"}, json=command)
        assert replay.status_code == 201 and replay.json() == task
        with psycopg.connect(dsn) as db:
            saved = plans.get_plan(db, region_scope, task["collection_task_id"])
            assert saved["region_code"] == "cn-east" and saved["dataset_id"] == task["dataset_id"]
            assert db.execute(
                "SELECT region_code FROM dataset_registry.datasets "
                "WHERE organization_id=%s AND dataset_id=%s",
                (scope[0], task["dataset_id"]),
            ).fetchall() == [("cn-east",)]
    # A new scoped request cannot replay the same task identity into a different Region.
    with request_scope((*scope[:2], "cn-west")), pytest.raises(ProblemException) as exc:
        task_service(dsn).create(
            organization_id=scope[0],
            project_id=scope[1],
            command=CreateCollectionTask.model_validate(command),
            idempotency_key="regional-http-task",
        )
    assert exc.value.problem.code == "IDEMPOTENCY_KEY_REUSED"


def test_existing_dataset_supplies_region_and_ambiguous_target_is_rejected(environment):  # noqa: F811
    dsn, scope, _ = environment
    ref = seed_scheme(dsn, scope)
    owner = create_task(dsn, scope, ref, key="owner", region="cn-east")
    command = CreateCollectionTask(
        name="Shared dataset task",
        type="ROBOT_CAPTURE",
        scenario="integration",
        dataset_id=owner.dataset_id,
    )
    with request_scope((*scope[:2], None)):
        task = (
            task_service(dsn)
            .create(
                organization_id=scope[0],
                project_id=scope[1],
                command=command,
                idempotency_key="shared",
            )
            .record
        )
    with psycopg.connect(dsn) as db:
        assert db.execute(
            "SELECT upload_region_code FROM collection_tasks.collection_tasks "
            "WHERE organization_id=%s AND collection_task_id=%s",
            (scope[0], task.collection_task_id),
        ).fetchone() == ("cn-east",)
    with request_scope((*scope[:2], "cn-west")), pytest.raises(ProblemException) as exc:
        task_service(dsn).create(
            organization_id=scope[0],
            project_id=scope[1],
            command=command,
            idempotency_key="wrong",
        )
    assert exc.value.problem.code == "COLLECTION_TASK_DATASET_NOT_ASSIGNABLE"
    with psycopg.connect(dsn) as db:
        db.execute(
            """INSERT INTO dataset_registry.datasets
            (organization_id,project_id,region_code,dataset_id,name,description,availability,
             owner_id,owner_display_name,asset_state,storage_class,version,dataset_document,
             created_at,updated_at,activity_at)
            SELECT organization_id,project_id,'cn-west',dataset_id,name,description,availability,
             owner_id,owner_display_name,asset_state,storage_class,version,
             jsonb_set(dataset_document,'{scope,region_code}','"cn-west"'::jsonb),
             created_at,updated_at,activity_at
            FROM dataset_registry.datasets WHERE organization_id=%s AND dataset_id=%s""",
            (scope[0], owner.dataset_id),
        )
    with request_scope((*scope[:2], None)), pytest.raises(ProblemException) as exc:
        task_service(dsn).create(
            organization_id=scope[0],
            project_id=scope[1],
            command=command,
            idempotency_key="ambiguous",
        )
    assert exc.value.problem.code == "COLLECTION_TASK_REGION_REQUIRED"
    with request_scope((*scope[:2], "cn-west")):
        selected = (
            task_service(dsn)
            .create(
                organization_id=scope[0],
                project_id=scope[1],
                command=command,
                idempotency_key="specific",
            )
            .record
        )
    with psycopg.connect(dsn) as db:
        assert db.execute(
            "SELECT upload_region_code FROM collection_tasks.collection_tasks "
            "WHERE organization_id=%s AND collection_task_id=%s",
            (scope[0], selected.collection_task_id),
        ).fetchone() == ("cn-west",)
