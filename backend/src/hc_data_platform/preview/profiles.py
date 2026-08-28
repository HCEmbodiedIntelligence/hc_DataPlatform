from __future__ import annotations

from collections.abc import Iterable, Mapping

from hc_data_platform.core.errors import problem

from .models import EncodingProfileV1

SERVER_PREVIEW_PROFILES: Mapping[str, EncodingProfileV1] = {
    "annotation-h264-720p-v1": EncodingProfileV1(
        name="annotation-h264-720p-v1",
        width=1280,
        height=720,
        video_codec="h264",
        video_bitrate_kbps=2_000,
        segment_duration_seconds=2.0,
        preset="veryfast",
    ),
    "quad-h264-360p-v1": EncodingProfileV1(
        name="quad-h264-360p-v1",
        width=640,
        height=360,
        video_codec="h264",
        video_bitrate_kbps=700,
        segment_duration_seconds=2.0,
        preset="veryfast",
    ),
}


class PreviewProfileCatalog:
    """The single server-side authority for allowed preview variants."""

    def __init__(self, allowed_profile_ids: Iterable[str]) -> None:
        normalized = tuple(dict.fromkeys(item.strip() for item in allowed_profile_ids))
        if not normalized:
            raise ValueError("at least one preview profile must be allowed")
        unknown = sorted(set(normalized).difference(SERVER_PREVIEW_PROFILES))
        if unknown:
            raise ValueError(f"unknown preview profiles: {', '.join(unknown)}")
        self._allowed = frozenset(normalized)

    @property
    def allowed_profile_ids(self) -> frozenset[str]:
        return self._allowed

    def get(self, profile_id: str) -> EncodingProfileV1:
        if profile_id not in self._allowed:
            raise problem(
                status=422,
                code="PREVIEW_PROFILE_NOT_ALLOWED",
                title="Preview profile is not allowed",
                detail="Select one of the server-configured preview profiles.",
            )
        return SERVER_PREVIEW_PROFILES[profile_id]


DEFAULT_PREVIEW_PROFILES = PreviewProfileCatalog(("annotation-h264-720p-v1",))
