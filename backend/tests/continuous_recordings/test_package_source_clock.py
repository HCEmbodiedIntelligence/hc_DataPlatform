"""A package consumes versioned producer evidence, including the new grid origin."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from hc_data_platform.continuous_recordings.asset_models import (
    RecordingAssetRole, RecordingConfigurationV1,
)
from hc_data_platform.continuous_recordings.package import source_context, timeline


def context(tmp_path, *, version="openarm-raw-platform/9", origin=1_010_000_123):
    config = RecordingConfigurationV1.model_validate({
        "recorder_version": "openarm-session/v2", "primary_clock_domain": "recording_grid",
        "cameras": [{"camera_id": "head", "topic": "observation.images.head", "fps": 30,
                     "width": 16, "height": 16, "codec": "h264", "clock_domain": "recording_grid",
                     "time_base_numerator": 1, "time_base_denominator": 30,
                     "capture_start_offset_ns": "0"}],
    })
    documents = {
        "recording-config.json": config.model_dump(mode="json"),
        "capture-provenance.json": {
            "processor_version": version, "timeline_mode": "primary_camera",
            "source_t0_ns": 1_000_000_000, "source_end_ns": 2_000_000_000,
            "original_manifest_sha256": "a" * 64, "depth_sources": {},
        },
        "source/frames.jsonl": {
            "source_frame": 0, "offset_ns": 0,
            "row": {"export_grid_origin_ns": origin, "export_target_time_ns": origin,
                    "target_time_ns": origin},
        },
    }
    paths = {}
    for name, value in documents.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n")
        paths[name] = path
    request = SimpleNamespace(recording_config=config, start_offset_ns=200_000_000,
        end_offset_ns=1_200_000_000, assets=[
            SimpleNamespace(relative_path="recording-config.json", role=RecordingAssetRole.RECORDING_CONFIG),
            SimpleNamespace(relative_path="source/original/manifest.json", role=RecordingAssetRole.AUXILIARY,
                            content_sha256="a" * 64),
        ])
    upload = SimpleNamespace(command=SimpleNamespace(capture_started_at=datetime.fromtimestamp(1, timezone.utc)))
    return request, upload, paths


def test_package_uses_exact_export_origin_instead_of_record_button_time(tmp_path):
    request, upload, paths = context(tmp_path)
    origin, facts, media = source_context(request, upload, paths)
    assert origin == 1_010_000_123
    assert facts["capture_to_source_grid_offset_ns"] == 10_000_123
    assert timeline(request, origin) == (1_210_000_123, 1_000_000_000)
    assert media == {}


@pytest.mark.parametrize("origin", [True, 999_999_999, 2_000_000_000])
def test_primary_grid_must_be_an_exact_timestamp_inside_the_recording(tmp_path, origin):
    with pytest.raises(ValueError, match="primary-camera grid"):
        source_context(*context(tmp_path, origin=origin))


def test_future_producer_version_requires_its_own_media_contract(tmp_path):
    with pytest.raises(ValueError, match="explicit media contract"):
        source_context(*context(tmp_path, version="openarm-raw-platform/999"))


def test_primary_grid_cannot_be_accepted_under_a_legacy_producer_version(tmp_path):
    with pytest.raises(ValueError, match="unsupported primary-camera"):
        source_context(*context(tmp_path, version="openarm-raw-platform/8"))
