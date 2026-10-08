"""Immutable delivery selection, separate from the saved annotation revision."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from hc_data_platform.episode_store.models import Digest, FrozenModel, digest

from .models import AnnotationReview, AnnotationSubmission
from .revision_models import ReviewReference
from .subtasks import SubtaskBinding, SubtaskTrack


class RequestAnnotationDelivery(FrozenModel):
    revision: int | None = Field(default=None, ge=0, strict=True)
    allow_unreviewed: bool = False
    track: SubtaskTrack

    @model_validator(mode="after")
    def explicit_unreviewed_revision(self):
        if self.allow_unreviewed and self.revision is None:
            raise ValueError("unreviewed delivery requires an explicit revision")
        return self


class AnnotationDeliveryPlan(FrozenModel):
    format: Literal["hc-annotation-delivery-plan/1"] = "hc-annotation-delivery-plan/1"
    binding: SubtaskBinding
    annotation_size: int = Field(gt=0, le=64 * 1024**2)
    review: ReviewReference
    approval: AnnotationReview | None = None
    submission: AnnotationSubmission | None = None
    # The parent's manifest binds all media, masks, source mappings and depth profiles.
    parent_manifest_sha256: Digest
    processor_version: Literal["hc-annotated-delivery-queue/1"] = "hc-annotated-delivery-queue/1"
    reader_version: Literal["lerobot/0.6.1"] = "lerobot/0.6.1"
    archive_format: Literal["independent_episode_zip/1"] = "independent_episode_zip/1"
    includes_collected_depth: Literal[True] = True

    @model_validator(mode="after")
    def review_binding(self):
        if self.review.status == "UNREVIEWED":
            if self.approval is not None or self.submission is not None:
                raise ValueError("unreviewed delivery cannot contain an approval")
            return self
        r, s, b = self.approval, self.submission, self.binding
        if (
            r is None
            or s is None
            or r.review_id != self.review.review_id
            or s.submission_id != self.review.submission_id
            or r.submission_id != s.submission_id
            or r.task_id != b.task_id
            or s.task_id != b.task_id
            or r.revision != b.revision
            or s.revision != b.revision
            or r.data_ref != b.data
            or s.data_ref != b.data
            or r.decision.value != "APPROVE"
            or s.revision_content_hash != b.revision_content_hash
        ):
            raise ValueError("approval must bind the exact submission, revision and data")
        return self

    @property
    def job_id(self):
        return "ad_" + digest(self.model_dump(mode="json"))


class AnnotationDeliveryJob(FrozenModel):
    job_id: str
    plan: AnnotationDeliveryPlan
    status: Literal["PENDING", "RUNNING", "READY", "FAILED", "CANCELLED"]
    phase: Literal[
        "queued",
        "verifying",
        "projecting",
        "archiving",
        "uploading",
        "completed",
        "failed",
        "cancelled",
    ]
    generation: int = Field(ge=1)
    attempts: int = Field(ge=0)
    artifact_sha256: Digest | None = None
    size_bytes: int | None = None
    manifest_sha256: Digest | None = None
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class DeliveryGeneration(FrozenModel):
    expected_generation: int = Field(ge=1, strict=True)


class AnnotationDeliveryDownload(FrozenModel):
    job_id: str
    artifact_sha256: Digest
    size_bytes: int
    url: str
    expires_seconds: Literal[300] = 300
