from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Sdp = Annotated[str, Field(min_length=1, max_length=131072, pattern=r"^v=0\r?\n")]


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IceServer(Wire):
    urls: str
    username: str | None = None
    credential: str | None = None


class RobotSync(Wire):
    instance_id: UUID
    name: str = Field(min_length=1, max_length=120)
    session_id: UUID | None = None
    answer: Sdp | None = None
    release: bool = False


class Assignment(Wire):
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
