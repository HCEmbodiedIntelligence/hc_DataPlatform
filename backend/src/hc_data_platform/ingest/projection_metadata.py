"""Manifest-to-alignment facts, independent of workflow input envelopes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from hc_data_platform.alignment.models import AlignmentInputV1, ModalityKind, ModalityStreamV1

from .models import ManifestPreflightResultV1

AlignmentMetadataBuilder = Callable[[str, str, ManifestPreflightResultV1], AlignmentInputV1]


def build_alignment_metadata(
    rollout_id: str,
    source_sha256: str,
    preflight: ManifestPreflightResultV1,
) -> AlignmentInputV1:
    camera_topics = {camera.topic for camera in preflight.manifest.cameras}
    expected_topics = tuple(sorted(preflight.manifest.actual_topics))
    return AlignmentInputV1(
        rollout_id=rollout_id,
        source_sha256=source_sha256,
        attempt_id=f"automatic-{source_sha256[:24]}",
        start_ns=datetime_ns(preflight.manifest.start_time),
        end_ns=datetime_ns(preflight.manifest.end_time),
        streams={
            topic: ModalityStreamV1(
                kind=modality_kind(topic, camera_topics),
                samples=(),
            )
            for topic in expected_topics
        },
    )


def datetime_ns(value: datetime) -> int:
    utc = value.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = utc - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000


def modality_kind(topic: str, camera_topics: set[str]) -> ModalityKind:
    if topic in camera_topics:
        return ModalityKind.IMAGE
    lowered = topic.lower()
    if "action" in lowered or "command" in lowered:
        return ModalityKind.ACTION
    if "point" in lowered or "lidar" in lowered:
        return ModalityKind.POINT_CLOUD
    return ModalityKind.DISCRETE
