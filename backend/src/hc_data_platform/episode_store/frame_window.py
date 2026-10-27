"""Pure frame/time selection contract; safe to import in Temporal's sandbox."""

from dataclasses import dataclass
from fractions import Fraction
from math import isfinite
from typing import Literal


@dataclass(frozen=True)
class DepthWindow:
    first_source_frame: int
    stop_source_frame: int
    frame_count: int
    frequency_hz: int | float
    target_start_ns: int
    rounding: Literal["floor", "nearest"] = "floor"
    # None retains the original integer-frame-origin contract. Arbitrary cuts
    # freeze their exact source offset instead of rounding the origin first.
    source_start_offset_ns: int | None = None
    source_frames: tuple[int, ...] | None = None
    target_timestamps_ns: tuple[int, ...] | None = None

    def __post_init__(self):
        values = (
            self.first_source_frame,
            self.stop_source_frame,
            self.frame_count,
            self.target_start_ns,
        )
        if (
            any(type(v) is not int for v in values)
            or self.first_source_frame < 0
            or self.stop_source_frame <= self.first_source_frame
            or self.frame_count < 1
            or type(self.frequency_hz) not in {int, float}
            or not isfinite(self.frequency_hz)
            or not 0 < self.frequency_hz <= 1000
            or self.target_start_ns < 0
            or self.rounding not in {"floor", "nearest"}
            or self.source_start_offset_ns is not None
            and (type(self.source_start_offset_ns) is not int or self.source_start_offset_ns < 0)
        ):
            raise ValueError("invalid depth frame window")
        for values in (self.source_frames, self.target_timestamps_ns):
            if values is not None and (len(values) != self.frame_count or any(type(v) is not int or v < 0 for v in values) or any(b <= a for a,b in zip(values,values[1:]))):
                raise ValueError("invalid preserved source frame timeline")
        if self.source_frames is not None and (self.source_frames[0] != self.first_source_frame or self.source_frames[-1] >= self.stop_source_frame):
            raise ValueError("preserved frames exceed source window")

    def source_frame(self, index: int, source_fps: Fraction) -> int:
        if self.source_frames is not None:
            return self.source_frames[index]
        frequency = Fraction(str(self.frequency_hz))
        if self.source_start_offset_ns is None:
            return self.first_source_frame + round(Fraction(index) * source_fps / frequency)
        origin = Fraction(self.source_start_offset_ns, 1_000_000_000) * source_fps
        return round(origin + Fraction(index) * source_fps / frequency)

    def target_time(self, index: int) -> int:
        if self.target_timestamps_ns is not None:
            return self.target_timestamps_ns[index]
        offset = Fraction(index * 1_000_000_000) / Fraction(str(self.frequency_hz))
        return self.target_start_ns + (round(offset) if self.rounding == "nearest" else int(offset))
