"""Project the recorder's ROS-clock takeover intervals onto committed Episode rows."""

import json
from bisect import bisect_left
from itertools import pairwise

from .mcap_contracts import JointEditRules


def parse_takeovers(content, recording, *, rules=None):
    document = json.loads(content)
    if (
        not isinstance(document, dict)
        or document.get("schema") != "hc-dagger-takeovers/v1"
        or document.get("clock") != "ros"
        or document.get("tag") != "dagger"
        or document.get("recording") != recording
        or type(document.get("complete")) is not bool
        or type(document.get("recording_start_ns")) is not int
        or not isinstance(document.get("intervals"), list)
    ):
        raise ValueError("MCAP_TAKEOVERS_INVALID: incompatible recorder document or MCAP name")
    ranges = set()
    for interval in document["intervals"]:
        if not isinstance(interval, dict):
            raise ValueError("MCAP_TAKEOVERS_INVALID: expected interval")
        start, end = interval.get("start_ns"), interval.get("end_ns")
        if type(start) is not int or start < document["recording_start_ns"]:
            raise ValueError("MCAP_TAKEOVERS_INVALID: invalid interval start")
        if end is None and not document["complete"]:
            continue  # A live checkpoint cannot establish an end for this intervention.
        if type(end) is not int or end < start:
            raise ValueError("MCAP_TAKEOVERS_INVALID: invalid interval end")
        if end > start:
            ranges.add((start, end))
    ordered = sorted(ranges)
    if isinstance(rules, JointEditRules) and "clean_dagger_intervals" in rules.active:
        return _clean_dagger_intervals(ordered)
    return ordered


def _clean_dagger_intervals(ordered: list[tuple[int, int]]) -> list[tuple[int, int]]:
    second_ns = 1_000_000_000
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if merged and start - merged[-1][1] <= second_ns:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return [(start, end) for start, end in merged if end - start >= second_ns]


def committed_takeover_steps(package):
    import pyarrow.parquet as pq

    from hc_data_platform.episode_store.frame_mapping import SOURCE_MAP

    intervals = package.document("hc/source.json")["mcap"]["takeover_intervals_ns"]
    # This map contains only the slice's retained rows, including after QC compaction.
    with package.file(SOURCE_MAP) as path:
        stamps = pq.read_table(path, columns=["timestamp_ns"]).column(0).to_pylist()
    if len(stamps) != package.receipt.data.frame_count or any(
        later < earlier for earlier, later in pairwise(stamps)
    ):
        raise ValueError("MCAP takeover projection requires ordered committed frame timestamps")
    ranges = set()
    for start, end in intervals:
        first, stop = bisect_left(stamps, start), bisect_left(stamps, end)
        if first < stop:
            ranges.add((first, stop))
    return tuple(sorted(ranges))
