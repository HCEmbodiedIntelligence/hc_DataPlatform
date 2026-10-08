"""Publish real immutable original-video receipts for historical logical streams."""

import hashlib
from datetime import datetime, timedelta, timezone

from hc_data_platform.aligned_media.artifact_store import S3AlignedMediaArtifactStore
from hc_data_platform.aligned_media.models import (
    AlignedMediaGenerationRequestV1,
    AlignedMediaScopeV1,
    AlignmentStagingArtifactV1,
    OriginalVideoReferenceV1,
)
from hc_data_platform.aligned_media.postgres import PostgresAlignedMediaRepository
from hc_data_platform.aligned_media.service import AlignedMediaGenerationService


def publish_media(packages, storage, source, version, assets):
    ident = source.identity
    scope = AlignedMediaScopeV1(
        organization_id=ident.organization_id,
        project_id=ident.project_id,
        region_code=ident.region_code,
    )
    repository = PostgresAlignedMediaRepository(packages.connections)
    store = S3AlignedMediaArtifactStore(storage._client, storage._bucket)

    class NoEncoder:
        def read_camera_frames(self, *args, **kwargs):
            raise AssertionError("original reference must not invoke frame reader")

        def encode(self, *args, **kwargs):
            raise AssertionError("original reference must not invoke encoder")

    generator = AlignedMediaGenerationService(
        frame_reader=NoEncoder(),
        encoder=NoEncoder(),
        repository=repository,
        store=store,
    )
    originals = {}
    for feature, video in assets.videos.items():
        content = video.path.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        key = f"logical-media-fixture/{ident.organization_id}/{ident.episode_id}/{feature}.mp4"
        storage._client.put_object(
            Bucket=storage._bucket, Key=key, Body=content, Metadata={"sha256": sha}
        )
        originals[feature] = OriginalVideoReferenceV1(
            object_key=key,
            content_sha256=sha,
            size_bytes=len(content),
            start_seconds=0,
            end_seconds=4 / 30,
            width=16,
            height=16,
            codec="hevc",
            fps=30,
        )
    now = datetime.now(timezone.utc)
    staging = AlignmentStagingArtifactV1(
        object_key="unused-original-reference-arrow",
        content_sha256="a" * 64,
        size_bytes=1,
        row_count=4,
        alignment_version="logical-fixture/1",
        original_videos=originals,
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    artifacts = {}
    for feature in originals:
        artifact = generator.generate(
            scope,
            AlignedMediaGenerationRequestV1(
                project_id=ident.project_id,
                dataset_id=ident.dataset_id,
                rollout_id=ident.rollout_id,
                expected_dataset_version=version.version,
                camera_id=feature,
                source_sha256=source.source_sha256,
                alignment=staging,
                profile_id="original-video-reference-v1",
            ),
        )
        repository.mark_dataset_committed(
            scope=scope, artifact_ids=(artifact.artifact_id,), now=now
        )
        artifacts[feature] = artifact.artifact_id
    return artifacts
