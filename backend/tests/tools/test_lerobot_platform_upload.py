from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hc_data_platform.tools.hf_unitree_g1_to_mcap import CAMERAS
from hc_data_platform.tools.lerobot_platform_upload import (
    build_native_source,
    build_parser,
    upload_native_lerobot,
)


def _write_native_lerobot(root: Path) -> set[str]:
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
                "total_episodes": 1,
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
