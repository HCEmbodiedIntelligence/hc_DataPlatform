from __future__ import annotations

from collections.abc import Iterable, Mapping

from hc_data_platform.core.errors import problem

from .models import AlignedMediaEncodingProfileV1

SERVER_ALIGNED_MEDIA_PROFILES: Mapping[str, AlignedMediaEncodingProfileV1] = {
    "canonical-depth-mm-hevc-v1": AlignedMediaEncodingProfileV1(
        profile_id="canonical-depth-mm-hevc-v1",
        codec="hevc",
        pixel_format="gray12le",
    ),
    "original-video-reference-v1": AlignedMediaEncodingProfileV1(
        profile_id="original-video-reference-v1",
        codec="source",
    ),
    "canonical-h264-crf20-v1": AlignedMediaEncodingProfileV1(),
    "canonical-h264-1080p-crf20-v1": AlignedMediaEncodingProfileV1(
        profile_id="canonical-h264-1080p-crf20-v1",
        resolution_policy="fit",
        max_width=1920,
        max_height=1080,
    ),
}


class AlignedMediaProfileCatalog:
    def __init__(self, allowed_profile_ids: Iterable[str]) -> None:
        normalized = tuple(dict.fromkeys(value.strip() for value in allowed_profile_ids))
        if not normalized:
            raise ValueError("at least one aligned media profile must be allowed")
        unknown = sorted(set(normalized).difference(SERVER_ALIGNED_MEDIA_PROFILES))
        if unknown:
            raise ValueError(f"unknown aligned media profiles: {', '.join(unknown)}")
        self._allowed = frozenset(
            (*normalized, "original-video-reference-v1", "canonical-depth-mm-hevc-v1")
        )

    @property
    def allowed_profile_ids(self) -> frozenset[str]:
        return self._allowed

    def get(self, profile_id: str) -> AlignedMediaEncodingProfileV1:
        if profile_id not in self._allowed:
            raise problem(
                status=422,
                code="ALIGNED_MEDIA_PROFILE_NOT_ALLOWED",
                title="Aligned media profile is not allowed",
                detail="Select a server-configured aligned media profile.",
            )
        return SERVER_ALIGNED_MEDIA_PROFILES[profile_id]


DEFAULT_ALIGNED_MEDIA_PROFILES = AlignedMediaProfileCatalog(("canonical-h264-crf20-v1",))
