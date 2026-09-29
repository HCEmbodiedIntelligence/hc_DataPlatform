"""Frozen collection selections share the existing Episode delivery lifecycle."""

from typing import Literal

from pydantic import Field, model_validator

from hc_data_platform.annotation.delivery_models import (
    AnnotationDeliveryPlan,
    RequestAnnotationDelivery,
)

from .models import DataVersionRef, Digest, FrozenModel, digest


class CollectionAnnotationSelection(RequestAnnotationDelivery):
    task_id: str = Field(min_length=1, max_length=256)
    revision: int = Field(ge=0, strict=True)


class CollectionEpisodeSelection(FrozenModel):
    data: DataVersionRef
    annotation: CollectionAnnotationSelection | None = None


class RequestCollectionDelivery(FrozenModel):
    members: tuple[CollectionEpisodeSelection, ...] = Field(min_length=1, max_length=100)
    modalities: Literal["rgb", "rgbd"]

    @model_validator(mode="after")
    def selection(self):
        data = [m.data for m in self.members]
        if len({d.identity.scope for d in data}) != 1:
            raise ValueError("collection selection requires one tenant/project/region")
        if len({d.identity.key for d in data}) != len(data):
            raise ValueError("select at most one data version per Episode")
        return self

    @property
    def request_sha256(self):
        value = self.model_dump(mode="json")
        value["members"].sort(key=lambda m: m["data"]["data_version_id"])
        return digest(value)


class CollectionDeliveryMember(FrozenModel):
    data: DataVersionRef
    manifest_sha256: Digest
    annotation: AnnotationDeliveryPlan | None = None
    selected_depth_profiles: dict[str, Digest]
    excluded_depth_features: tuple[str, ...]

    @model_validator(mode="after")
    def binding(self):
        if self.annotation is not None and (
            self.annotation.binding.data != self.data
            or self.annotation.parent_manifest_sha256 != self.manifest_sha256
        ):
            raise ValueError("collection annotation differs from its exact parent package")
        return self


class CollectionDeliveryPlan(FrozenModel):
    format: Literal["hc-collection-delivery-plan/1"] = "hc-collection-delivery-plan/1"
    members: tuple[CollectionDeliveryMember, ...] = Field(min_length=1, max_length=100)
    modalities: Literal["rgb", "rgbd"]
    archive_format: Literal["lerobot_collection_zip/1"] = "lerobot_collection_zip/1"
    annotation_selection: Literal["per_episode_fixed_selection"] = "per_episode_fixed_selection"
    contract_sha256: Digest
    processor_version: Literal["hc-lerobot-collection/1"] = "hc-lerobot-collection/1"
    archive_version: Literal["hc-collection-archive/1"] = "hc-collection-archive/1"
    reader_version: Literal["lerobot/0.6.1"] = "lerobot/0.6.1"

    @model_validator(mode="after")
    def coherent(self):
        RequestCollectionDelivery(
            members=tuple(CollectionEpisodeSelection(data=m.data) for m in self.members),
            modalities=self.modalities,
        )
        versions = [m.data.data_version_id for m in self.members]
        if versions != sorted(versions):
            raise ValueError("collection data versions require deterministic order")
        for member in self.members:
            if self.modalities == "rgb" and member.selected_depth_profiles:
                raise ValueError("RGB subset cannot select depth")
            if self.modalities == "rgbd" and member.excluded_depth_features:
                raise ValueError("RGB-D collection cannot exclude collected depth")
        if self.modalities == "rgbd" and any(
            m.selected_depth_profiles != self.members[0].selected_depth_profiles
            for m in self.members
        ):
            raise ValueError("collection depth profiles differ")
        return self

    @property
    def job_id(self):
        return "ed_" + digest(self.model_dump(mode="json"))


class EpisodeDeliveryCandidates(FrozenModel):
    dataset_id: str
    collection_version_id: str | None
    version: int | None
    items: tuple[DataVersionRef, ...]
    total: int = Field(ge=0)
    legacy_episode_count: int = Field(ge=0)
    next_offset: int | None
