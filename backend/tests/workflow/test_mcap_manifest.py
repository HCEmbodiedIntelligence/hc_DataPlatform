import hashlib

import numpy as np
from mcap.writer import Writer

from hc_data_platform.ingest.manifest import parse_manifest_bytes
from hc_data_platform.tools.mcap_manifest import build_manifest, build_quality_profile
from tests.aligned_media.test_mcap_camera_media import png
from tests.workflow.test_mcap_camera import SCHEMA, message


def test_manifest_preserves_bytes_relative_topics_and_explicit_depth_unit(tmp_path):
    path = tmp_path / "raw.mcap"
    with path.open("wb") as stream:
        writer = Writer(stream)
        writer.start()
        schema = writer.register_schema(SCHEMA.name, SCHEMA.encoding, SCHEMA.data)
        channel = writer.register_channel("camera_head/depth", "cdr", schema)
        payload = message(png(np.ones((24, 32), np.uint16)), "16UC1; png").data
        writer.add_message(
            channel, log_time=1700000000000000000, publish_time=1700000000000000000, data=payload
        )
        writer.finish()
    original = path.read_bytes()
    manifest = build_manifest(
        path, project_id="1", task_id="task", robot_id="hc-tj", depth_unit="mm"
    )
    assert path.read_bytes() == original
    assert manifest.sha256 == hashlib.sha256(original).hexdigest()
    assert manifest.cameras[0].depth_unit == "mm"
    assert manifest.actual_topics == ["camera_head/depth"]
    parse_manifest_bytes(manifest.model_dump_json().encode())
    other = build_manifest(path, project_id="1", task_id="task", robot_id="hc-tj")
    assert other.cameras[0].depth_unit is None
    assert other.rollout_id == manifest.rollout_id
    assert build_quality_profile(manifest).profile_id == build_quality_profile(other).profile_id
