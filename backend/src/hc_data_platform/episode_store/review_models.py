"""Capture-compliance decisions, independent of automatic QC and annotation review."""

from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .models import DataVersionRef, Digest, FrozenModel, Identifier


class DataReviewTagReference(FrozenModel):
    task_id: Identifier
    revision: int = Field(ge=0)
    revision_content_hash: Digest
    annotation_id: Identifier


class DataReviewLocation(FrozenModel):
    """Zero-based half-open Episode frames, never parent recording frame numbers."""

    start_step: int | None = Field(default=None, ge=0)
    end_step: int | None = Field(default=None, gt=0)
    tag: DataReviewTagReference | None = None
    comment: str = Field(min_length=1, max_length=2000)

    @field_validator("comment")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("review comment cannot be blank")
        return value

    @model_validator(mode="after")
    def located(self):
        if (self.start_step is None) != (self.end_step is None):
            raise ValueError("both frame boundaries are required")
        if self.start_step is not None and self.start_step >= self.end_step:
            raise ValueError("review range must be nonempty and half-open")
        if self.start_step is None and self.tag is None:
            raise ValueError("a range or exact annotation reference is required")
        return self


class SubmitEpisodeDataReview(FrozenModel):
    expected_content_hash: Digest
    expected_sequence: int = Field(ge=0)
    decision: Literal["APPROVED", "RETURNED"]
    comment: str = Field(min_length=1, max_length=4000)
    locations: tuple[DataReviewLocation, ...] = Field(default=(), max_length=100)

    @field_validator("comment")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("review comment cannot be blank")
        return value


class EpisodeDataReview(FrozenModel):
    format: Literal["hc-episode-data-review/1"] = "hc-episode-data-review/1"
    review_id: Identifier
    data: DataVersionRef
    sequence: int = Field(ge=1)
    previous_review_id: Identifier | None
    decision: Literal["APPROVED", "RETURNED"]
    actor_id: Identifier
    created_at: AwareDatetime
    comment: str
    locations: tuple[DataReviewLocation, ...]


class EpisodeDataReviewAction(FrozenModel):
    action: Literal["SUBMIT_DATA_REVIEW", "LINK_ANNOTATION_TAG"]
    allowed: bool
    required_capabilities: tuple[Literal["dataset_version.review", "annotation_task.read"], ...]
    denied_reason: Literal["MISSING_CAPABILITY"] | None


class EpisodeDataReviewPage(FrozenModel):
    data: DataVersionRef
    status: Literal["NOT_REVIEWED", "APPROVED", "RETURNED"]
    current_sequence: int = Field(ge=0)
    latest: EpisodeDataReview | None
    items: tuple[EpisodeDataReview, ...]
    next_before: int | None
    allowed_actions: tuple[EpisodeDataReviewAction, ...] = ()
