from pathlib import Path

import yaml

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings


def test_preview_openapi_exposes_profile_mapping_and_invalid_placeholders() -> None:
    path = Path(__file__).parents[2] / "openapi" / "preview.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    schemas = document["components"]["schemas"]

    request = schemas["PreviewRequestV1"]
    descriptor = schemas["PreviewDescriptorV1"]
    placeholder = schemas["PlaceholderDescriptorV1"]
    timeline = schemas["TimelineSegmentV1"]

    assert request["properties"]["encoding_profile"] == {
        "$ref": "#/components/schemas/EncodingProfileV1"
    }
    assert descriptor["properties"]["timeline"] == {
        "$ref": "#/components/schemas/TimelineMappingV1"
    }
    assert descriptor["properties"]["placeholders"]["items"] == {
        "$ref": "#/components/schemas/PlaceholderDescriptorV1"
    }
    assert "invalid_reason" in placeholder["required"]
    assert {
        "playback_start_seconds",
        "playback_end_seconds",
        "source_start_step",
        "source_end_step",
    }.issubset(timeline["required"])


def test_preview_openapi_exposes_unsigned_hls_capability_endpoint() -> None:
    path = Path(__file__).parents[2] / "openapi" / "preview.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    media = document["paths"]["/api/v1/previews/sessions/{session_id}/media/{asset_name}"]["get"]

    assert media["operationId"] == "getPreviewMedia"
    assert media["security"] == []
    assert {item["name"] for item in media["parameters"]} == {
        "session_id",
        "asset_name",
        "expires",
        "sig",
    }
    assert {"200", "206", "403", "404"}.issubset(media["responses"])
    assert "video/iso.segment" in media["responses"]["200"]["content"]


def test_runtime_openapi_marks_only_media_capability_as_public() -> None:
    runtime = create_app(
        settings=Settings(environment="test", runtime_backend="memory", _env_file=None)
    ).openapi()
    media = runtime["paths"]["/api/v1/previews/sessions/{session_id}/media/{asset_name}"]["get"]
    descriptor = runtime["paths"]["/api/v1/previews/sessions/{session_id}"]["get"]

    assert media["security"] == []
    assert descriptor["security"] == [{"bearerAuth": []}]
    assert media["responses"]["206"]["headers"]["Accept-Ranges"]["schema"] == {
        "type": "string",
        "const": "bytes",
    }
