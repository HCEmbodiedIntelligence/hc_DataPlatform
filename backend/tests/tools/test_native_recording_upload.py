from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from hc_data_platform.ingest.cli import HttpResponse
from hc_data_platform.tools.native_recording_upload import upload_native_recording
from hc_data_platform.tools.native_unitree_g1_recording import (
    CameraInput,
    PrepareRequest,
    VideoProbe,
    prepare_recording,
)


def _probe(_path: Path) -> VideoProbe:
    return VideoProbe(
        codec="h264",
        fps=30,
        width=640,
        height=480,
        duration_seconds=1,
        time_base_numerator=1,
        time_base_denominator=90_000,
    )


class _FakeHttp:
    def __init__(self) -> None:
        self.command: dict[str, Any] | None = None
        self.put_bodies: dict[str, bytes] = {}
        self.completed: list[str] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        del headers
        if method == "POST" and url.endswith("/uploads"):
            self.command = json.loads(body or b"{}")
            assets = [
                {
                    "asset": {
                        "asset_id": f"asset-{index}",
                        "path": asset["path"],
                        "status": "UPLOADING",
                    },
                    "parts": [],
                }
                for index, asset in enumerate(self.command["assets"])
            ]
            return self._json(201, {"upload": {"upload_id": "upload-a"}, "assets": assets})
        if method == "POST" and url.endswith(":authorize-parts"):
            asset_id = url.split("/assets/")[1].split(":", 1)[0]
            part_number = json.loads(body or b"{}")["part_numbers"][0]
            return self._json(
                200,
                {
                    "asset_id": asset_id,
                    "parts": [
                        {
                            "part_number": part_number,
                            "url": f"https://oss.test/{asset_id}/{part_number}",
                        }
                    ],
                },
            )
        if method == "PUT" and url.startswith("https://oss.test/"):
            self.put_bodies[url] = body or b""
            return HttpResponse(status=200, headers={"ETag": '"etag-a"'}, body=b"")
        if method == "POST" and url.endswith(":complete"):
            self.completed.append(url)
            return self._json(200, {"upload": {"status": "UPLOADING"}, "assets": []})
        if method == "POST" and url.endswith(":commit"):
            return self._json(200, {"data": {"recording_id": "recording-a"}})
        raise AssertionError(f"unexpected request {method} {url}")

    @staticmethod
    def _json(status: int, value: object) -> HttpResponse:
        return HttpResponse(
            status=status,
            headers={"Content-Type": "application/json"},
            body=json.dumps(value).encode(),
        )


def test_uploads_every_native_asset_through_platform_grants(tmp_path: Path) -> None:
    video = tmp_path / "source.mp4"
    video.write_bytes(b"native-mp4")
    sensor = tmp_path / "sensor.mcap"
    sensor.write_bytes(b"native-mcap")
    bundle = prepare_recording(
        PrepareRequest(
            output_dir=tmp_path / "bundle",
            project_id="project-a",
            collection_task_id="task-a",
            robot_id="robot-a",
            device_id="device-a",
            capture_started_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
            telemetry=sensor,
            cameras=(CameraInput(camera_id="front", source=video),),
        ),
        video_probe=_probe,
    )
    http = _FakeHttp()

    result = upload_native_recording(
        bundle,
        project_id="project-a",
        organization_id="org-a",
        region_code="cn-beijing",
        api_base_url="https://platform.test",
        access_token="token-a",
        retry_base_seconds=0,
        sleep=lambda _seconds: None,
        http=http,
    )

    assert result == {"data": {"recording_id": "recording-a"}}
    assert http.command is not None
    assert len(http.put_bodies) == 3
    assert len(http.completed) == 3


def test_native_recording_can_use_organization_robot_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "source-robot.mp4"
    video.write_bytes(b"native-mp4")
    sensor = tmp_path / "sensor-robot.mcap"
    sensor.write_bytes(b"native-mcap")
    bundle = prepare_recording(
        PrepareRequest(
            output_dir=tmp_path / "bundle-robot",
            project_id="project-a",
            collection_task_id="task-a",
            robot_id="robot-a",
            device_id="device-a",
            capture_started_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
            telemetry=sensor,
            cameras=(CameraInput(camera_id="front", source=video),),
        ),
        video_probe=_probe,
    )
    captured: dict[str, Any] = {}

    def fake_robot_upload(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"data": {"raw_source_id": "raw-a"}}

    monkeypatch.setattr(
        "hc_data_platform.tools.native_recording_upload.upload_robot_ingest",
        fake_robot_upload,
    )

    result = upload_native_recording(
        bundle,
        project_id=None,
        organization_id=None,
        region_code=None,
        api_base_url="https://platform.test",
        access_token=None,
        robot_credential="robot-secret",
    )

    assert result == {"data": {"raw_source_id": "raw-a"}}
    assert captured["collection_task_id"] == "task-a"
    assert captured["source_format"] == "CAPTURE_BUNDLE"
    assert captured["capture_mode"] == "CONTINUOUS"
    assert captured["project_id"] is None
    assert captured["organization_id"] is None
    assert len(captured["cameras"]) == 1
