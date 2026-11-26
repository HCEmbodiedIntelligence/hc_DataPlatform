import asyncio
import json
import os
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import av
import httpx
import numpy as np
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import SecretStr

from hc_data_platform.annotation import InMemoryAnnotationService, model_inference
from hc_data_platform.annotation.model_router import annotation_service
from hc_data_platform.annotation.router import get_annotation_service, router
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.episode_store.models import DataVersionRef, EpisodeIdentity
from hc_data_platform.episode_store.postgres import PostgresEpisodeStore
from hc_data_platform.security import AuthContext


@pytest.mark.parametrize(
    "span",
    [
        {"label_id": 2, "start_step": 0, "end_step": 2},
        {"label_id": 1, "start_step": 1, "end_step": 1},
        {"label_id": 1, "start_step": 0, "end_step": 3},
        {"label_id": True, "start_step": 0, "end_step": 2},
    ],
)
def test_invalid_prediction_rejects_entire_response(span):
    valid = {"label_id": 1, "start_step": 0, "end_step": 2}
    with pytest.raises(ProblemException, match="MODEL_ANNOTATION_INVALID"):
        model_inference.parse_tags(json.dumps({"segments": [valid, span]}), {1: "拿取"}, 2)


@pytest.mark.integration
def test_reuse_project_settings_and_read_generated_episode_draft(tmp_path, monkeypatch):
    dsn = os.getenv("HC_ANNOTATION_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("requires isolated PostgreSQL")
    asyncio.run(apply_migrations(dsn))
    project = "vision-" + uuid4().hex
    org, region = "org", "cn-test"
    identity = EpisodeIdentity(
        organization_id=org,
        project_id=project,
        region_code=region,
        dataset_id="dataset",
        rollout_id="rollout",
        episode_id="episode",
    )
    ref = DataVersionRef(
        identity=identity,
        data_version_id="data",
        version=1,
        content_hash="f" * 64,
        schema_snapshot_id="schema",
        package_prefix="test",
        frame_count=4,
    )
    service = InMemoryAnnotationService(allowed_tags=("拿取", "放置"))
    service.create_task(
        task_id="task",
        project_id=project,
        region_code=region,
        dataset_id="dataset",
        dataset_version=1,
        rollout_id="rollout",
        data_ref=ref,
        base_step_count=4,
    )
    auth = AuthContext(
        subject_id="admin",
        project_ids=frozenset({project}),
        region_codes=frozenset({region}),
        organization_ids=frozenset({org}),
        organization_scope_triples=frozenset({(org, project, region)}),
        capabilities=frozenset({"platform.admin"}),
    )
    video_path = tmp_path / "camera.mp4"
    with av.open(str(video_path), "w") as video:
        stream = video.add_stream("mpeg4", rate=10)
        stream.width = stream.height = 16
        for step in range(4):
            frame = av.VideoFrame.from_ndarray(
                np.full((16, 16, 3), step * 50, dtype=np.uint8), format="rgb24"
            )
            video.mux(stream.encode(frame))
        video.mux(stream.encode())
    package = SimpleNamespace(
        receipt=SimpleNamespace(data=ref),
        file=lambda _: nullcontext(video_path),
        document=lambda _: {"features": {"front": {"dtype": "video", "info": {}}}},
    )
    repository = PostgresEpisodeStore(lambda: psycopg.connect(dsn))
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(data_source_credential_key=SecretStr("test-only"))
    app.state.episode_packages = SimpleNamespace(
        repository=repository, files=SimpleNamespace(open=lambda *_: package)
    )
    app.dependency_overrides[annotation_service] = lambda: service
    app.dependency_overrides[get_annotation_service] = lambda: service

    @app.middleware("http")
    async def authenticate(request, call_next):
        request.state.auth_context = auth
        token = bind_request_context(
            RequestContext(organization_id=org, project_id=project, region_code=region)
        )
        try:
            return await call_next(request)
        finally:
            reset_request_context(token)

    @app.exception_handler(ProblemException)
    async def error_handler(request, exc):
        return JSONResponse(
            status_code=exc.problem.status, content=exc.problem.model_dump(mode="json")
        )

    span = {"label_id": 1, "start_step": 0, "end_step": 3}

    def provider(request):
        assert request.headers["Authorization"] == "Bearer test-provider-key"
        body = json.loads(request.content)
        content = body["messages"][-1]["content"]
        assert json.loads(content[0]["text"])["标签候选"] == {"1": "拿取", "2": "放置"}
        assert sum(item["type"] == "image_url" for item in content) == 4
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps({"segments": [span]})}}]}
        )

    client_class = httpx.Client
    monkeypatch.setattr(
        model_inference.httpx,
        "Client",
        lambda **kw: client_class(transport=httpx.MockTransport(provider), **kw),
    )
    headers = {"X-Organization-ID": org, "X-Project-ID": project, "X-Region-Code": region}
    base = f"/api/v1/projects/{project}/regions/{region}/model-annotation"
    task_path = "/api/v1/annotation-tasks/task"
    with TestClient(app, headers=headers) as client:
        cfg = {"endpoint": "https://example.test/chat/completions", "model": "vision"}
        response = client.put(
            base + "/connection",
            json={"revision": 0, "config": cfg, "api_key": "test-provider-key"},
        )
        assert response.status_code == 200, response.text
        assert "test-provider-key" not in client.get(base + "/connection").text
        assert (
            client.put(base + "/connection", json={"revision": 1, "config": cfg}).status_code == 200
        )
        assert (
            client.put(base + "/connection", json={"revision": 1, "config": cfg}).status_code == 409
        )
        guide = {
            "name": "流程",
            "revision": 0,
            "instructions": "标记完整动作",
            "prompt": "按帧观察",
        }
        assert client.put(base + "/guides", json=guide).json()["revision"] == 1
        assert client.get(base + "/guides").json()[0]["prompt"] == guide["prompt"]
        claimed = client.post(task_path + "/claim")
        assert claimed.status_code == 200, claimed.text
        etag = claimed.headers["etag"]
        human = {
            "label": "放置",
            "annotation_id": "human",
            "tag_id": "local",
            "path": ["local"],
            "start_step": 3,
            "end_step": 4,
        }
        response = client.post(
            task_path + "/revisions",
            json={
                "expected_revision": 0,
                "client_mutation_id": "manual",
                "operations": [],
                "tags": [human],
            },
            headers={"If-Match": etag},
        )
        assert response.status_code == 201, response.text
        command = {
            "guide": "流程",
            "guide_revision": 1,
            "expected_revision": 1,
            "mutation_id": str(uuid4()),
        }
        etag = response.headers["etag"]
        response = client.post(base + "/tasks/task/run", json=command, headers={"If-Match": etag})
        assert response.status_code == 200, response.text
        assert response.json() == {"task_id": "task", "added": 1, "revision": 2}
        assert (
            client.post(
                base + "/tasks/task/run", json=command, headers={"If-Match": etag}
            ).status_code
            == 409
        )
        draft = client.get(task_path + "/draft")
        assert [(t["label"], t["start_step"], t["end_step"]) for t in draft.json()["tags"]] == [
            ("放置", 3, 4),
            ("拿取", 0, 3),
        ]
        command.update(expected_revision=2, mutation_id=str(uuid4()))
        assert (
            client.post(
                base + "/tasks/task/run", json=command, headers={"If-Match": draft.headers["etag"]}
            ).json()["added"]
            == 0
        )
        span["label_id"] = 3
        assert (
            client.post(
                base + "/tasks/task/run", json=command, headers={"If-Match": draft.headers["etag"]}
            ).status_code
            == 422
        )
        assert client.get(task_path + "/draft").json()["revision"] == 2
