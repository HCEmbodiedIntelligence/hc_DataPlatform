import asyncio
import os
import shutil

import httpx
import pytest
from fastapi import FastAPI
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from hc_data_platform.core.errors import problem
from hc_data_platform.lerobot_imports import oss_imports
from hc_data_platform.lerobot_imports import router as batch_routes
from hc_data_platform.lerobot_imports.router import router
from hc_data_platform.lerobot_imports.service import LeRobotWebUploadService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.http import require_auth_context
from hc_data_platform.workflow import activities
from hc_data_platform.workflow import router as job_routes
from hc_data_platform.workflow.models import JobRecord
from hc_data_platform.workflow.oss_batch_workflow import (
    OssBatchImportWorkflow,
    commit_oss_batch_item,
)
from hc_data_platform.workflow.service import TemporalWorkflowLauncher


@pytest.mark.asyncio
async def test_accepted_batch_runs_after_client_closes_and_deduplicates(monkeypatch):
    executable = os.getenv("HC_TEST_TEMPORAL_CLI") or shutil.which("temporal")
    if not executable:
        pytest.skip("requires the Temporal CLI for a local isolated queue")
    snapshots, submitted = [], []

    class Repository:
        def put_job(self, request):
            snapshots.append(request.job)

    def commit(service, *, auth, command, **scope):
        LeRobotWebUploadService._authorize(auth, **scope)
        assert auth.subject_id == "operator"
        submitted.append(command.recording_path)
        if command.recording_path == "bad.mcap":
            raise problem(
                status=422, code="BAD_RECORDING", title="Invalid", detail="Invalid test recording"
            )
        from types import SimpleNamespace
        return SimpleNamespace(import_id=command.recording_path)

    from hc_data_platform.ingest import upload_batches

    memberships = []
    def register(scope, channel, key, upload_id=None):
        memberships.append((scope, channel, key, upload_id))
        return 1

    monkeypatch.setattr(upload_batches, "register_batch", register)
    receipts = []
    monkeypatch.setattr(
        batch_routes, "record_accepted", lambda batch, job: receipts.append(job.workflow_id)
    )
    monkeypatch.setattr(oss_imports, "commit_source", commit)
    monkeypatch.setattr(
        activities,
        "_dependencies",
        activities.ActivityDependencies(oss_uploads=object(), workflow_jobs=Repository()),
    )
    auth = AuthContext(
        subject_id="operator",
        organization_ids=frozenset(["org"]),
        project_ids=frozenset(["project"]),
        region_codes=frozenset(["cn"]),
        organization_scope_triples=frozenset([("org", "project", "cn")]),
        capabilities=frozenset(["upload.manage"]),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth_context] = lambda: auth
    path = "/api/v1/projects/project/regions/cn/lerobot-imports/oss:batches"
    options = dict(
        headers={"X-Organization-Id": "org"},
        json={
            "request_id": "e048bed8-766a-4fd3-b6b7-746ed3efdc55",
            "source_uri": "oss://example/test.zip",
            "inventory_sha256": "f" * 64,
            "dataset_id": "dataset",
            "collection_task_id": "task",
            "robot_id": "robot",
            "recording_paths": ["one.mcap", "bad.mcap", "three.mcap"],
        },
    )
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=executable,
        data_converter=pydantic_data_converter,
    ) as env:
        launcher = TemporalWorkflowLauncher("local", task_queue="oss-test", client=env.client)
        monkeypatch.setattr(job_routes, "get_launcher", lambda: launcher)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as browser:
            response = await browser.post(path, **options)
            assert response.status_code == 202, response.text
            receipt = response.json()
            assert receipt["total"] == 3 and submitted == []
        async with Worker(
            env.client,
            task_queue="oss-test",
            workflows=[OssBatchImportWorkflow],
            activities=[commit_oss_batch_item, activities.persist_workflow_job],
        ):
            handle = env.client.get_workflow_handle(receipt["batch_id"], result_type=JobRecord)
            result = await asyncio.wait_for(handle.result(), 45)
        assert submitted == ["one.mcap", "bad.mcap", "three.mcap"]
        assert result.result["submitted"] == 2 and result.result["failed"] == 1
        assert "bad.mcap" in result.result["first_failure"]
        assert [row.result["submitted"] for row in snapshots] == [0, 1, 1, 2]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as browser:
            repeated = await browser.post(path, **options)
        assert repeated.json() == receipt and len(submitted) == 3
    assert receipts == [receipt["batch_id"]] * 2
    assert {row[3] for row in memberships if row[3]} == {"one.mcap", "three.mcap"}
    assert {row[2] for row in memberships} == {receipt["batch_id"]}


@pytest.mark.asyncio
async def test_resume_registered_selection_while_legacy_activity_is_still_queued(monkeypatch):
    from hc_data_platform.lerobot_imports.oss_batch import BatchInput
    from hc_data_platform.workflow.names import control_task_queue

    executable = os.getenv("HC_TEST_TEMPORAL_CLI") or shutil.which("temporal")
    if not executable:
        pytest.skip("requires the Temporal CLI")
    received = []

    class Repo:
        def put_job(self, request):
            pass

    def commit(service, *, auth, command, **scope):
        LeRobotWebUploadService._authorize(auth, **scope)
        received.append(command.recording_path)
        from types import SimpleNamespace
        return SimpleNamespace(import_id=command.recording_path)

    monkeypatch.setattr(oss_imports, "commit_source", commit)
    from hc_data_platform.ingest import upload_batches
    monkeypatch.setattr(upload_batches, "register_batch", lambda *args: 1)
    monkeypatch.setattr(activities, "_dependencies", activities.ActivityDependencies(oss_uploads=object(), workflow_jobs=Repo()))
    batch = BatchInput.model_validate(dict(
        organization_id="org", project_id="project", region_code="cn", actor_id="operator",
        command=dict(request_id="e048bed8-766a-4fd3-b6b7-746ed3efdc55", source_uri="oss://bucket/test.zip",
                     inventory_sha256="f"*64, dataset_id="dataset", collection_task_id="task", robot_id="robot",
                     recording_paths=["one.mcap", "two.mcap"]),
    ))
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=executable,
                                                      data_converter=pydantic_data_converter) as env:
        async with (
            Worker(env.client, task_queue="queued", workflows=[OssBatchImportWorkflow],
                   activities=[activities.persist_workflow_job], no_remote_activities=True),
            Worker(env.client, task_queue=control_task_queue("queued"), activities=[commit_oss_batch_item]),
        ):
            h = await env.client.start_workflow(OssBatchImportWorkflow.run, batch, id="legacy", task_queue="queued")
            async with asyncio.timeout(25):
                while not (await h.describe()).raw_description.pending_activities:
                    await asyncio.sleep(0.1)
            await h.signal("resume_mcap_registration")
            result = await asyncio.wait_for(h.result(), 25)
            assert received == ["one.mcap", "two.mcap"]
            assert result.result["submitted"] == 2 and result.result["failed"] == 0
