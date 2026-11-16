"""RGB H.264 encoding independent of recording, Episode and publishing domains.

Default lossy preview and explicitly selected lossless RGB settings retain their
existing encoder options, time grid and packet/mux behavior.
"""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path
from typing import cast

import av
import numpy as np
from av.video.stream import VideoStream
from numpy.typing import NDArray


# Encoder profiles frozen into Episode plans. Legacy names keep the original
# options so plans frozen before an upgrade still reproduce their bytes.
LEGACY_RGB_PROFILE = "openarm-training-h264-crf18-veryfast/1"
# First generation from camera frames: one keyframe per second at 30 fps.
SOURCE_RGB_PROFILE = "openarm-training-h264-crf18-veryfast-g30/2"
# Re-encode of an already lossy stream: crf 12 limits generation loss.
WINDOW_RGB_PROFILE = "openarm-training-h264-crf12-veryfast-g30/2"
RGB_PROFILES = {
    LEGACY_RGB_PROFILE: {},
    SOURCE_RGB_PROFILE: {"keyframe_interval": 30},
    WINDOW_RGB_PROFILE: {"crf": 12, "keyframe_interval": 30, "zerolatency": False},
}


class RgbVideoWriter:
    def __init__(
        self,
        path: Path,
        fps: float | int | Fraction,
        shape: Sequence[int],
        *,
        lossless_rgb: bool = False,
        crf: int | None = None,
        keyframe_interval: int | None = None,
        zerolatency: bool = True,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.container = av.open(str(path), "w")
        self.stream = cast(
            VideoStream,
            self.container.add_stream(
                "libx264rgb" if lossless_rgb else "libx264", rate=Fraction(str(fps))
            ),
        )
        self.stream.height, self.stream.width = shape[:2]
        self.stream.pix_fmt = "rgb24" if lossless_rgb else "yuv420p"
        self.stream.codec_context.thread_count = 1
        # Defaults keep the original options byte for byte; frozen plans and
        # rebuilt media depend on them.
        options = {
            "crf": "0" if lossless_rgb else str(18 if crf is None else crf),
            "preset": "veryfast",
            "bf": "0",
        }
        if zerolatency:
            options["tune"] = "zerolatency"
        if keyframe_interval is not None:
            options["g"] = str(keyframe_interval)
        self.stream.options = options
        self.stream.time_base = 1 / Fraction(str(fps))
        self.fps, self.frames = fps, 0

    def write(self, pixels: NDArray[np.uint8]) -> None:
        frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
        frame.pts = self.frames
        frame.time_base = 1 / Fraction(str(self.fps))
        for packet in self.stream.encode(frame):
            self.container.mux(packet)
        self.frames += 1

    def close(self) -> None:
        for packet in self.stream.encode(None):
            self.container.mux(packet)
        self.container.close()
