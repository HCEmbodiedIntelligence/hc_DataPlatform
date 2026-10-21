"""Pure references shared by tasks and clients, independent of source decoders."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
SourceFormat = Literal["MCAP", "LEROBOT_V3", "CAPTURE_BUNDLE"]
Scope = tuple[str, str, str]  # organization, project, region


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VersionReference(Strict):
    scheme_id: Identifier
    version: int = Field(ge=1)
    content_sha256: Sha256


class Participant(Strict):
    robot_id: Identifier
    mapping_version: int = Field(ge=1)


class SetTaskPlan(Strict):
    expected_revision: int = Field(ge=0)
    scheme: VersionReference
    # Optional legacy mapping pins. Tasks are open to all compatible robots;
    # new clients select only the shared scheme.
    participants: tuple[Participant, ...] = Field(default_factory=tuple, max_length=100)

    @model_validator(mode="after")
    def unique_robots(self) -> SetTaskPlan:
        ids = [p.robot_id for p in self.participants]
        if len(set(ids)) != len(ids):
            raise ValueError("each participating robot must have exactly one mapping")
        return self


class PrepareRecording(Strict):
    expected_plan_revision: int = Field(ge=1)
    recording_id: Identifier
    source_format: SourceFormat


class AddParticipants(Strict):
    expected_revision: int = Field(ge=1)
    participants: tuple[Participant, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_robots(self) -> AddParticipants:
        ids = [p.robot_id for p in self.participants]
        if len(set(ids)) != len(ids):
            raise ValueError("each new participant must have exactly one mapping")
        return self


class CaptureReference(Strict):
    plan_revision: int = Field(ge=1)
    plan_sha256: Sha256
    snapshot_sha256: Sha256
    recording_id: Identifier
