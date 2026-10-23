"""Frozen continuous-recording package input, independent of Lance numbering."""

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .frame_window import DepthWindow
from .models import Digest, digest
from .native_plan import NativeEpisodePlan, NativeFile


def recording_request_hash(request):
    document = request.model_dump(mode="json")
    document["quality_profile"]["required_topics"] = sorted(request.quality_profile.required_topics)
    document["alignment_profile"]["required_modalities"] = sorted(
        request.alignment_profile.required_modalities
    )
    return digest(document)


class RecordingEpisodePlan(NativeEpisodePlan):
    format: Literal["hc-recording-episode-plan/1"] = "hc-recording-episode-plan/1"
    request_sha256: Digest
    inventory: tuple[NativeFile, ...] = Field(min_length=3, max_length=4096)
    windows: dict[str, DepthWindow]
    encoding: dict[str, JsonValue]

    @model_validator(mode="after")
    def continuous_inventory(self):
        paths = [item.relative_path for item in self.inventory]
        if paths != sorted(set(paths)) or set(self.windows) != set(self.videos):
            raise ValueError("recording plan requires one frozen window per camera")
        files = {item.relative_path: item for item in self.inventory}
        for feature, video in self.videos.items():
            if any(
                files.get(item.relative_path) != item
                for item in (video.source, *video.evidence.values())
            ):
                raise ValueError("recording camera is outside its committed asset inventory")
            window = self.windows[feature]
            if (
                window.frame_count != self.frame_count
                or window.frequency_hz != self.alignment_profile.frequency_hz
            ):
                raise ValueError("recording camera window differs from canonical Episode grid")
        return self

    def validate_recording_bundle(self, request, alignment, manifest, staging):
        type(self).model_validate(self.model_dump(mode="json"))
        source, identity = request.projection, self.input.identity
        if (
            request.storage_format != "lerobot_v3"
            or request.expected_dataset_version is not None
            or alignment.storage_format != "lerobot_v3"
            or recording_request_hash(request) != self.request_sha256
            or identity.scope != (source.organization_id, source.project_id, source.region_code)
            or identity.scope
            != (alignment.organization_id, alignment.project_id, alignment.region_code)
            or identity.rollout_id != source.rollout_id
            or self.input.source_sha256 != source.source_sha256
            or alignment.dataset_id != identity.dataset_id
            or alignment.schema_snapshot_id != self.input.schema_snapshot_id
            or alignment.profile != self.alignment_profile
            or alignment.data.rollout_id != identity.rollout_id
            or alignment.data.source_sha256 != self.input.source_sha256
            or manifest.rollout_id != identity.rollout_id
            or manifest.source_sha256 != self.input.source_sha256
            or manifest.converter_version != self.input.processor_version
            or manifest.profile_id != self.alignment_profile.profile_id
            or manifest.frequency_hz != self.alignment_profile.frequency_hz
            or manifest.row_count != self.frame_count
            or staging.row_count != self.frame_count
            or staging.content_sha256 != self.alignment_sha256
            or staging.alignment_version != self.alignment_version
            or any(
                window.target_start_ns != alignment.data.start_ns
                for window in self.windows.values()
            )
        ):
            raise ValueError("recording commit differs from its frozen input/alignment receipts")
