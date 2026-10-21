"""The already aligned OpenArm transport uses frame numbers on a 30 Hz grid.

Rebase by frame number when slicing: subtracting two rounded nanosecond
timestamps is not equivalent to constructing an episode-local integer grid.
Original capture times and command evidence remain in the immutable source.
"""

FREQUENCY = 30
SECOND = 1_000_000_000


def frame_at(offset_ns: int, frequency: int = FREQUENCY) -> int:
    return (offset_ns * frequency + SECOND // 2) // SECOND


def frame_time(frame: int, frequency: int = FREQUENCY) -> int:
    return frame * SECOND // frequency


def slice_frame_time(
    raw_offset_ns: int, start_ns: int, end_ns: int, frequency: int = FREQUENCY
) -> int | None:
    frame = frame_at(raw_offset_ns, frequency)
    # Accept both floor and round representations of this exact frame only.
    if abs(raw_offset_ns - frame_time(frame, frequency)) > 1:
        raise ValueError("OpenArm sensor timestamp is outside its declared frame grid")
    first, last = frame_at(start_ns, frequency), frame_at(end_ns, frequency)
    if not first <= frame < last:
        return None
    return frame_time(frame - first, frequency)
