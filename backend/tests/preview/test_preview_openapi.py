from pathlib import Path

import yaml

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings


def _static() -> dict:  # type: ignore[type-arg]
    path = Path(__file__).parents[2] / "openapi" / "preview.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_request_accepts_only_a_fixed_profile_identity() -> None:
    request = _static()["components"]["schemas"]["PreviewRequestV1"]

    assert "encoding_profile" not in request["properties"]
    assert request["properties"]["profile_id"]["default"] == (
        "annotation-h264-720p-v1"
    )


def test_create_documents_ready_and_durable_pending_states_without_503() -> None:
    create = _static()["paths"]["/api/v1/previews/sessions"]["post"]

    assert set(create["responses"]) == {"201", "202"}
    assert create["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PreviewDescriptorV1"
    }
    assert create["responses"]["202"]["headers"]["Retry-After"]["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 60,
    }


def test_playlist_bridge_is_public_but_segments_are_not_api_routes() -> None:
    document = _static()
    path = "/api/v1/previews/sessions/{session_id}/media/index.m3u8"
    media = document["paths"][path]["get"]

    assert media["operationId"] == "getPreviewPlaylist"
    assert media["security"] == []
    assert {item["name"] for item in media["parameters"]} == {
        "session_id",
        "expires",
        "sig",
        "organization_id",
        "project_id",
        "region_code",
    }
    assert not any("asset_name" in route for route in document["paths"])


def test_runtime_openapi_matches_async_control_plane_contract() -> None:
    runtime = create_app(
        settings=Settings(environment="test", runtime_backend="memory", _env_file=None)
    ).openapi()
    media = runtime["paths"][
        "/api/v1/previews/sessions/{session_id}/media/index.m3u8"
    ]["get"]
    descriptor = runtime["paths"]["/api/v1/previews/sessions/{session_id}"]["get"]
    create = runtime["paths"]["/api/v1/previews/sessions"]["post"]

    assert media["security"] == []
    assert descriptor["security"] == [{"bearerAuth": []}]
    assert {"201", "202"}.issubset(create["responses"])
    assert "503" not in create["responses"]
    assert create["responses"]["202"]["headers"]["Retry-After"]["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 60,
    }
