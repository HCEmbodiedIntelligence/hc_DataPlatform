"""Explicit persistence capabilities consumed by the native import pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from hc_data_platform.ingest.models import CollectionJob, Rollout

from .orchestration import LeRobotEpisodeTaskV1


@dataclass(frozen=True)
class NativeStateChange:
    task: LeRobotEpisodeTaskV1
    status: str
    error_code: str | None = None
    ready: bool = False
    derived: Mapping[str, Any] = field(default_factory=dict)


class NativeProcessingState(Protocol):
    def episode_ready(self, task: LeRobotEpisodeTaskV1) -> bool: ...
    def update_state(self, update: NativeStateChange) -> None: ...


class SourceRolloutRegistration(Protocol):
    def register_source_rollout(self, job: CollectionJob, rollout: Rollout) -> None: ...


class NativePlanningQuery(Protocol):
    def targets(self, task: LeRobotEpisodeTaskV1) -> tuple[str, tuple[str, ...]]: ...
