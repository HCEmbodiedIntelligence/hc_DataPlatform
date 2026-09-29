"""Frozen internal commit input, independent of physical catalog version numbers.

This is produced after alignment/media preparation from authorized source facts.
It is not a public request accepting arbitrary object-store locators.
"""

from typing import Literal

from pydantic import Field, JsonValue, TypeAdapter, model_validator

from hc_data_platform.alignment.models import AlignmentProfileV1
from hc_data_platform.dataset_registry.models import DatasetId, EpisodeId
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot
from hc_data_platform.lance_catalog.schema import compute_schema_fingerprint
from hc_data_platform.publishing.feature_names import feature_names

from .bundle_models import DepthAssetContract, SourceInventory
from .models import Digest, FrozenModel, Identifier, PackageInput, digest


def media_inputs_hash(artifacts):
    # Lease, publication and access timestamps can change during recovery. Only
    # immutable media identities/content belong in the package configuration.
    fields = {
        "scope",
        "artifact_id",
        "artifact_key",
        "dataset_id",
        "dataset_version",
        "rollout_id",
        "camera_id",
        "source_sha256",
        "alignment_version",
        "profile_id",
        "profile_version",
        "media_object_key",
        "objects",
        "content_sha256",
        "timeline",
        "frame_count",
        "width",
        "height",
        "fps",
    }
    return digest(
        [
            item.model_dump(mode="json", include=fields)
            for item in sorted(artifacts, key=lambda item: item.camera_id)
        ]
    )


class EpisodeBundlePlan(FrozenModel):
    format: Literal["hc-episode-bundle-plan/1"] = "hc-episode-bundle-plan/1"
    input: PackageInput
    schema_snapshot: DatasetSchemaSnapshot
    metadata: dict[str, JsonValue]
    alignment_sha256: Digest
    alignment_version: Identifier
    alignment_profile: AlignmentProfileV1
    frame_count: int = Field(gt=0)
    media_inputs_sha256: Digest
    feature_mapping: dict[str, str]
    depth_contracts: dict[str, DepthAssetContract] = Field(default_factory=dict)
    inventory: SourceInventory | None = None

    @property
    def configuration_sha256(self):
        configuration = self.model_dump(mode="json", exclude={"input"})
        configuration["alignment_profile"]["required_modalities"] = sorted(
            self.alignment_profile.required_modalities
        )
        return digest(configuration)

    @classmethod
    def freeze(cls, *, input, artifacts, **facts):
        """Bind derived receipts/configuration to the stable package version key."""
        provisional = cls.model_construct(
            input=input, media_inputs_sha256=media_inputs_hash(artifacts), **facts
        )
        frozen = input.model_copy(update={"configuration_sha256": provisional.configuration_sha256})
        return cls.model_validate({**provisional.model_dump(mode="json"), "input": frozen})

    @model_validator(mode="after")
    def coherent_inputs(self):
        source, schema = self.input, self.schema_snapshot
        # The product projection is part of this activity; reject unusable target
        # IDs before publishing a complete package that cannot be projected.
        TypeAdapter(DatasetId).validate_python(source.identity.dataset_id)
        TypeAdapter(EpisodeId).validate_python(source.identity.episode_id)
        if (
            source.configuration_sha256 != self.configuration_sha256
            or schema.project_id != source.identity.project_id
            or schema.dataset_id != source.identity.dataset_id
            or schema.schema_snapshot_id != source.schema_snapshot_id
            or schema.fingerprint != compute_schema_fingerprint(schema.fields)
        ):
            raise ValueError("Episode bundle configuration/schema differs from frozen input")
        if (
            len(set(source.required_features)) != len(source.required_features)
            or len(set(self.feature_mapping.values())) != len(self.feature_mapping)
            or not set(self.feature_mapping).issubset(schema.fields)
            or not set(self.feature_mapping.values()).issubset(source.required_features)
            or set(self.depth_contracts) != set(source.required_depth_features)
            or not set(self.depth_contracts).issubset(self.feature_mapping.values())
        ):
            raise ValueError("Episode bundle feature inventory differs from frozen input")
        if self.inventory is not None and (
            self.inventory.identity != source.identity
            or self.inventory.source_sha256 != source.source_sha256
        ):
            raise ValueError("Episode bundle source inventory belongs to another input")
        if self.depth_contracts and self.inventory is None:
            raise ValueError("Episode depth requires an authorized source evidence inventory")
        return self

    def validate_bundle(self, alignment, staged_manifest, staging, artifacts):
        # Revalidate even worker-local models: frozen Pydantic models may contain
        # mutable dictionaries and model_copy deliberately bypasses validation.
        type(self).model_validate(self.model_dump(mode="json"))
        source, identity = alignment.source, self.input.identity
        if (
            source is None
            or identity.scope
            != (alignment.organization_id, alignment.project_id, alignment.region_code)
            or identity.dataset_id != alignment.dataset_id
            or source.organization_id != identity.organization_id
            or source.project_id != identity.project_id
            or source.region_code != identity.region_code
            or source.rollout_id != identity.rollout_id
            or source.source_sha256 != self.input.source_sha256
            or alignment.schema_snapshot_id != self.input.schema_snapshot_id
            or alignment.profile.frequency_hz != self.schema_snapshot.frequency_hz
            or alignment.profile != self.alignment_profile
            or alignment.profile.converter_version != self.input.processor_version
            or staged_manifest.rollout_id != identity.rollout_id
            or staged_manifest.source_sha256 != self.input.source_sha256
            or staged_manifest.converter_version != self.input.processor_version
            or staged_manifest.profile_id != self.alignment_profile.profile_id
            or staged_manifest.frequency_hz != self.schema_snapshot.frequency_hz
            or staged_manifest.row_count != self.frame_count
            or staging.row_count != self.frame_count
            or staging.content_sha256 != self.alignment_sha256
            or staging.alignment_version != self.alignment_version
            or media_inputs_hash(artifacts) != self.media_inputs_sha256
            or {item.camera_id for item in artifacts} != set(self.feature_mapping)
            or any(
                (item.scope.organization_id, item.scope.project_id, item.scope.region_code)
                != identity.scope
                or item.dataset_id != identity.dataset_id
                or item.rollout_id != identity.rollout_id
                or item.source_sha256 != self.input.source_sha256
                for item in artifacts
            )
        ):
            raise ValueError("Episode commit receipts differ from the frozen bundle plan")
        mapping = feature_names(sorted(self.schema_snapshot.fields))
        if (
            set(mapping.values()) != set(self.input.required_features)
            or any(mapping[key] != target for key, target in self.feature_mapping.items())
            or not {
                key for key, target in mapping.items() if target.startswith("observation.images.")
            }.issubset(self.feature_mapping)
        ):
            raise ValueError("Episode feature mapping differs from its frozen logical schema")

    def asset_arguments(self, attempt_id, artifacts):
        return {
            "attempt_id": attempt_id,
            "artifacts": artifacts,
            "feature_mapping": self.feature_mapping,
            "alignment_version": self.alignment_version,
            "frame_count": self.frame_count,
            "depth_contracts": self.depth_contracts,
            "inventory": self.inventory,
        }
