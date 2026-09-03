from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hc_data_platform.tools.hf_unitree_g1_to_mcap import CAMERAS
from hc_data_platform.tools.lerobot_platform_upload import (
    build_native_source,
    build_parser,
    run_interactive,
    upload_native_lerobot,
)


def _write_native_lerobot(root: Path, *, episode_count: int = 1) -> set[str]:
    features = {
        "observation.state.ee_state": {},
        "observation.state.hand_state": {},
        "observation.state.robot_q_current": {},
        "action.ee_action": {},
        "action.hand_cmd": {},
        "action.robot_q_desired": {},
        **{camera.feature_key: {"dtype": "video"} for camera in CAMERAS},
    }
    bodies = {
        "meta/info.json": json.dumps(
            {
                "codebase_version": "v3.0",
                "robot_type": "unitree_g1",
                "total_episodes": episode_count,
                "fps": 30,
                "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
                "video_path": (
                    "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
                ),
                "features": features,
            }
        ).encode(),
        "meta/episodes/chunk-000/file-000.parquet": b"episode",
        "data/chunk-000/file-000.parquet": b"data",
        **{f"videos/{camera.feature_key}/chunk-000/file-000.mp4": b"video" for camera in CAMERAS},
    }
    for relative, body in bodies.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return set(bodies)


def test_native_uploader_preserves_source_paths_for_platform_upload(tmp_path: Path) -> None:
    expected_paths = _write_native_lerobot(tmp_path)

    source = build_native_source(
        tmp_path,
        dataset_id="dataset-a",
        collection_task_id="task-a",
        robot_id="robot-a",
    )

    assert set(source.files) == expected_paths
    assert {item.path for item in source.manifest.files} == expected_paths
    assert source.manifest.episode_count == 1


def test_native_uploader_ignores_hugging_face_cache_for_154_episode_source(
    tmp_path: Path,
) -> None:
    expected_paths = _write_native_lerobot(tmp_path, episode_count=154)
    cache_lock = tmp_path / ".cache/huggingface/download/data/file-000.parquet.lock"
    cache_lock.parent.mkdir(parents=True)
    cache_lock.write_bytes(b"")
    cache_lock.with_suffix(".metadata").write_text("cache metadata", encoding="utf-8")

    source = build_native_source(
        tmp_path,
        dataset_id="dataset-a",
        collection_task_id="task-a",
        robot_id="robot-a",
    )

    assert set(source.files) == expected_paths
    assert source.manifest.episode_count == 154


def test_native_uploader_rejects_unfinished_resumable_download(tmp_path: Path) -> None:
    _write_native_lerobot(tmp_path)
    partial = tmp_path / "videos/observation.images.wrist_left/chunk-000/file-001.mp4.part"
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(b"partial")

    with pytest.raises(ValueError, match=r"download is incomplete.*file-001\.mp4\.part"):
        build_native_source(
            tmp_path,
            dataset_id="dataset-a",
            collection_task_id="task-a",
            robot_id="robot-a",
        )


def test_native_uploader_rejects_incomplete_hugging_face_revision(tmp_path: Path) -> None:
    expected_paths = _write_native_lerobot(tmp_path)
    tree_path = tmp_path / ".cache/huggingface/trees" / f"{tmp_path.name}.json"
    tree_path.parent.mkdir(parents=True)
    inventory = {path: {"size": (tmp_path / path).stat().st_size} for path in expected_paths}
    inventory["README.md"] = {"size": 12}
    tree_path.write_text(json.dumps({"format_version": 1, "files": inventory}), encoding="utf-8")

    with pytest.raises(ValueError, match=r"expects 8 files.*README\.md"):
        build_native_source(
            tmp_path,
            dataset_id="dataset-a",
            collection_task_id="task-a",
            robot_id="robot-a",
        )


def test_native_uploader_exposes_platform_input_only() -> None:
    help_text = build_parser().format_help()

    assert "--organization-id" in help_text
    assert "--oss-uri" not in help_text
    assert "AccessKey" not in help_text


