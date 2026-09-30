"""Frozen native-source commit contract, with no Lance/media version reservation.

Only the authorized native processing session produces this internal plan. Every
asset locator is bound to the committed Raw manifest; workers recheck that
manifest before accessing assets. Publication still uses normal package receipts.
"""

from typing import Literal

from pydantic import Field, JsonValue, TypeAdapter, model_validator

from hc_data_platform.alignment.models import AlignmentProfileV1
from hc_data_platform.data_formats.feature_names import feature_names
from hc_data_platform.dataset_registry.models import DatasetId, EpisodeId
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot
from hc_data_platform.lance_catalog.schema import compute_schema_fingerprint

from .bundle_models import FrozenObject
from .models import Digest, FileEntry, FrozenModel, Identifier, PackageInput, digest


class NativeFile(FrozenModel):
    relative_path: str
    object: FrozenObject

    @model_validator(mode="after")
    def valid_file(self):
        FileEntry(
            path=self.relative_path,
            size=self.object.size,
            sha256=self.object.sha256,
            media_type="application/octet-stream",
        )
        if not self.object.key:
            raise ValueError("native asset requires its committed object locator")
        return self


class NativeVideo(FrozenModel):
    source: NativeFile
    feature: dict[str, JsonValue]
    evidence: dict[str, NativeFile] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_evidence(self):
        for relative, file in self.evidence.items():
            FileEntry(
                path=relative,
                size=file.object.size,
                sha256=file.object.sha256,
                media_type="application/octet-stream",
            )
        depth = bool(self.feature.get("info", {}).get("is_depth_map"))
        if depth and not {
            "profile.json",
            "frames.parquet",
            "report.json",
            "validity/flags.parquet",
        }.issubset(self.evidence):
            raise ValueError("LEROBOT_DEPTH_EVIDENCE_REQUIRED: profile, frame mapping and masks")
        if not depth and self.evidence:
            raise ValueError("RGB video cannot claim depth evidence")
        return self


class NativeEpisodePlan(FrozenModel):
    format: Literal["hc-native-episode-plan/1"] = "hc-native-episode-plan/1"
    input: PackageInput
    schema_snapshot: DatasetSchemaSnapshot
    metadata: dict[str, JsonValue]
    alignment_sha256: Digest
    alignment_version: Identifier
    alignment_profile: AlignmentProfileV1
    frame_count: int = Field(gt=0)
    feature_mapping: dict[str, str]
    videos: dict[str, NativeVideo]

    @property
    def configuration_sha256(self):
        value = self.model_dump(mode="json", exclude={"input"})
        value["alignment_profile"]["required_modalities"] = sorted(
            self.alignment_profile.required_modalities
        )
        return digest(value)

    @classmethod
    def freeze(cls, *, input, **facts):
        value = cls.model_construct(input=input, **facts)
        frozen = input.model_copy(update={"configuration_sha256": value.configuration_sha256})
        return cls.model_validate({**value.model_dump(mode="json"), "input": frozen})

    @model_validator(mode="after")
    def coherent(self):
        source, schema = self.input, self.schema_snapshot
        TypeAdapter(DatasetId).validate_python(source.identity.dataset_id)
        TypeAdapter(EpisodeId).validate_python(source.identity.episode_id)
        mapping = feature_names(sorted(schema.fields))
        cameras = {
            key: target
            for key, target in mapping.items()
            if target.startswith("observation.images.")
        }
        depth = {
            key
            for key, video in self.videos.items()
            if video.feature.get("info", {}).get("is_depth_map")
        }
        if (
            source.configuration_sha256 != self.configuration_sha256
            or schema.project_id != source.identity.project_id
            or schema.dataset_id != source.identity.dataset_id
            or schema.schema_snapshot_id != source.schema_snapshot_id
            or schema.fingerprint != compute_schema_fingerprint(schema.fields)
            or schema.frequency_hz != self.alignment_profile.frequency_hz
            or source.processor_version != self.alignment_profile.converter_version
            or cameras != self.feature_mapping
            or set(self.videos) != set(cameras.values())
            or len(source.required_features) != len(set(source.required_features))
            or set(source.required_features) != set(mapping.values())
            or set(source.required_depth_features) != depth
        ):
            raise ValueError("native package plan differs from its frozen schema/input")
        return self

    def validate_bundle(self, alignment, manifest, staging, artifacts):
        type(self).model_validate(self.model_dump(mode="json"))
        source, identity = alignment.source, self.input.identity
        if (
            artifacts
            or source is None
            or source.lerobot is None
            or not source.lerobot.preserve_source_features
            or alignment.storage_format != "lerobot_v3"
            or identity.scope
            != (alignment.organization_id, alignment.project_id, alignment.region_code)
            or identity.dataset_id != alignment.dataset_id
            or identity.scope != (source.organization_id, source.project_id, source.region_code)
            or identity.rollout_id != source.rollout_id
            or self.input.source_sha256 != source.source_sha256
            or self.input.schema_snapshot_id != alignment.schema_snapshot_id
            or self.alignment_profile != alignment.profile
            or manifest.rollout_id != identity.rollout_id
            or manifest.source_sha256 != self.input.source_sha256
            or manifest.converter_version != self.input.processor_version
            or manifest.profile_id != self.alignment_profile.profile_id
            or manifest.frequency_hz != self.schema_snapshot.frequency_hz
            or manifest.row_count != self.frame_count
            or staging.row_count != self.frame_count
            or staging.content_sha256 != self.alignment_sha256
            or staging.alignment_version != self.alignment_version
            or set(staging.original_videos) != set(self.feature_mapping)
        ):
            raise ValueError("native commit differs from the frozen processing receipts")
        for topic, target in self.feature_mapping.items():
            original, file = staging.original_videos[topic], self.videos[target].source.object
            if (original.object_key, original.size_bytes, original.content_sha256) != (
                file.key,
                file.size,
                file.sha256,
            ):
                raise ValueError("native video differs from its alignment source")
            if (
                original.start_seconds != 0
                or original.fps != self.schema_snapshot.frequency_hz
                or abs(original.end_seconds - self.frame_count / original.fps) > 1e-6
            ):
                raise ValueError("LEROBOT_VIDEO_WINDOW_REQUIRED: materialize the aligned window")
