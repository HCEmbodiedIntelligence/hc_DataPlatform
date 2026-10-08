"""Pure immutable file and review receipts; no service/storage imports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FORMAT: Literal["hc-annotation/1"] = "hc-annotation/1"


class AnnotationRevisionFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    revision: int = Field(ge=0)
    revision_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    format_version: Literal["hc-annotation/1"] = FORMAT
    status: Literal["NOT_REQUESTED", "PENDING", "GENERATING", "READY", "FAILED"]
    generation: int = Field(ge=0)
    attempts: int = Field(ge=0)
    content_sha256: str | None = None
    size_bytes: int | None = None
    error_code: str | None = None
    updated_at: datetime | None = None


class SnapshotIntegrityError(RuntimeError):
    pass


class SnapshotBusy(RuntimeError):
    pass


@dataclass(frozen=True)
class RevisionFileDownload:
    content: bytes
    file: AnnotationRevisionFile
    approved: bool
    review_id: str | None
    submission_id: str | None
    # Internal locator, never serialized in public responses or supplied by a client.
    object_key: str | None = None


class ReviewReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["APPROVED", "UNREVIEWED"]
    review_id: str | None = None
    submission_id: str | None = None

    @model_validator(mode="after")
    def immutable_review_reference(self) -> ReviewReference:
        if self.status == "APPROVED":
            if not self.review_id or not self.submission_id:
                raise ValueError("approved annotation requires its immutable review and submission")
        elif self.review_id is not None or self.submission_id is not None:
            raise ValueError("unreviewed annotation cannot claim approval references")
        return self
