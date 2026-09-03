from __future__ import annotations

import json
from io import BytesIO
from threading import get_ident
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.ports import InMemoryObjectStorage
from hc_data_platform.lerobot_imports.models import (
    AuthorizeLeRobotPartsV1,
    CommitLeRobotImportV1,
    CompleteLeRobotAssetV1,
    CreateLeRobotImportV1,
    LeRobotSourceFileV1,
)
from hc_data_platform.lerobot_imports.orchestration import build_import_plan
from hc_data_platform.lerobot_imports.router import get_service as get_lerobot_service
from hc_data_platform.lerobot_imports.router import router as lerobot_router
from hc_data_platform.lerobot_imports.service import LeRobotWebUploadService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.tools.hf_unitree_g1_to_mcap import CAMERAS


def _auth() -> AuthContext:
    return AuthContext(
        subject_id="uploader-a",
        organization_ids=frozenset({"org-a"}),
        project_ids=frozenset({"project-a"}),
        region_codes=frozenset({"cn-hz"}),
        capabilities=frozenset({"upload.read", "upload.manage"}),
        scope_pairs=frozenset({("project-a", "cn-hz")}),
        organization_scope_triples=frozenset({("org-a", "project-a", "cn-hz")}),
    )


def _info(*, episode_count: int = 1) -> dict[str, object]:
    features = {
        "observation.state.ee_state": {},
        "observation.state.hand_state": {},
        "observation.state.robot_q_current": {},
        "action.ee_action": {},
        "action.hand_cmd": {},
        "action.robot_q_desired": {},
    }
    features.update({camera.feature_key: {"dtype": "video"} for camera in CAMERAS})
    return {
        "codebase_version": "v3.0",
        "robot_type": "unitree_g1",
        "total_episodes": episode_count,
        "fps": 30,
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": ("videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"),
        "features": features,
    }


def _source_files() -> tuple[LeRobotSourceFileV1, ...]:
    paths = (
        "meta/info.json",
        "meta/episodes/chunk-000/file-000.parquet",
        "data/chunk-000/file-000.parquet",
        *(f"videos/{camera.feature_key}/chunk-000/file-000.mp4" for camera in CAMERAS),
    )
    return tuple(LeRobotSourceFileV1(path=path, size=1, part_count=1) for path in paths)


