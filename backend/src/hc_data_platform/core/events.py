from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class DomainEventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    schema_version: int = Field(default=1, ge=1)
    aggregate_type: str
    aggregate_id: str
    organization_id: str | None = None
    project_id: str
    region_code: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