def test_native_uploader_calls_lerobot_api_and_puts_original_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_native_lerobot(tmp_path)
    expected = {path: (tmp_path / path).read_bytes() for path in paths}
    uploaded: dict[str, bytes] = {}
    begin_manifest: dict[str, Any] | None = None

    def fake_json_request(
        _client: object,
        method: str,
        url: str,
        *,
        payload: Any,
        **_kwargs: object,
    ) -> dict[str, Any]:
        nonlocal begin_manifest
        if method == "POST" and url.endswith("/lerobot-imports"):
            begin_manifest = payload
            return {
                "import_id": "a" * 32,
                "assets": [
                    {
                        "path": item["path"],
                        "multipart_upload_id": f"upload-{index}",
                    }
                    for index, item in enumerate(payload["files"])
                ],
            }
        if url.endswith("/assets:authorize-parts"):
            return {
                "path": payload["path"],
                "parts": [
                    {
                        "part_number": payload["part_numbers"][0],
                        "url": f"memory://{payload['path']}",
                    }
                ],
            }
        if url.endswith("/assets:complete"):
            return {"path": payload["path"]}
        if url.endswith(":commit"):
            assert payload == {"manifest": begin_manifest}
            return {"status": "EPISODES_QUEUED", "import_id": "a" * 32}
        raise AssertionError(f"unexpected platform request: {method} {url}")

    def fake_put_part(_client: object, *, url: str, body: bytes, **_kwargs: object) -> None:
        uploaded[url.removeprefix("memory://")] = body

    monkeypatch.setattr(
        "hc_data_platform.tools.lerobot_platform_upload._json_request",
        fake_json_request,
    )
    monkeypatch.setattr(
        "hc_data_platform.tools.lerobot_platform_upload._put_part",
        fake_put_part,
    )

    result = upload_native_lerobot(
        tmp_path,
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        dataset_id="dataset-a",
        collection_task_id="task-a",
        robot_id="robot-a",
        api_base_url="https://platform.test/api/v1",
        access_token="token-a",
    )

    assert result["status"] == "EPISODES_QUEUED"
    assert begin_manifest is not None
    assert set(item["path"] for item in begin_manifest["files"]) == paths
    assert uploaded == expected
    assert all(not path.endswith(".mcap") for path in uploaded)


def test_native_lerobot_can_use_robot_identity_without_user_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _write_native_lerobot(tmp_path)
    captured: dict[str, Any] = {}

    def fake_robot_upload(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"data": {"raw_source_id": "raw-lerobot"}}

    monkeypatch.setattr(
        "hc_data_platform.tools.lerobot_platform_upload.upload_robot_ingest",
        fake_robot_upload,
    )

    result = upload_native_lerobot(
        tmp_path,
        organization_id=None,
        project_id=None,
        region_code=None,
        dataset_id=None,
        collection_task_id="task-a",
        robot_id="robot-a",
        api_base_url="https://platform.test",
        access_token=None,
        robot_credential="robot-secret",
        capture_started_at="2026-09-01T00:00:00Z",
        capture_ended_at="2026-09-01T00:10:00Z",
    )

    assert result == {"data": {"raw_source_id": "raw-lerobot"}}
    assert captured["source_format"] == "LEROBOT_V3"
    assert captured["declared_episode_count"] == 1
    assert captured["organization_id"] is None
    assert captured["project_id"] is None
    assert {asset.path for asset in captured["assets"]} == paths


def test_robot_cli_does_not_prompt_for_or_send_authoritative_task_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_native_lerobot(tmp_path)
    captured: dict[str, Any] = {}

    def fake_upload(_source_dir: Path, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"data": {"upload_id": "riu-cli"}}

    monkeypatch.setenv("HC_ROBOT_INGEST_TOKEN", "robot-secret")
    monkeypatch.setattr(
        "hc_data_platform.tools.lerobot_platform_upload.upload_native_lerobot",
        fake_upload,
    )
    args = build_parser().parse_args(
        [
            "--source-dir",
            str(tmp_path),
            "--api-base-url",
            "https://platform.test",
            "--collection-task-id",
            "task-a",
            "--robot-id",
            "robot-a",
            "--capture-started-at",
            "2026-09-01T00:00:00Z",
            "--capture-ended-at",
            "2026-09-01T00:10:00Z",
        ]
    )

    result = run_interactive(args)

    assert result == {"data": {"upload_id": "riu-cli"}}
    assert captured["organization_id"] is None
    assert captured["project_id"] is None
    assert captured["region_code"] is None
    assert captured["dataset_id"] is None