def _app(service: LeRobotWebUploadService) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(ProblemException)
    async def handle_problem(_request: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

    @app.middleware("http")
    async def install_auth(request: Request, call_next: Any) -> Any:
        request.app.state.event_loop_thread_id = get_ident()
        request.state.auth_context = _auth()
        token = bind_request_context(
            RequestContext(
                organization_id="org-a",
                subject_id="uploader-a",
                request_id="test-request-id",
            )
        )
        try:
            return await call_next(request)
        finally:
            reset_request_context(token)

    app.dependency_overrides[get_lerobot_service] = lambda: service
    app.include_router(lerobot_router)
    return app


@pytest.mark.parametrize("episode_count", [154, 10_000])
def test_import_contract_accepts_supported_unitree_episode_counts(episode_count: int) -> None:
    manifest = CreateLeRobotImportV1(
        dataset_id="dataset-a",
        collection_task_id="task-a",
        robot_id="robot-a",
        info=_info(episode_count=episode_count),
        files=_source_files(),
    )

    plan = build_import_plan(
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        dataset_id=manifest.dataset_id,
        collection_task_id=manifest.collection_task_id,
        robot_id=manifest.robot_id,
        raw_upload_id="a" * 32,
        raw_manifest_key="raw/org-a/dataset-a/import-a/manifest.json",
        episode_count=manifest.episode_count,
    )

    assert len(plan.episode_tasks) == episode_count
    assert plan.episode_tasks[-1].source.episode_index == episode_count - 1


def test_import_contract_rejects_more_than_10000_episodes() -> None:
    with pytest.raises(ValueError, match="10000 episodes"):
        CreateLeRobotImportV1(
            dataset_id="dataset-a",
            collection_task_id="task-a",
            robot_id="robot-a",
            info=_info(episode_count=10_001),
            files=_source_files(),
        )


@pytest.mark.parametrize(
    "path",
    [
        ".cache/huggingface/download/data/file-000.parquet.lock",
        "videos/observation.images.wrist_left/chunk-000/file-001.mp4.part",
    ],
)
def test_import_contract_rejects_local_cache_and_partial_downloads(path: str) -> None:
    with pytest.raises(ValueError, match="local cache or incomplete download"):
        CreateLeRobotImportV1(
            dataset_id="dataset-a",
            collection_task_id="task-a",
            robot_id="robot-a",
            info=_info(),
            files=(*_source_files(), LeRobotSourceFileV1(path=path, size=1, part_count=1)),
        )


def test_begin_resumes_the_server_session_and_reports_uploaded_parts() -> None:
    manifest = CreateLeRobotImportV1(
        dataset_id="dataset-a",
        collection_task_id="task-a",
        robot_id="robot-a",
        info=_info(),
        files=_source_files(),
    )
    storage = InMemoryObjectStorage()
    service = LeRobotWebUploadService(storage)

    first = service.begin(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        manifest=manifest,
    )
    first_asset = first.assets[0]
    assert first_asset.multipart_upload_id is not None
    service.upload_part(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        import_id=first.import_id,
        dataset_id=manifest.dataset_id,
        path=first_asset.path,
        multipart_upload_id=first_asset.multipart_upload_id,
        part_number=1,
        body=BytesIO(b"x"),
        size=1,
    )

    resumed_service = LeRobotWebUploadService(storage)
    resumed = resumed_service.begin(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        manifest=manifest.model_copy(update={"files": tuple(reversed(manifest.files))}),
    )

    assert resumed.import_id == first.import_id
    assert {item.path: item.multipart_upload_id for item in resumed.assets} == {
        item.path: item.multipart_upload_id for item in first.assets
    }
    renewed = resumed_service.authorize_parts(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        import_id=resumed.import_id,
        command=AuthorizeLeRobotPartsV1(
            dataset_id=manifest.dataset_id,
            path=first_asset.path,
            multipart_upload_id=first_asset.multipart_upload_id,
            part_numbers=(1,),
        ),
    )
    assert renewed.uploaded_part_numbers == (1,)
    assert renewed.parts == ()

    changed_file = LeRobotSourceFileV1(
        path=manifest.files[0].path,
        size=2,
        part_count=1,
    )
    changed_manifest = manifest.model_copy(update={"files": (changed_file, *manifest.files[1:])})
    with pytest.raises(ProblemException) as captured:
        service.begin(
            auth=_auth(),
            organization_id="org-a",
            project_id="project-a",
            region_code="cn-hz",
            manifest=changed_manifest,
        )
    assert captured.value.problem.code == "LEROBOT_IMPORT_MANIFEST_CHANGED"

    with pytest.raises(ProblemException) as changed_binding:
        service.begin(
            auth=_auth(),
            organization_id="org-a",
            project_id="project-a",
            region_code="cn-hz",
            manifest=manifest.model_copy(update={"robot_id": "robot-b"}),
        )
    assert changed_binding.value.problem.code == "LEROBOT_IMPORT_MANIFEST_CHANGED"


def test_browser_upload_preserves_original_lerobot_bytes_and_paths() -> None:
    info_bytes = json.dumps(_info(), ensure_ascii=False, indent=2).encode()
    bodies = {
        "meta/info.json": info_bytes,
        "meta/tasks.jsonl": b'{"task_index":0,"task":"fold"}\n',
        "meta/episodes/chunk-000/file-000.parquet": b"episode-parquet",
        "data/chunk-000/file-000.parquet": b"data-parquet",
        **{
            f"videos/{camera.feature_key}/chunk-000/file-000.mp4": (
                f"video-{camera.feature_key}".encode()
            )
            for camera in CAMERAS
        },
    }
    manifest = CreateLeRobotImportV1(
        dataset_id="dataset-a",
        collection_task_id="task-a",
        robot_id="robot-a",
        info=_info(),
        files=tuple(
            LeRobotSourceFileV1(path=path, size=len(body), part_count=1)
            for path, body in bodies.items()
        ),
    )
    storage = InMemoryObjectStorage()
    service = LeRobotWebUploadService(storage)

    grant = service.begin(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        manifest=manifest,
    )
    for asset in grant.assets:
        assert asset.multipart_upload_id is not None
        body = bodies[asset.path]
        uploaded = service.upload_part(
            auth=_auth(),
            organization_id="org-a",
            project_id="project-a",
            region_code="cn-hz",
            import_id=grant.import_id,
            dataset_id="dataset-a",
            path=asset.path,
            multipart_upload_id=asset.multipart_upload_id,
            part_number=1,
            body=BytesIO(body),
            size=len(body),
        )
        assert uploaded.size == len(body)
        service.complete_asset(
            auth=_auth(),
            organization_id="org-a",
            project_id="project-a",
            region_code="cn-hz",
            import_id=grant.import_id,
            command=CompleteLeRobotAssetV1(
                dataset_id="dataset-a",
                path=asset.path,
                multipart_upload_id=asset.multipart_upload_id,
                size=len(bodies[asset.path]),
                part_count=1,
            ),
        )
        resumed = service.authorize_parts(
            auth=_auth(),
            organization_id="org-a",
            project_id="project-a",
            region_code="cn-hz",
            import_id=grant.import_id,
            command=AuthorizeLeRobotPartsV1(
                dataset_id="dataset-a",
                path=asset.path,
                multipart_upload_id=asset.multipart_upload_id,
                part_numbers=(1,),
            ),
        )
        assert resumed.completed is True
        assert resumed.parts == ()

    accepted = service.commit(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        import_id=grant.import_id,
        command=CommitLeRobotImportV1(manifest=manifest),
    )
    accepted_again = service.commit(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        import_id=grant.import_id,
        command=CommitLeRobotImportV1(manifest=manifest),
    )
    resumed_after_commit = service.begin(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        manifest=manifest,
    )

    assert accepted.status == "EPISODES_QUEUED"
    assert accepted_again == accepted
    assert resumed_after_commit.import_id == grant.import_id
    assert all(asset.completed for asset in resumed_after_commit.assets)
    assert accepted.source_file_count == len(bodies)
    assert accepted.episode_task_count == 1
    for relative_path, body in bodies.items():
        key = f"raw/org-a/dataset-a/{grant.import_id}/source/{relative_path}"
        assert storage.objects[key] == body

    raw_manifest_key = f"raw/org-a/dataset-a/{grant.import_id}/manifest.json"
    raw_manifest = json.loads(storage.objects[raw_manifest_key])
    assert raw_manifest["storage_mode"] == "native_objects"
    assert raw_manifest["source_format"] == "lerobot"
    assert len(raw_manifest["content_hash"]) == 64
    assert raw_manifest["file_count"] == len(bodies)
    assert all(item["sha256"] for item in raw_manifest["files"])
    episode_task = json.loads(
        storage.objects[
            f"derived/lerobot-imports/org-a/dataset-a/{grant.import_id}/episodes/000000.json"
        ]
    )
    assert episode_task["source"] == {
        "source_format": "lerobot_v3",
        "raw_upload_id": grant.import_id,
        "raw_manifest_key": raw_manifest_key,
        "episode_index": 0,
    }
    assert not any(key.startswith("lerobot-inbox/") for key in storage.objects)

    raw_source = service.raw_sources.get_source(
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        raw_source_id=grant.import_id,
    )
    assert raw_source is not None
    assert raw_source.source_format.value == "LEROBOT_V3"
    assert raw_source.source_format_version == "v3.0"
    assert raw_source.manifest_key == raw_manifest_key
    assert raw_source.storage_prefix.endswith(f"/{grant.import_id}/source")
    assert raw_source.content_hash == raw_manifest["content_hash"]
    assert raw_source.processing_status.value == "PENDING"
    episodes = service.raw_sources.list_episodes(
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        raw_source_id=grant.import_id,
    )
    assert [(item.source_episode_index, item.status.value) for item in episodes] == [(0, "PENDING")]
    job = service.raw_sources.get_job(
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        raw_source_id=grant.import_id,
    )
    assert job is not None
    assert job.job_type.value == "LEROBOT_IMPORT"
    assert job.adapter_name == "lerobot_v3"
    assert job.status.value == "PENDING"

    changed_manifest = CreateLeRobotImportV1(
        dataset_id=manifest.dataset_id,
        collection_task_id=manifest.collection_task_id,
        robot_id=manifest.robot_id,
        info=manifest.info,
        files=tuple(item for item in manifest.files if item.path != "meta/tasks.jsonl"),
    )
    with pytest.raises(ProblemException) as captured:
        service.commit(
            auth=_auth(),
            organization_id="org-a",
            project_id="project-a",
            region_code="cn-hz",
            import_id=grant.import_id,
            command=CommitLeRobotImportV1(manifest=changed_manifest),
        )
    assert captured.value.problem.code == "LEROBOT_IMPORT_MANIFEST_CHANGED"


def test_same_origin_part_fallback_accepts_binary_body() -> None:
    manifest = CreateLeRobotImportV1(
        dataset_id="dataset-a",
        collection_task_id="task-a",
        robot_id="robot-a",
        info=_info(),
        files=_source_files(),
    )
    storage = InMemoryObjectStorage()
    storage_thread_ids: list[int] = []
    upload_part_stream = storage.upload_part_stream

    def record_upload_thread(*args: Any, **kwargs: Any) -> Any:
        storage_thread_ids.append(get_ident())
        return upload_part_stream(*args, **kwargs)

    storage.upload_part_stream = record_upload_thread  # type: ignore[method-assign]
    app = _app(LeRobotWebUploadService(storage))
    client = TestClient(app)
    headers = {"X-Organization-Id": "org-a", "Authorization": "Bearer test-token"}
    root = "/api/v1/projects/project-a/regions/cn-hz/lerobot-imports"
    created = client.post(root, headers=headers, json=manifest.model_dump(mode="json"))
    assert created.status_code == 201
    grant = created.json()
    asset = grant["assets"][0]

    uploaded = client.put(
        f"{root}/{grant['import_id']}/assets:upload-part",
        headers={**headers, "Content-Type": "application/octet-stream"},
        params={
            "dataset_id": "dataset-a",
            "path": asset["path"],
            "multipart_upload_id": asset["multipart_upload_id"],
            "part_number": 1,
        },
        content=b"x",
    )

    assert uploaded.status_code == 200
    assert uploaded.headers["cache-control"] == "no-store"
    assert len(storage_thread_ids) == 1
    assert storage_thread_ids[0] != app.state.event_loop_thread_id
    key = f"raw/org-a/dataset-a/{grant['import_id']}/source/{asset['path']}"
    assert storage.list_parts(key, asset["multipart_upload_id"])[0].size == 1
