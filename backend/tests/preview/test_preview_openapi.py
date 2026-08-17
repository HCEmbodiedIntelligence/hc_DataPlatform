from pathlib import Path

import yaml


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
