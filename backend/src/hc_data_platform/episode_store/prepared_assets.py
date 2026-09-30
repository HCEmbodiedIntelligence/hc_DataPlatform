"""Attempt-scoped asset access before package publication.

Only ingestion/processing code constructs this port from its authorized frozen
input and verified attempt outputs. It does not accept public object paths and
does not query or relax the READY-only export authorization service.
"""

from dataclasses import dataclass
from pathlib import Path

from hc_data_platform.data_formats.depth_evidence import file_hash

from .models import EpisodeIdentity, FileEntry
from .postgres import assert_scope


@dataclass(frozen=True)
class PreparedVideo:
    path: Path
    sha256: str
    size: int
    feature: dict
    # Frozen, hashed files in the per-camera evidence root. Required for depth.
    evidence_root: Path | None = None
    evidence_files: tuple[FileEntry, ...] = ()


@dataclass(frozen=True)
class PreparedAssets:
    identity: EpisodeIdentity
    attempt_id: str
    videos: dict[str, PreparedVideo]
    auxiliary_root: Path | None = None
    auxiliary_files: tuple[FileEntry, ...] = ()

    def video(self, identity: EpisodeIdentity, feature: str) -> PreparedVideo:
        assert_scope(identity)
        if self.identity != identity or not self.attempt_id:
            raise PermissionError("asset attempt is not bound to this Episode identity")
        media = self.videos[feature]
        if (
            media.path.is_symlink()
            or media.path.stat().st_size != media.size
            or file_hash(media.path) != media.sha256
        ):
            raise ValueError("prepared media differs from frozen attempt receipt")
        for entry in media.evidence_files:
            if media.evidence_root is None:
                raise ValueError("depth evidence root is missing")
            from .validation import package_file

            path = package_file(media.evidence_root, entry.path)
            if path.stat().st_size != entry.size or file_hash(path) != entry.sha256:
                raise ValueError("prepared depth evidence differs from attempt receipt")
        return media
