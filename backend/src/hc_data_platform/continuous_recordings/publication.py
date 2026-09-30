"""Prepare recording-owned source facts before publication lineage is sealed."""

from typing import Protocol


class RecordingPublicationLineage(Protocol):
    def prepare_rollout(
        self, *, project_id: str, dataset_id: str, rollout_id: str, source_sha256: str
    ) -> None:
        """Bind verified soft Episodes in the enclosing publication transaction."""
        ...
