"""Real, small camera assets for system tests that previously used URI placeholders."""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from hc_data_platform.publishing.export_assets import ExportAssetsPort, ExportVideoSource
from hc_data_platform.publishing.models import ExportStepV1


def portable_export_source(
    steps: Sequence[ExportStepV1], root: Path, *, tags: list[dict[str, Any]]
) -> tuple[ExportAssetsPort, tuple[ExportStepV1, ...]]:
    count = max(step.step_index for step in steps) + 1
    path = root / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            "16x16",
            "-r",
            "30",
            "-i",
            "pipe:0",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            str(path),
        ],
        input=b"".join(bytes([index % 256]) * 16 * 16 * 3 for index in range(count)),
        check=True,
    )
    body = path.read_bytes()
    assets = Mock(spec=ExportAssetsPort)
    assets.episode_metadata.return_value = {
        "tags": tags,
        "task": "System demonstration",
        "robot_type": "test-robot",
    }
    assets.video_source.side_effect = lambda manifest, rollout, reference: ExportVideoSource(
        reference.object_key, hashlib.sha256(body).hexdigest(), len(body), 16, 16, 0, count / 30
    )
    assets.read_video.return_value = (body,)
    result = []
    for step in steps:
        modalities = dict(step.modalities)
        for name in modalities:
            if not name.startswith("/camera/"):
                continue
            modalities[name] = {
                "schema_version": "aligned-media-frame-ref/v1",
                "camera_id": name,
                "artifact_id": "fixture-camera",
                "object_key": "fixture/source.mp4",
                "frame_index": step.step_index,
                "pts": step.step_index,
                "pts_time_base_numerator": 1,
                "pts_time_base_denominator": 30,
                "timestamp_ns": step.timestamp_ns,
                "valid": True,
                "placeholder": False,
                "repeated": False,
                "dropped": False,
                "alignment_version": "fixture/1",
            }
        result.append(step.model_copy(update={"modalities": modalities}))
    return assets, tuple(result)
