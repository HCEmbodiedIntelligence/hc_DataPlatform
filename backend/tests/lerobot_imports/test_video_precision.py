import json
from types import SimpleNamespace

import pytest

from hc_data_platform.lerobot_imports.reader import _validate_video


@pytest.mark.parametrize("count", [247, 246])
def test_fractional_episode_start_uses_integer_pts(monkeypatch, count):
    # 731.033333333 seconds rounds down to 731.033333 in ffprobe's decimal field.
    # The first frame must be included, while an actual missing frame must fail.
    payload = {
        "streams": [{"time_base": "1/15360"}],
        "frames": [
            {"best_effort_timestamp": (21931 + i) * 512, "width": 640, "height": 480}
            for i in range(count)
        ],
    }
    monkeypatch.setattr(
        "hc_data_platform.lerobot_imports.reader.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(payload)),
    )
    video = SimpleNamespace(
        file="unused.mp4", from_timestamp=21931 / 30, to_timestamp=(21931 + 247) / 30
    )
    if count == 247:
        _validate_video(video, {"shape": [480, 640, 3]}, 247, 30)
    else:
        with pytest.raises(ValueError):
            _validate_video(video, {"shape": [480, 640, 3]}, 247, 30)
