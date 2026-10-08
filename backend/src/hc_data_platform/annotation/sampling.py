"""Deterministic candidate selection owned by annotation, without workflow SDKs."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Protocol


class FrameSelectionSamplerPort(Protocol):
    source_frame_count: int

    def observe_camera(
        self,
        topic: str,
        timestamp_ns: int,
        *,
        luma_mean: float | None,
        perceptual_hash: str | None,
        corrupt: bool,
    ) -> None: ...
    def observe_signal(self, topic: str, timestamp_ns: int, value: object) -> None: ...
    def manifest(
        self, *, source_sha256: str, camera_topics: tuple[str, ...]
    ) -> dict[str, object]: ...


SamplerFactory = Callable[[], FrameSelectionSamplerPort]


class FrameSelectionSampler:
    """Decode-free candidate selection after the one ingest image decode."""

    _UNIFORM_INTERVAL_NS = 500_000_000
    _REFINEMENT_NS = 500_000_000
    _MAX_GROUPS = 10_000

    def __init__(self) -> None:
        self.source_frame_count = 0
        self._last_uniform: dict[str, int] = {}
        self._last_luma: dict[str, float] = {}
        self._last_hash: dict[str, str] = {}
        self._last_signal: dict[str, str] = {}
        self._recent_camera: dict[str, deque[int]] = defaultdict(deque)
        self._refine_until = -1
        self._groups: dict[int, set[str]] = {}

    def observe_camera(
        self,
        topic: str,
        timestamp_ns: int,
        *,
        luma_mean: float | None,
        perceptual_hash: str | None,
        corrupt: bool,
    ) -> None:
        self.source_frame_count += 1
        recent = self._recent_camera[topic]
        recent.append(timestamp_ns)
        while recent and recent[0] < timestamp_ns - self._REFINEMENT_NS:
            recent.popleft()
        last_uniform = self._last_uniform.get(topic)
        if last_uniform is None or timestamp_ns - last_uniform >= self._UNIFORM_INTERVAL_NS:
            self._select(timestamp_ns, "uniform-2fps")
            self._last_uniform[topic] = timestamp_ns
        previous_luma = self._last_luma.get(topic)
        if (
            luma_mean is not None
            and previous_luma is not None
            and abs(luma_mean - previous_luma) >= 18
        ):
            self._select(timestamp_ns, "luma-change")
        if luma_mean is not None:
            self._last_luma[topic] = luma_mean
        previous_hash = self._last_hash.get(topic)
        if (
            perceptual_hash is not None
            and previous_hash is not None
            and _hex_hamming(perceptual_hash, previous_hash) >= 12
        ):
            self._select(timestamp_ns, "perceptual-change")
        if perceptual_hash is not None:
            self._last_hash[topic] = perceptual_hash
        if timestamp_ns <= self._refine_until:
            self._select(timestamp_ns, "event-refinement")
        if corrupt:
            self._select(timestamp_ns, "corrupt-frame-boundary")

    def observe_signal(self, topic: str, timestamp_ns: int, value: object) -> None:
        normalized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        prior = self._last_signal.get(topic)
        self._last_signal[topic] = normalized
        lowered = topic.lower()
        if (
            prior is None
            or prior == normalized
            or not any(marker in lowered for marker in ("action", "joint", "command", "event"))
        ):
            return
        # Signals trigger denser sampling of real camera timestamps. Selecting
        # signal timestamps themselves invents image groups when joints run faster
        # than cameras, and can violate selected_group_count <= source_frame_count.
        for recent in self._recent_camera.values():
            for camera_timestamp in recent:
                self._select(camera_timestamp, "robot-state-change")
        self._refine_until = max(self._refine_until, timestamp_ns + self._REFINEMENT_NS)

    def manifest(self, *, source_sha256: str, camera_topics: tuple[str, ...]) -> dict[str, object]:
        groups = [
            {
                "timestamp_ns": timestamp_ns,
                "camera_set": list(camera_topics),
                "reasons": sorted(reasons),
            }
            for timestamp_ns, reasons in sorted(self._groups.items())
        ]
        return {
            "schema_version": 1,
            "sampling_version": "adaptive-2fps-v2",
            "source_sha256": source_sha256,
            "camera_set": list(camera_topics),
            "source_frame_count": self.source_frame_count,
            "groups": groups,
        }

    def _select(self, timestamp_ns: int, reason: str) -> None:
        if timestamp_ns not in self._groups and len(self._groups) >= self._MAX_GROUPS:
            return
        self._groups.setdefault(timestamp_ns, set()).add(reason)


def _hex_hamming(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return 0
