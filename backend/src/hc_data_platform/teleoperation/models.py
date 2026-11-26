from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Sdp = Annotated[str, Field(min_length=1, max_length=131072, pattern=r"^v=0\r?\n")]


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IceServer(Wire):
    urls: str
    username: str | None = None
    credential: str | None = None


class CaptureCommand(Wire):
    request_id: UUID
    session_id: UUID
    project_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    action: Literal["start", "mark", "stop"]
    revision: int = Field(ge=0)


class CaptureReceipt(Wire):
    request_id: UUID
    state: Literal["recording", "saved", "error", "interrupted"]
    record_id: str = Field(default="", max_length=256)
    detail: str = Field(default="", max_length=512)
    upload: Literal["", "pending", "uploaded", "failed"] = ""


class CaptureSlot(Wire):
    owner: str
    instance_id: UUID
    command: CaptureCommand
    expires: float
    delivered: bool = False
    record_id: str = ""
    previous_state: Literal["idle", "recording", "saved", "interrupted"] = "idle"
    receipt: CaptureReceipt | None = None


class CaptureView(Wire):
    supported: bool = False
    revision: int = 0
    eligible: list[str] = Field(default_factory=list)
    state: Literal[
        "idle", "pending", "unknown", "recording", "saved", "interrupted", "error", "busy"
    ] = "idle"
    task_id: str = ""
    record_id: str = ""
    detail: str = ""
    upload: str = ""
    active: bool = False


class RobotSync(Wire):
    instance_id: UUID
    name: str = Field(min_length=1, max_length=120)
    session_id: UUID | None = None
    answer: Sdp | None = None
    release: bool = False
    capture_project: str = Field(default="", max_length=128)
    capture_tasks: list[Annotated[str, Field(min_length=1, max_length=128)]] | None = Field(
        default=None, max_length=512
    )
    capture_result: CaptureReceipt | None = None


class Assignment(Wire):
    capture_command: CaptureCommand | None = None
    session_id: UUID | None = None
    offer: str | None = None
    ice_servers: list[IceServer] = Field(default_factory=list)


class OperatorSync(Wire):
    offer: Sdp | None = None


class OperatorState(Wire):
    answer: str | None = None


class RobotPresence(Wire):
    robot_id: str
    name: str
    last_seen: float
    busy: bool


class Session(Wire):
    id: UUID
    owner: str
    expires: float
    offer: str | None = None
    answer: str | None = None


class Endpoint(Wire):
    instance_id: UUID
    name: str
    seen: float
    session: Session | None = None
    capture_project: str = ""
    capture_tasks: list[str] | None = None
    capture_revision: int = 0
    capture: CaptureSlot | None = None
