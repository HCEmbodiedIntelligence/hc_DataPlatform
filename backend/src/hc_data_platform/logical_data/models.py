"""Opaque snapshot identity, extensible streams and bounded sample reads."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SnapshotSelection(FrozenModel):
    organization_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)


class LogicalStream(FrozenModel):
    stream_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    value_kind: str | None = None
    sample_count: int = Field(ge=0)
    temporal: bool
    start_ns: str | None = Field(default=None, pattern=r"^[0-9]+$")
    end_ns: str | None = Field(default=None, pattern=r"^[0-9]+$")

    @model_validator(mode="after")
    def time_domain(self) -> LogicalStream:
        if self.temporal:
            if (
                self.start_ns is None
                or self.end_ns is None
                or int(self.end_ns) <= int(self.start_ns)
            ):
                raise ValueError("temporal stream requires its actual half-open time interval")
        elif self.start_ns is not None or self.end_ns is not None:
            raise ValueError("independent samples cannot claim a fabricated time interval")
        return self


class LogicalSnapshot(FrozenModel):
    format: Literal["hc-logical-snapshot/1"] = "hc-logical-snapshot/1"
    selection: SnapshotSelection
    episode_id: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    streams: tuple[LogicalStream, ...]

    @property
    def snapshot_id(self) -> str:
        body = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return "snapshot_" + hashlib.sha256(body.encode()).hexdigest()


class LogicalSnapshotView(FrozenModel):
    snapshot_id: str
    snapshot: LogicalSnapshot


class AnnotationLogicalSnapshot(LogicalSnapshotView):
    task_id: str
    snapshot_token: str = Field(min_length=16, max_length=2048)


class LogicalSample(FrozenModel):
    # valid belongs to this stream, never to the multi-channel frame aggregate.
    sample_id: str
    sample_index: int = Field(ge=0)
    timestamp_ns: str | None = Field(default=None, pattern=r"^[0-9]+$")
    value: Any
    valid: bool
    repeated: bool
    source_timestamps_ns: tuple[str, ...] = ()
    time_error_ns: str | None = None


class LogicalSampleBatch(FrozenModel):
    snapshot_id: str
    stream_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    samples: tuple[LogicalSample, ...]

    @model_validator(mode="after")
    def contiguous_window(self) -> LogicalSampleBatch:
        if self.end < self.start or tuple(s.sample_index for s in self.samples) != tuple(
            range(self.start, self.end)
        ):
            raise ValueError("sample batch must completely cover its selected half-open window")
        return self


class LogicalMediaGrant(FrozenModel):
    """Playback mapping for one frozen stream, without storage dispatch fields.

    Logical sample i maps to media_start_seconds + i / sample_rate_hz.
    Original videos retain their real offset; depth previews retain their
    colorizer metadata. The URL grants existing media and never starts encoding.
    """

    snapshot_id: str
    stream_id: str
    kind: Literal["video", "depth-preview"]
    media_url: str
    content_type: Literal["video/mp4"] = "video/mp4"
    expires_at: datetime
    sample_count: int = Field(gt=0)
    sample_rate_hz: float = Field(gt=0, allow_inf_nan=False)
    media_start_seconds: float = Field(ge=0, allow_inf_nan=False)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    media_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    depth_min_m: float | None = None
    depth_max_m: float | None = None
    colorizer: dict[str, Any] | None = None
