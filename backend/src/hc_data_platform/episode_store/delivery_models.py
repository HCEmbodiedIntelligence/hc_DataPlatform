"""Complete independent packages: no implicit selection of annotations or modalities."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .collection_delivery_models import CollectionDeliveryPlan
from .models import DataVersionRef, Digest, FrozenModel, digest

ArchiveKind = Literal["independent_episode_zip/1", "independent_episode_batch_zip/1"]


class RequestEpisodeDelivery(FrozenModel):
    data: tuple[DataVersionRef, ...] = Field(min_length=1, max_length=100)
    archive_format: ArchiveKind = "independent_episode_zip/1"

    @model_validator(mode="after")
    def selection(self):
        if self.archive_format == "independent_episode_zip/1" and len(self.data) != 1:
            raise ValueError("single Episode ZIP requires exactly one data version")
        identities = [(ref.identity.key, ref.data_version_id) for ref in self.data]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate data version in delivery selection")
        if len({ref.identity.scope for ref in self.data}) != 1:
            raise ValueError("delivery selection must stay in one organization/project/region")
        return self


class EpisodeDeliveryMember(FrozenModel):
    data: DataVersionRef
    manifest_sha256: Digest
    root: str = Field(pattern=r"^(episode|episodes/[0-9]{6})$")
    file_count: int = Field(ge=3)
    byte_count: int = Field(gt=0)


class EpisodeDeliveryPlan(FrozenModel):
    format: Literal["hc-episode-delivery-plan/1"] = "hc-episode-delivery-plan/1"
    members: tuple[EpisodeDeliveryMember, ...] = Field(min_length=1, max_length=100)
    archive_format: ArchiveKind
    modalities: Literal["all_collected"] = "all_collected"
    annotation_selection: Literal["package_contents_only"] = "package_contents_only"
    processor_version: Literal["hc-episode-archive/1"] = "hc-episode-archive/1"
    reader_version: Literal["lerobot/0.6.1"] = "lerobot/0.6.1"

    @model_validator(mode="after")
    def members_match_format(self):
        RequestEpisodeDelivery(
            data=tuple(m.data for m in self.members), archive_format=self.archive_format
        )
        if [m.data.data_version_id for m in self.members] != sorted(
            m.data.data_version_id for m in self.members
        ):
            raise ValueError("delivery members must have deterministic version ordering")
        for index, member in enumerate(self.members):
            expected = (
                "episode"
                if self.archive_format == "independent_episode_zip/1"
                else f"episodes/{index:06d}"
            )
            if member.root != expected:
                raise ValueError("delivery member root differs from the selected archive layout")
        return self

    @property
    def job_id(self):
        return "ed_" + digest(self.model_dump(mode="json"))


class EpisodeDeliveryJob(FrozenModel):
    job_id: str = Field(pattern=r"^ed_[0-9a-f]{64}$")
    plan: EpisodeDeliveryPlan | CollectionDeliveryPlan = Field(discriminator="format")
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
    processed_files: int = Field(ge=0)
    processed_bytes: int = Field(ge=0)
    total_files: int | None = Field(gt=0)
    total_bytes: int | None = Field(gt=0)
    uploaded_bytes: int = Field(ge=0)
    artifact_sha256: Digest | None = None
    size_bytes: int | None = Field(default=None, gt=0)
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def archive_totals(self):
        if (self.total_files is None) != (self.total_bytes is None):
            raise ValueError("archive totals must become known together")
        if self.total_files is None and (
            isinstance(self.plan, EpisodeDeliveryPlan)
            or self.status == "READY"
            or self.processed_files
            or self.processed_bytes
            or self.artifact_sha256 is not None
        ):
            raise ValueError("only an unbuilt collection may have unknown archive totals")
        return self


class EpisodeDeliveryGeneration(FrozenModel):
    expected_generation: int = Field(ge=1, strict=True)


class EpisodeDeliveryDownload(FrozenModel):
    job_id: str
    artifact_sha256: Digest
    size_bytes: int = Field(gt=0)
    url: str
    expires_seconds: Literal[300] = 300
