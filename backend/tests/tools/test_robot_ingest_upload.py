from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hc_data_platform.ingest.cli import HttpResponse
from hc_data_platform.tools.robot_ingest_upload import (
    RobotUploadAssetInput,
    upload_robot_ingest,
)


class _RobotUploadHttp:
    def __init__(self, *, asset_id: str) -> None:
        self.asset_id = asset_id
        self.manifests: list[dict[str, Any]] = []
        self.uploaded = {1: "etag-existing"}
        self.put_parts: list[int] = []
        self.completion: dict[str, Any] | None = None
        self.committed = False

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        if method == "POST" and url.endswith("/robot-ingest/uploads"):
            assert headers is not None
            assert headers["Authorization"] == "Bearer robot-secret"
            manifest = json.loads(body or b"{}")
            self.manifests.append(manifest)
            return self._json(
                200 if self.committed else 201,
                {
                    "data": {
                        "upload_id": "riu-a",
                        "state": "COMMITTED" if self.committed else "UPLOADING",
                        "assets": [
                            {
                                "asset_id": self.asset_id,
                                "state": "COMPLETED" if self.committed else "UPLOADING",
                            }
                        ],
                    },
                    "resumed": len(self.manifests) > 1,
                },
            )
        if method == "POST" and url.endswith(":authorize-parts"):
            part_number = int(json.loads(body or b"{}")["part_numbers"][0])
            return self._json(
                200,
                {
                    "upload_id": "riu-a",
                    "asset_id": self.asset_id,
                    "uploaded_parts": [
                        {
                            "part_number": number,
                            "etag": etag,
                            "size_bytes": 1,
                            "crc64": None,
                        }
                        for number, etag in sorted(self.uploaded.items())
                    ],
                    "authorizations": (
                        []
                        if part_number in self.uploaded
                        else [
                            {
                                "part_number": part_number,
                                "url": f"https://oss.test/{part_number}",
                                "expires_at": "2026-09-01T12:00:00Z",
                            }
                        ]
                    ),
                },
            )
        if method == "PUT" and url.startswith("https://oss.test/"):
            part_number = int(url.rsplit("/", 1)[1])
            self.put_parts.append(part_number)
            self.uploaded[part_number] = f"etag-{part_number}"
            return HttpResponse(
                status=200,
                headers={"ETag": f'"etag-{part_number}"'},
                body=b"",
            )
        if method == "POST" and url.endswith(":complete"):
            self.completion = json.loads(body or b"{}")
            return self._json(200, {"data": {"state": "READY_TO_COMMIT"}})
        if method == "POST" and url.endswith(":commit"):
            self.committed = True
            return self._json(
                200,
                {
                    "data": {
                        "upload_id": "riu-a",
                        "state": "COMMITTED",
                        "raw_source_id": "raw-a",
                    },
                    "resumed": False,
                },
            )
        raise AssertionError(f"unexpected robot upload request: {method} {url}")

    @staticmethod
    def _json(status: int, value: object) -> HttpResponse:
        return HttpResponse(
            status=status,
            headers={"Content-Type": "application/json"},
            body=json.dumps(value).encode(),
        )


def test_robot_client_resumes_from_storage_parts_and_reuses_client_uuid(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "capture.mcap"
    raw.write_bytes(b"a" * (5 * 1024**2 + 17))
    asset_id = "asset-" + hashlib.sha256(b"capture.mcap").hexdigest()[:24]
    http = _RobotUploadHttp(asset_id=asset_id)
    state = tmp_path / "resume.json"

    first = upload_robot_ingest(
        assets=(RobotUploadAssetInput(path="capture.mcap", local_path=raw),),
        collection_task_id="task-a",
        robot_id="robot-a",
        source_format="MCAP",
        source_format_version="1.0",
        capture_mode="CONTINUOUS",
        capture_started_at="2026-09-01T00:00:00Z",
        capture_ended_at="2026-09-01T02:00:00Z",
        api_base_url="https://platform.test",
        robot_credential="robot-secret",
        state_path=state,
        part_size=5 * 1024**2,
        retry_base_seconds=0.001,
        sleep=lambda _seconds: None,
        http=http,
    )
    second = upload_robot_ingest(
        assets=(RobotUploadAssetInput(path="capture.mcap", local_path=raw),),
        collection_task_id="task-a",
        robot_id="robot-a",
        source_format="MCAP",
        source_format_version="1.0",
        capture_mode="CONTINUOUS",
        capture_started_at="2026-09-01T00:00:00Z",
        capture_ended_at="2026-09-01T02:00:00Z",
        api_base_url="https://platform.test",
        robot_credential="robot-secret",
        state_path=state,
        part_size=5 * 1024**2,
        retry_base_seconds=0.001,
        sleep=lambda _seconds: None,
        http=http,
    )

    assert first["data"]["raw_source_id"] == "raw-a"
    assert second["resumed"] is True
    assert http.put_parts == [2]
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert http.completion == {
        "parts": [
            {"part_number": 1, "etag": "etag-existing"},
            {"part_number": 2, "etag": "etag-2"},
        ]
    }
    assert http.manifests[0]["client_upload_id"] == http.manifests[1]["client_upload_id"]
    assert "organization_id" not in http.manifests[0]
    assert "project_id" not in http.manifests[0]
    assert isinstance(http.manifests[0]["assets"][0]["crc64"], str)
    assert "robot-secret" not in state.read_text(encoding="utf-8")
