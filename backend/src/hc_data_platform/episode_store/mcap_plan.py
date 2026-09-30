"""Frozen MCAP -> Episode contract; pure metadata, safe for Temporal history."""

from typing import Literal

from pydantic import Field, JsonValue, TypeAdapter, model_validator

from hc_data_platform.alignment.models import AlignmentProfileV1
from hc_data_platform.data_formats.feature_names import feature_names
from hc_data_platform.dataset_registry.models import DatasetId, EpisodeId
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot
from hc_data_platform.lance_catalog.schema import compute_schema_fingerprint

from .bundle_models import FrozenObject
from .models import Digest, FrozenModel, Identifier, PackageInput, digest


class McapCameraContract(FrozenModel):
    kind: Literal["rgb", "depth"]
    shape: tuple[int, ...] = Field(min_length=2, max_length=3)
    source_profiles: dict[Digest, dict[str, JsonValue]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_contract(self):
        depth = self.kind == "depth"
        if (
            len(self.shape) != (2 if depth else 3)
            or any(type(value) is not int or value < 1 for value in self.shape)
            or self.shape[0] * self.shape[1] > 16 * 1024 * 1024
            or not depth
            and self.shape[-1] != 3
            or bool(self.source_profiles) != depth
            or len(self.source_profiles) > 64
            or any(key != digest(value) for key, value in self.source_profiles.items())
        ):
            raise ValueError("MCAP camera layout/source units are incomplete or inconsistent")
        return self


class McapEpisodePlan(FrozenModel):
    format: Literal["hc-mcap-episode-plan/1"] = "hc-mcap-episode-plan/1"
    input: PackageInput
    schema_snapshot: DatasetSchemaSnapshot
    metadata: dict[str, JsonValue]
    source: FrozenObject
    source_locator_sha256: Digest
    alignment_sha256: Digest
    alignment_version: Identifier
    alignment_profile: AlignmentProfileV1
    frame_count: int = Field(gt=0)
    feature_mapping: dict[str, str]
    cameras: dict[str, McapCameraContract]
    encoding: dict[str, JsonValue]

    @property
    def configuration_sha256(self):
        value = self.model_dump(mode="json", exclude={"input"})
        value["alignment_profile"]["required_modalities"] = sorted(
            self.alignment_profile.required_modalities
        )
        return digest(value)

    @classmethod
    def freeze(cls, *, input, **facts):
        facts["cameras"] = {
            feature: McapCameraContract.model_validate(contract)
            for feature, contract in facts["cameras"].items()
        }
        provisional = cls.model_construct(input=input, **facts)
        frozen = input.model_copy(update={"configuration_sha256": provisional.configuration_sha256})
        return cls.model_validate({**provisional.model_dump(mode="json"), "input": frozen})

    @model_validator(mode="after")
    def coherent(self):
        source, schema = self.input, self.schema_snapshot
        TypeAdapter(DatasetId).validate_python(source.identity.dataset_id)
        TypeAdapter(EpisodeId).validate_python(source.identity.episode_id)
        mapping = feature_names(sorted(schema.fields), camera_topics=tuple(self.feature_mapping))
        clock = self.metadata.get("source", {})
        start, end = clock.get("start_ns"), clock.get("end_ns")
        if type(start) is not int or type(end) is not int or not 0 <= start < end:
            raise ValueError("MCAP package requires its exact declared time window")
        if (
            source.configuration_sha256 != self.configuration_sha256
            or schema.project_id != source.identity.project_id
            or schema.dataset_id != source.identity.dataset_id
            or schema.schema_snapshot_id != source.schema_snapshot_id
            or schema.fingerprint != compute_schema_fingerprint(schema.fields)
            or schema.frequency_hz != self.alignment_profile.frequency_hz
            or self.source.sha256 != source.source_sha256
            or self.source.size < 1
            or not self.source.key
            or self.frame_count
            != ((end - start) * self.alignment_profile.frequency_hz + 999_999_999) // 1_000_000_000
            or self.alignment_profile.converter_version != source.processor_version
            or set(self.cameras) != set(self.feature_mapping.values())
            or any(mapping[key] != target for key, target in self.feature_mapping.items())
            or set(source.required_features) != set(mapping.values())
            or set(source.required_depth_features)
            != {key for key, contract in self.cameras.items() if contract.kind == "depth"}
            or self.metadata.get("camera_topics") != sorted(self.feature_mapping)
        ):
            raise ValueError("MCAP package differs from its frozen source/schema contract")
        return self

    def validate_bundle(self, alignment, manifest, staging, artifacts):
        type(self).model_validate(self.model_dump(mode="json"))
        source, identity = alignment.source, self.input.identity
        if (
            source is None
            or source.lerobot is not None
            or artifacts
            or alignment.storage_format != "lerobot_v3"
            or identity.scope
            != (alignment.organization_id, alignment.project_id, alignment.region_code)
            or identity.scope != (source.organization_id, source.project_id, source.region_code)
            or digest(source.model_dump(mode="json")) != self.source_locator_sha256
            or source.object_key != self.source.key
            or source.source_sha256 != self.source.sha256
            or source.rollout_id != identity.rollout_id
            or alignment.dataset_id != identity.dataset_id
            or alignment.schema_snapshot_id != self.input.schema_snapshot_id
            or alignment.profile != self.alignment_profile
            or manifest.rollout_id != identity.rollout_id
            or manifest.source_sha256 != self.input.source_sha256
            or manifest.converter_version != self.input.processor_version
            or manifest.profile_id != self.alignment_profile.profile_id
            or manifest.frequency_hz != self.schema_snapshot.frequency_hz
            or manifest.row_count != self.frame_count
            or staging.row_count != self.frame_count
            or staging.content_sha256 != self.alignment_sha256
            or staging.alignment_version != self.alignment_version
            or staging.original_videos
            or staging.camera_shards
        ):
            raise ValueError("MCAP commit differs from its frozen source/alignment receipts")
