from __future__ import annotations

import json

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.ports import InMemoryObjectStorage
from hc_data_platform.lerobot_imports.models import (
    CommitLeRobotImportV1,
    CompleteLeRobotAssetV1,
    CreateLeRobotImportV1,
    LeRobotSourceFileV1,
)
from hc_data_platform.lerobot_imports.orchestration import build_import_plan
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
        storage.upload_part(asset.multipart_upload_id, 1, bodies[asset.path])
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

    accepted = service.commit(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        import_id=grant.import_id,
        command=CommitLeRobotImportV1(manifest=manifest),
    )

    assert accepted.status == "EPISODES_QUEUED"
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
