from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from hc_data_platform.ingest.cli import HttpResponse
from hc_data_platform.tools.lerobot_platform_upload import (
    COLLECTION_TASK_ID,
    PLATFORM_REGION_CODE,
    PROJECT_ID,
    ROBOT_ID,
    TOKEN_ENV,
    build_import_arguments,
    platform_login,
    upload,
)


class _LoginHttp:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.request_body: dict[str, str] | None = None

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        assert method == "POST"
        assert url == "https://platform.test/api/v1/auth/sessions"
        assert headers == {"Content-Type": "application/json"}
        self.request_body = json.loads(body or b"{}")
        return self.response


def test_platform_login_returns_opaque_session_token() -> None:
    http = _LoginHttp(
        HttpResponse(
            status=201,
            headers={},
            body=json.dumps({"access_token": "session-token-a"}).encode(),
        )
    )

    token = platform_login(
        api_base_url="https://platform.test/api/v1/",
        username="operator-a",
        password="secret-a",
        http=http,
    )

    assert token == "session-token-a"
    assert http.request_body == {"username": "operator-a", "password": "secret-a"}


def test_import_arguments_use_preconfigured_platform_scope(tmp_path: Path) -> None:
    args = build_import_arguments(
        source_dir=Path(r"E:\dataset\revision"),
        api_base_url="http://127.0.0.1:8088",
        episode_count=10,
        output_dir=tmp_path / "output",
        cache_dir=tmp_path / "cache",
    )

    assert args.project_id == PROJECT_ID
    assert args.collection_task_id == COLLECTION_TASK_ID
    assert args.robot_id == ROBOT_ID
    assert args.region_code == PLATFORM_REGION_CODE
    assert args.all_episodes is True
    assert args.episode_count == 10
    assert args.convert_only is False


def test_upload_exposes_token_only_for_the_import_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    observed: list[str] = []

    def fake_run(_args: object) -> dict[str, object]:
        observed.append(__import__("os").environ[TOKEN_ENV])
        return {"uploaded": ["episode-a"]}

    monkeypatch.setattr("hc_data_platform.tools.lerobot_platform_upload.importer.run", fake_run)

    result = upload(
        source_dir=tmp_path / "source",
        api_base_url="http://127.0.0.1:8000",
        token="session-token-a",
        episode_count=1,
        output_dir=tmp_path / "output",
        cache_dir=tmp_path / "cache",
    )

    assert result == {"uploaded": ["episode-a"]}
    assert observed == ["session-token-a"]
    assert TOKEN_ENV not in __import__("os").environ
