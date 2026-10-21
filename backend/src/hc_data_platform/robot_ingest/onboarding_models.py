"""Organization robot onboarding and commit-time upload metrics."""

from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from .models import (
    CreateRobotIngestIdentity,
    RobotCredentialSummary,
    RobotIdentityEnvelope,
    RobotIngestIdentity,
    StrictModel,
)

ConnectionState = Literal["ONLINE", "OFFLINE", "UNVERIFIED", "UNKNOWN"]
PermissionState = Literal["NOT_ENABLED", "ENABLED", "DISABLED"]
CredentialAvailability = Literal["NOT_ISSUED", "AVAILABLE", "UNAVAILABLE"]
RobotSort = Literal["updated", "name", "communication"]
RobotSegment = Literal["online", "pending", "attention"]


class LegacyRobotSource(StrictModel):
    source_id: str
    project_id: str
    region_code: str
    administrative_state: str


class OnboardingFacts(StrictModel):
    robot_id: str
    display_name: str
    serial_no: str
    lifecycle_status: str
    model_version_id: str | None = None
    updated_at: datetime
    identity: RobotIngestIdentity | None = None
    credentials: tuple[RobotCredentialSummary, ...] = ()
    legacy_sources: tuple[LegacyRobotSource, ...] = ()


class OnboardingFault(StrictModel):
    code: str
    reason: str
    occurred_at: datetime
    action: str


class OnboardingRobot(OnboardingFacts):
    upload_permission: PermissionState
    credential_availability: CredentialAvailability
    connection_state: ConnectionState
    last_communication_at: datetime | None = None
    pending: bool
    faults: tuple[OnboardingFault, ...] = ()


class OnboardingSummary(StrictModel):
    total_count: int
    online_count: int | None
    pending_count: int
    attention_count: int
    unknown_count: int


class OnboardingPage(StrictModel):
    items: tuple[OnboardingRobot, ...]
    total: int
    page: int
    page_size: int
    summary: OnboardingSummary
    observed_at: datetime
    online_timeout_seconds: int


class OnboardRobotCommand(CreateRobotIngestIdentity):
    # For a new instance robot_id is omitted; the creation receipt owns retries.
    robot_id: str = ""
    serial_no: str | None = Field(default=None, min_length=1, max_length=128)
    model_version_id: str | None = Field(default=None, min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=128)
    generate_credential: bool = False

    @model_validator(mode="after")
    def require_device(self) -> "OnboardRobotCommand":
        if not self.robot_id and not (
            self.serial_no and self.display_name and self.model_version_id
        ):
            raise ValueError("A new robot requires name, serial number and published model")
        return self


class OnboardRobotResult(StrictModel):
    robot: OnboardingRobot
    identity: RobotIdentityEnvelope


class RobotHeartbeat(StrictModel):
    activity: Literal["IDLE", "COLLECTING", "UPLOADING"] = "IDLE"


class RobotHeartbeatReceipt(StrictModel):
    robot_id: str
    observed_at: datetime
    online_timeout_seconds: int
    activity_expires_at: datetime | None


class SuccessfulUploadVolume(StrictModel):
    day: date
    timezone: str
    from_inclusive: datetime
    to_exclusive: datetime
    raw_bytes: int
    upload_count: int
