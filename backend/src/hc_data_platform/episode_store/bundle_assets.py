"""Internal asset localization from a frozen processing input and media receipts.

The caller has already authorized and verified the immutable source inventory.
No public endpoint accepts these object references. The live scoped media receipt
is checked again here; published-package/export authorization is never relaxed.
"""

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from hc_data_platform.aligned_media.models import (
    AlignedMediaArtifactStatus,
    AlignedMediaScopeV1,
    AlignedMediaSelectorV1,
)

from .models import EpisodeIdentity, FileEntry
from .postgres import assert_scope
from .prepared_assets import PreparedAssets, PreparedVideo


@dataclass(frozen=True)
class FrozenObject:
    key: str
    size: int
    sha256: str


@dataclass(frozen=True)
class SourceInventory:
    identity: EpisodeIdentity
    source_sha256: str
    objects: tuple[FrozenObject, ...]


@dataclass(frozen=True)
class DepthAssetContract:
    feature: dict
    evidence: tuple[tuple[FileEntry, FrozenObject], ...]


class BundleAssetsLoader:
    def __init__(
        self, storage, media_repository, directory: Path, *, max_object_bytes=32 * 1024**3
    ):
        self.storage, self.media_repository = storage, media_repository
        self.directory, self.max_object_bytes = directory, max_object_bytes

    def _copy(self, frozen, target):
        if not 0 < frozen.size <= self.max_object_bytes:
            raise ValueError("prepared asset exceeds the configured object size limit")
        head = self.storage.head(frozen.key)
        if head is None or head.size != frozen.size:
            raise ValueError("prepared asset is missing or its size changed")
        target.parent.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        with target.open("xb") as output:
            for chunk in self.storage.read_chunks(frozen.key):
                size += len(chunk)
                if size > frozen.size:
                    raise ValueError("prepared asset exceeds its frozen size")
                digest.update(chunk)
                output.write(chunk)
        if size != frozen.size or digest.hexdigest() != frozen.sha256:
            raise ValueError("prepared asset hash differs from frozen receipt")

    def _media(self, source, frozen, alignment_version, frame_count):
        identity = source.identity
        scope = AlignedMediaScopeV1(
            organization_id=identity.organization_id,
            project_id=identity.project_id,
            region_code=identity.region_code,
        )
        if (
            frozen.scope != scope
            or frozen.dataset_id != identity.dataset_id
            or frozen.rollout_id != identity.rollout_id
            or frozen.source_sha256 != source.source_sha256
            or frozen.alignment_version != alignment_version
            or frozen.frame_count != frame_count
        ):
            raise PermissionError("prepared media is outside the frozen processing identity")
        current = self.media_repository.find_by_selector(
            scope,
            AlignedMediaSelectorV1(
                project_id=identity.project_id,
                dataset_id=identity.dataset_id,
                rollout_id=identity.rollout_id,
                dataset_version=frozen.dataset_version,
                camera_id=frozen.camera_id,
            ),
            profile_id=frozen.profile_id,
        )
        # Commit lease/version timestamps may change on retry. Media identity and
        # content must remain exactly the ones frozen by the processing attempt.
        immutable = (
            "artifact_id",
            "artifact_key",
            "source_sha256",
            "alignment_version",
            "profile_version",
            "media_object_key",
            "objects",
            "content_sha256",
            "timeline",
            "frame_count",
            "width",
            "height",
            "fps",
        )
        if (
            current is None
            or current.status is not AlignedMediaArtifactStatus.READY
            or current.retired_at is not None
            or current.deleted_at is not None
            or any(getattr(current, name) != getattr(frozen, name) for name in immutable)
        ):
            raise PermissionError("prepared media receipt is absent, changed or unavailable")
        original = current.timeline.original_source if current.timeline else None
        if original is not None:
            if (
                original.object_key != current.media_object_key
                or original.content_sha256 != current.content_sha256
                or original.start_seconds != 0
                or abs(original.end_seconds - frame_count / current.fps) > 1e-7
            ):
                raise ValueError(
                    "original video requires an explicit aligned window materialization"
                )
            return current, FrozenObject(
                original.object_key, original.size_bytes, original.content_sha256
            )
        objects = [item for item in current.objects if item.key == current.media_object_key]
        if len(objects) != 1 or objects[0].sha256 != current.content_sha256:
            raise ValueError("prepared media is absent from its publication receipt")
        return current, FrozenObject(objects[0].key, objects[0].size, objects[0].sha256)

    @contextmanager
    def load(
        self,
        source,
        *,
        attempt_id,
        artifacts,
        feature_mapping,
        alignment_version,
        frame_count,
        depth_contracts=None,
        inventory=None,
    ):
        assert_scope(source.identity)
        if not attempt_id or frame_count < 1:
            raise ValueError("prepared media requires a non-empty processing attempt")
        contracts = depth_contracts or {}
        camera_ids = [a.camera_id for a in artifacts]
        if len(camera_ids) != len(set(camera_ids)) or set(camera_ids) != set(feature_mapping):
            raise ValueError("prepared bundle must cover each declared camera exactly once")
        if (
            len(set(feature_mapping.values())) != len(feature_mapping)
            or not set(feature_mapping.values()).issubset(source.required_features)
            or set(contracts) != set(source.required_depth_features)
            or not set(contracts).issubset(feature_mapping.values())
        ):
            raise ValueError("prepared RGB/depth features differ from the frozen package input")
        if contracts and (
            inventory is None
            or inventory.identity != source.identity
            or inventory.source_sha256 != source.source_sha256
        ):
            raise PermissionError("depth evidence requires the authorized frozen source inventory")
        allowed = set(inventory.objects) if inventory is not None else set()
        self.directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="media-", dir=self.directory) as temporary:
            root, videos = Path(temporary), {}
            for index, frozen in enumerate(artifacts):
                artifact, media = self._media(source, frozen, alignment_version, frame_count)
                target = feature_mapping[artifact.camera_id]
                path = root / str(index) / "video.mp4"
                self._copy(media, path)
                evidence_root, entries = None, ()
                feature = {"dtype": "video", "shape": [artifact.height, artifact.width, 3]}
                if contract := contracts.get(target):
                    if not contract.feature.get("info", {}).get("is_depth_map"):
                        raise ValueError("depth contract must declare official depth metadata")
                    feature = contract.feature
                    evidence_root = root / str(index) / "evidence"
                    entries = tuple(item[0] for item in contract.evidence)
                    if not entries or len({entry.path for entry in entries}) != len(entries):
                        raise ValueError("depth evidence paths must be non-empty and unique")
                    for entry, obj in contract.evidence:
                        if obj not in allowed or (entry.size, entry.sha256) != (
                            obj.size,
                            obj.sha256,
                        ):
                            raise PermissionError(
                                "depth evidence is not in the frozen input inventory"
                            )
                        self._copy(obj, evidence_root / entry.path)
                videos[target] = PreparedVideo(
                    path, media.sha256, media.size, feature, evidence_root, entries
                )
            yield PreparedAssets(source.identity, attempt_id, videos)
