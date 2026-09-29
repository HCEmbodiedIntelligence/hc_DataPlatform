"""An explicit Tag interval creates a separate identity, never a parent mutation."""

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from hc_data_platform.annotation.delivery_models import AnnotationDeliveryPlan
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot

from .models import DataVersionRef, FrozenModel, PackageInput, digest

VERSION = "hc-tag-episode/1"


class TagEpisodePlan(FrozenModel):
    format: Literal["hc-tag-episode-plan/1"] = "hc-tag-episode-plan/1"
    processor_version: Literal["hc-tag-episode/1"] = VERSION
    parent: DataVersionRef
    parent_input: PackageInput
    annotation: AnnotationDeliveryPlan
    schema_snapshot: DatasetSchemaSnapshot
    annotation_id: str = Field(min_length=1, max_length=256)
    start_step: int = Field(ge=0, strict=True)
    end_step: int = Field(gt=0, strict=True)
    parent_origin_timestamp_ns: int = Field(ge=0, strict=True)
    start_timestamp_ns: int = Field(ge=0, strict=True)
    next_parent_timestamp_ns: int | None = Field(ge=0, strict=True)
    display_name: str = Field(min_length=1, max_length=256)
    encoding: dict[str, JsonValue]

    @model_validator(mode="after")
    def coherent(self):
        data, schema = self.parent, self.schema_snapshot
        if (
            self.parent_input.identity != data.identity
            or self.parent_input.data_version_id != data.data_version_id
            or self.annotation.binding.data != data
            or self.annotation.parent_manifest_sha256 != data.content_hash
            or self.annotation.binding.track.annotation_ids != (self.annotation_id,)
            or not self.start_step < self.end_step <= data.frame_count
            or self.start_timestamp_ns < self.parent_origin_timestamp_ns
            or (self.next_parent_timestamp_ns is None) != (self.end_step == data.frame_count)
            or self.next_parent_timestamp_ns is not None
            and self.next_parent_timestamp_ns <= self.start_timestamp_ns
            or schema.project_id != data.identity.project_id
            or schema.dataset_id != data.identity.dataset_id
            or schema.schema_snapshot_id != data.schema_snapshot_id
            or self.display_name != self.display_name.strip()
        ):
            raise ValueError("Tag derivation differs from its frozen parent/revision/window")
        return self

    @property
    def frame_count(self):
        return self.end_step - self.start_step

    @property
    def input(self):
        # Approval changes can create new content versions, but not duplicate
        # logical Episodes for the same explicitly selected Tag revision/window.
        identity_hash = digest(
            {
                "binding": self.annotation.binding.model_dump(mode="json"),
                "start_step": self.start_step,
                "end_step": self.end_step,
                "display_name": self.display_name,
            }
        )
        identity = self.parent.identity.model_copy(
            update={
                "episode_id": "episode_derived_" + identity_hash[:32],
                "rollout_id": "derived:" + identity_hash,
            }
        )
        return self.parent_input.model_copy(
            update={
                "identity": identity,
                "processor_version": self.processor_version,
                "configuration_sha256": digest(self.model_dump(mode="json")),
            }
        )
