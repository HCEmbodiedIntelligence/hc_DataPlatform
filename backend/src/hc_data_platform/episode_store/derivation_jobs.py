"""Public command/state for a separate primary Episode, not an export archive."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from hc_data_platform.dataset_registry.models import DatasetIngestViewerTarget

from .derivation_models import TagEpisodePlan
from .models import DataVersionRef, Digest, FrozenModel, digest


class RequestTagEpisode(FrozenModel):
    revision: int = Field(ge=0, strict=True)
    annotation_id: str = Field(min_length=1, max_length=256)
    allow_unreviewed: bool = False
    display_name: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def explicit_name(self):
        if self.display_name is not None and self.display_name != self.display_name.strip():
            raise ValueError("Episode display name cannot have outer whitespace")
        return self


class TagDerivationPlan(FrozenModel):
    format: Literal["hc-tag-derivation-job/1"] = "hc-tag-derivation-job/1"
    episode: TagEpisodePlan
    parent_collection_version_id: str = Field(min_length=1)

    @property
    def job_id(self):
        return "td_" + digest(self.model_dump(mode="json"))


class TagJobState(FrozenModel):
    job_id: str = Field(pattern=r"^td_[0-9a-f]{64}$")
    status: Literal["PENDING", "RUNNING", "READY", "FAILED", "CANCELLED"]
    phase: Literal[
        "queued",
        "verifying",
        "deriving",
        "committing",
        "projecting",
        "annotating",
        "completed",
        "failed",
        "cancelled",
    ]
    generation: int = Field(ge=1)
    attempts: int = Field(ge=0)
    committed_data: DataVersionRef | None = None
    collection_version_id: str | None = None
    viewer_target: DatasetIngestViewerTarget | None = None
    annotation_task_id: str | None = None
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class TagDerivationJob(TagJobState):
    plan: TagDerivationPlan

    @model_validator(mode="after")
    def result_binding(self):
        plan, data = self.plan.episode, self.committed_data
        if (
            self.job_id != self.plan.job_id
            or data is not None
            and (
                data.identity != plan.input.identity
                or data.data_version_id != plan.input.data_version_id
                or data.frame_count != plan.frame_count
            )
        ):
            raise ValueError("Tag job result differs from its frozen identity")
        if (self.collection_version_id is None) != (self.viewer_target is None):
            raise ValueError("Tag product publication must bind collection and viewer together")
        if self.viewer_target is not None and (
            data is None
            or self.viewer_target.dataset_id != data.identity.dataset_id
            or self.viewer_target.episode_id != data.identity.episode_id
            or self.viewer_target.version_id != "version_" + self.collection_version_id
        ):
            raise ValueError("Tag viewer target differs from its committed collection")
        if self.status == "READY" and (
            self.viewer_target is None or self.annotation_task_id is None
        ):
            raise ValueError(
                "Tag job READY requires product publication and its own annotation task"
            )
        return self


class TagDerivationGeneration(FrozenModel):
    expected_generation: int = Field(ge=1, strict=True)


class TagDerivationSelection(FrozenModel):
    parent: DataVersionRef
    parent_collection_version_id: str
    task_id: str
    revision: int = Field(ge=0)
    revision_content_hash: Digest
    annotation_file_sha256: Digest
    review_status: Literal["APPROVED", "UNREVIEWED"]
    annotation_id: str
    start_step: int = Field(ge=0)
    end_step: int = Field(gt=0)
    start_timestamp_ns: str = Field(pattern=r"^[0-9]+$")
    next_parent_timestamp_ns: str | None = Field(pattern=r"^[0-9]+$")
    display_name: str
    new_episode_id: str
    new_data_version_id: str = Field(pattern=r"^dv_[0-9a-f]{64}$")


class TagDerivationView(TagJobState):
    """Browser projection of the command; nanosecond integers remain exact strings."""

    selection: TagDerivationSelection


def tag_job_view(job: TagDerivationJob):
    plan, episode = job.plan, job.plan.episode
    binding = episode.annotation.binding
    return TagDerivationView(
        **job.model_dump(exclude={"plan"}),
        selection=TagDerivationSelection(
            parent=episode.parent,
            parent_collection_version_id=plan.parent_collection_version_id,
            task_id=binding.task_id,
            revision=binding.revision,
            revision_content_hash=binding.revision_content_hash,
            annotation_file_sha256=binding.annotation_file_sha256,
            review_status=episode.annotation.review.status,
            annotation_id=episode.annotation_id,
            start_step=episode.start_step,
            end_step=episode.end_step,
            start_timestamp_ns=str(episode.start_timestamp_ns),
            next_parent_timestamp_ns=None
            if episode.next_parent_timestamp_ns is None
            else str(episode.next_parent_timestamp_ns),
            display_name=episode.display_name,
            new_episode_id=episode.input.identity.episode_id,
            new_data_version_id=episode.input.data_version_id,
        ),
    )
