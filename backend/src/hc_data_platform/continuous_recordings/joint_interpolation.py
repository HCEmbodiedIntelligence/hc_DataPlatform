"""Interpolate bounded runs on the preserved training timeline."""


def short_gap_fills(timeline, samples, sample_times, nearest):
    """Return sparse per-frame repairs. Only aligned input values can be endpoints."""
    fills = {}
    last = {}
    for right, target in enumerate(timeline):
        pos = nearest(sample_times, target)
        if pos is None:
            continue
        source_stamp, sample = samples[pos]
        for axis, right_value in sample.items():
            previous = last.get(axis)
            last[axis] = (right, right_value, source_stamp)
            if previous is None:
                continue
            left, left_value, left_stamp = previous
            if not 1 <= right - left - 1 <= 2:
                continue
            for frame in range(left + 1, right):
                weight = (timeline[frame] - timeline[left]) / (target - timeline[left])
                value = (1.0 - weight) * left_value + weight * right_value
                evidence = {
                    "source": "linear_interpolation",
                    "left_frame": left, "right_frame": right,
                    "left_source_timestamp_ns": left_stamp, "right_source_timestamp_ns": source_stamp,
                }
                fills.setdefault(frame, {})[axis] = (value, evidence)
    return fills
