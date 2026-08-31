"""Long-running capture bundles and manual episode slicing."""

from .models import (
    ContinuousRecording,
    EpisodeSlice,
    RecordingSliceRevision,
)

__all__ = [
    "ContinuousRecording",
    "ContinuousRecordingService",
    "EpisodeSlice",
    "RecordingSliceRevision",
]


def __getattr__(name: str) -> object:
    """Keep the service export without loading workflow dependencies at package import."""

    if name == "ContinuousRecordingService":
        from .service import ContinuousRecordingService

        return ContinuousRecordingService
    raise AttributeError(name)
