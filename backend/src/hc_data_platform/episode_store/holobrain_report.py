"""Reference hc_tj report layout, measured from the normalized MCAP inputs.

The normalized inputs are complete MCAPs without the legacy payload tails.
For selections producing multiple episodes, examined RGB frames are assigned to
the preceding output episode; leading excluded frames belong to the first one.
This counts every candidate once while preserving actual per-episode output sizes.
"""

from bisect import bisect_right
from collections import Counter
from pathlib import Path

import numpy as np

STREAMS = (
    ("depth", "/observation/cameras/head/depth/compressed", 50),
    ("joint_state", "/observation/joint_states", 20),
    ("joint_command", "/action/joint_states", 20),
    ("right_finger_command", "/action/right_hand/joint_states", 150),
    ("left_finger_command", "/action/left_hand/joint_states", 150),
)


class ReferenceConversionReport:
    """Collect source message counts, RGB alignment errors and exported frames."""

    def __init__(self, source):
        from .episode_outcome import outcome_fields

        self.outcome = outcome_fields(source["episode_outcome"]) if "episode_outcome" in source else {}
        self.path = Path(source["path"])
        selected = source["provenance"]["selected_mcap"]
        self.source = {"path": selected["path"], "sha256": selected["sha256"],
                       "size_bytes": selected["size_bytes"]}
        self.messages = Counter()
        self.samples = []
        self.outputs = {}
        self.starts = {}

    def observe(self, stamp, matches):
        errors, reason = {}, None
        # Match the reference's stream order and first-failure accounting.
        for name, topic, _ in STREAMS:
            matched = matches[topic]
            if matched is None:
                reason = name + "_outside_tolerance"
                break
            errors[name] = abs(matched - stamp) / 1_000_000
        self.samples.append((stamp, errors, reason))

    def select(self, stamp, episode_index):
        self.outputs[stamp] = episode_index
        self.starts.setdefault(episode_index, stamp)

    def finish(self):
        if not self.starts:
            return []
        # Count top-level records by seeking, without loading the image payloads.
        size, records, chunks = self.path.stat().st_size, 0, 0
        magic = b"\x89MCAP0\r\n"
        with self.path.open("rb") as stream:
            if stream.read(8) != magic:
                raise ValueError("HOLOBRAIN_REPORT_MCAP_INVALID")
            while stream.tell() < size - 8:
                header = stream.read(9)
                if len(header) != 9:
                    raise ValueError("HOLOBRAIN_REPORT_MCAP_TRUNCATED")
                end = stream.tell() + int.from_bytes(header[1:], "little")
                if end > size - 8:
                    raise ValueError("HOLOBRAIN_REPORT_MCAP_TRUNCATED")
                records += 1
                chunks += header[0] == 6
                stream.seek(end)
            if stream.read(8) != magic:
                raise ValueError("HOLOBRAIN_REPORT_MCAP_TRUNCATED")
        recovery = {"recovered": False, "complete_records": records,
                    "complete_chunks": chunks, "recovery_offset": None, "discarded_bytes": 0}
        episode_indexes, starts = zip(*self.starts.items(), strict=True)
        buckets = {index: [] for index in episode_indexes}
        for sample in self.samples:
            index = episode_indexes[max(0, bisect_right(starts, sample[0]) - 1)]
            buckets[index].append(sample)
        reports = []
        for samples in buckets.values():
            errors = {name: [] for name, _, _ in STREAMS}
            drops, output = Counter(), 0
            for stamp, measured, reason in samples:
                for name, value in measured.items():
                    errors[name].append(value)
                if stamp in self.outputs:
                    output += 1
                else:
                    drops[reason or "excluded_by_selection"] += 1
            statistics = {}
            for name, _, tolerance in STREAMS:
                values = np.asarray(errors[name], dtype=np.float64)
                statistics[name] = {
                    "max": float(values.max()) if len(values) else None,
                    "mean": float(values.mean()) if len(values) else None,
                    "p95": float(np.percentile(values, 95)) if len(values) else None,
                    "tolerance": float(tolerance),
                }
            reports.append({
                **self.outcome,
                "source": self.source, "recovery": recovery, "messages": dict(self.messages),
                "joint_command_zero_tail_messages": 0, "finger_command_trimmed_tail_messages": 0,
                "frames": {"candidate_rgb_frames": len(samples), "output_frames": output,
                           "dropped_frames": len(samples) - output,
                           "dropped_by_reason": dict(drops),
                           "nearest_neighbor_error_ms": statistics},
            })
        return reports
