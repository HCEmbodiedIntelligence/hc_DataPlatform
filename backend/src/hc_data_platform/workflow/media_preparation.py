"""Private media staging shared by MCAP and native LeRobot ingest."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, unquote, urlparse

from hc_data_platform.aligned_media.models import (
    AlignedMediaArtifactV1,
    AlignedMediaGenerationRequestV1,
    AlignedMediaScopeV1,
    EncodedAlignedMediaV1,
)

from .models import AlignedMediaActivityInput, PreparedAlignedMediaV1
from .projection_store import ProjectionArtifactStorePort


class PreparedMediaService(Protocol):
    def prepare(
        self,
        scope: AlignedMediaScopeV1,
        request: AlignedMediaGenerationRequestV1,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> AbstractContextManager[EncodedAlignedMediaV1]: ...

    def generate(
        self,
        scope: AlignedMediaScopeV1,
        request: AlignedMediaGenerationRequestV1,
        *,
        cancelled: Callable[[], bool] | None = None,
        prepared: EncodedAlignedMediaV1 | None = None,
    ) -> AlignedMediaArtifactV1: ...


def media_scope(value: AlignedMediaActivityInput) -> AlignedMediaScopeV1:
    return AlignedMediaScopeV1(
        organization_id=value.organization_id,
        project_id=value.request.project_id,
        region_code=value.region_code,
    )


def prepare_media(
    value: AlignedMediaActivityInput,
    service: PreparedMediaService,
    store: ProjectionArtifactStorePort,
    *,
    cancelled: Callable[[], bool],
) -> PreparedAlignedMediaV1:
    # 1 is only a legacy encoder input placeholder. No public artifact or
    # Dataset version is reserved here; the real version is allocated at commit.
    value = value.model_copy(
        update={"request": value.request.model_copy(update={"expected_dataset_version": 1})}
    )
    with service.prepare(media_scope(value), value.request, cancelled=cancelled) as encoded:
        if encoded.original_source is not None:
            return PreparedAlignedMediaV1(input=value, encoded=encoded)
        path = Path(unquote(urlparse(encoded.file_uri).path))
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(8 * 1024 * 1024):
                digest.update(chunk)
        sha = digest.hexdigest()
        key = "/".join(
            quote(str(part), safe="-._~")
            for part in (
                "staging",
                "prepared-media",
                value.organization_id,
                value.request.project_id,
                value.region_code,
                value.request.dataset_id,
                value.request.rollout_id,
                value.request.source_sha256,
                value.request.alignment.content_sha256,
                value.request.profile_id,
                value.request.camera_id,
                sha,
                "media.mp4",
            )
        )
        size = store.publish_file(key, path, sha256=sha)
        return PreparedAlignedMediaV1(
            input=value,
            encoded=encoded.model_copy(update={"file_uri": "staging://prepared"}),
            object_key=key,
            content_sha256=sha,
            size_bytes=size,
        )


def publish_prepared_media(
    prepared: PreparedAlignedMediaV1,
    dataset_version: int,
    service: PreparedMediaService,
    store: ProjectionArtifactStorePort,
) -> AlignedMediaArtifactV1:
    request = prepared.input.request.model_copy(
        update={"expected_dataset_version": dataset_version}
    )
    scope = media_scope(prepared.input)
    if prepared.encoded.original_source is not None:
        return service.generate(scope, request, prepared=prepared.encoded)
    assert prepared.object_key and prepared.content_sha256
    with (
        store.local_file(
            prepared.object_key,
            expected_sha256=prepared.content_sha256,
            expected_size=prepared.size_bytes,
        ) as source,
        tempfile.TemporaryDirectory(prefix="prepared-publication-") as root,
    ):
        # The immutable media publisher accepts an explicit media.mp4 file.
        target = Path(root) / "media.mp4"
        shutil.copyfile(source, target)
        return service.generate(
            scope,
            request,
            prepared=prepared.encoded.model_copy(update={"file_uri": target.resolve().as_uri()}),
        )
