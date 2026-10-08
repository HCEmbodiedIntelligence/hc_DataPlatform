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


class RgbVideoWriter:
    def __init__(
        self,
        path: Path,
        fps: float | int | Fraction,
        shape: Sequence[int],
        *,
        lossless_rgb: bool = False,
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
        self.stream.options = {
            "crf": "0" if lossless_rgb else "18",
            "preset": "veryfast",
            "bf": "0",
            "tune": "zerolatency",
        }
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
