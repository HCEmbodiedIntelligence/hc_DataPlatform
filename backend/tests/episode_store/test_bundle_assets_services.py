"""Actual media repository/object-store hand-off before Episode publication.

Small synthetic media exercise software contracts, not real ingestion acceptance.
"""

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from hc_data_platform.aligned_media.artifact_store import S3AlignedMediaArtifactStore
from hc_data_platform.aligned_media.models import (
    AlignedMediaGenerationRequestV1,
    AlignedMediaScopeV1,
    AlignedMediaSelectorV1,
    AlignmentStagingArtifactV1,
    OriginalVideoReferenceV1,
)
from hc_data_platform.aligned_media.postgres import PostgresAlignedMediaRepository
from hc_data_platform.aligned_media.service import (
    AlignedMediaAuthorizationService,
    AlignedMediaGenerationService,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.episode_store.bundle_assets import (
    DepthAssetContract,
    FrozenObject,
    SourceInventory,
)
from hc_data_platform.episode_store.service import EpisodePackageService
from hc_data_platform.lance_catalog.adapters import LanceAdapter
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot, StepRecord
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_writer import build_rgbd_package, identity


def forbidden(*args, **kwargs):
    raise AssertionError("prepared H.265 media must not be re-encoded or written to Lance")


@pytest.fixture
def prepared_bundle(services, tmp_path, request):
    repository, storage = services
    options = getattr(request, "param", {})
    ident = identity().model_copy(
        update={"organization_id": "bundle-" + uuid4().hex, **options.get("identity", {})}
    )
    _, source, _, assets = build_rgbd_package(
        tmp_path, ident, mcap_topics=options.get("mcap_topics", False)
    )
    camera_topics = {
        feature: "/camera/" + feature.removeprefix("observation.images.") + "/image"
        if options.get("mcap_topics", False)
        else feature
        for feature in assets.videos
    }
    media_repository = PostgresAlignedMediaRepository(repository.connections)
    media_store = S3AlignedMediaArtifactStore(storage._client, storage._bucket)

    # Original-reference generation has no reason to invoke either of these ports.
    class UnusedEncoding:
        encode = read_camera_frames = staticmethod(forbidden)

    generation = AlignedMediaGenerationService(
        frame_reader=UnusedEncoding(),
        encoder=UnusedEncoding(),
        repository=media_repository,
        store=media_store,
    )
    service = EpisodePackageService(
        repository.connections, storage, tmp_path / "service", media_repository=media_repository
    )
    scope = AlignedMediaScopeV1(
        organization_id=ident.organization_id,
        project_id=ident.project_id,
        region_code=ident.region_code,
    )
    now = datetime.now(timezone.utc)
    originals, contracts, inventory = {}, {}, []

    def upload(path, relative):
        content = path.read_bytes()
        obj = FrozenObject(
            f"g1-bundle-fixtures/{ident.organization_id}/{relative}",
            len(content),
            hashlib.sha256(content).hexdigest(),
        )
        storage._client.put_object(
            Bucket=storage._bucket,
            Key=obj.key,
            Body=content,
            Metadata={"sha256": obj.sha256},
        )
        inventory.append(obj)
        return obj

    with scoped(source):
        with repository.transaction() as cursor:
            cursor.execute(
                """INSERT INTO registry.organization_projects
                (organization_id,project_id,display_name) VALUES (%s,%s,'G1 prepared bundle')""",
                (ident.organization_id, ident.project_id),
            )
        for feature, media in assets.videos.items():
            obj = upload(media.path, f"{feature}.mp4")
            originals[camera_topics[feature]] = OriginalVideoReferenceV1(
                object_key=obj.key,
                content_sha256=obj.sha256,
                size_bytes=obj.size,
                start_seconds=0,
                end_seconds=4 / 30,
                width=16,
                height=16,
                codec="hevc",
                fps=30,
            )
            if media.evidence_root is not None:
                contracts[feature] = DepthAssetContract(
                    media.feature,
                    tuple(
                        (
                            entry,
                            upload(media.evidence_root / entry.path, feature + "/" + entry.path),
                        )
                        for entry in media.evidence_files
                    ),
                )
        staging = AlignmentStagingArtifactV1(
            object_key="fixture-unused-arrow",
            content_sha256="a" * 64,
            size_bytes=1,
            row_count=4,
            alignment_version="fixture-alignment/1",
            original_videos=originals,
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        artifacts = tuple(
            generation.generate(
                scope,
                AlignedMediaGenerationRequestV1(
                    project_id=ident.project_id,
                    dataset_id=ident.dataset_id,
                    rollout_id=ident.rollout_id,
                    expected_dataset_version=1,
                    camera_id=feature,
                    source_sha256=source.source_sha256,
                    alignment=staging,
                    profile_id="original-video-reference-v1",
                ),
            )
            for feature in originals
        )
        assert all(a.dataset_committed_at is None for a in artifacts)
    bundle = dict(
        attempt_id="fixture-alignment-attempt",
        artifacts=artifacts,
        feature_mapping={topic: feature for feature, topic in camera_topics.items()},
        alignment_version=staging.alignment_version,
        frame_count=4,
        depth_contracts=contracts,
        inventory=SourceInventory(ident, source.source_sha256, tuple(inventory)),
    )
    try:
        yield service, source, bundle, assets, media_store
    finally:
        service.close()


@pytest.mark.integration
def test_pre_ready_handoff_reuses_rgbd_and_preserves_public_authorization(
    prepared_bundle, monkeypatch
):
    service, source, bundle, assets, media_store = prepared_bundle
    ident = source.identity
    monkeypatch.setattr(LanceAdapter, "stage_attempt", forbidden)
    monkeypatch.setattr(LanceAdapter, "commit_staged", forbidden)
    media = next(iter(bundle["artifacts"]))
    authorization = AlignedMediaAuthorizationService(
        repository=service.bundle_assets.media_repository, store=media_store
    )
    selector = AlignedMediaSelectorV1(
        project_id=ident.project_id,
        dataset_id=ident.dataset_id,
        rollout_id=ident.rollout_id,
        dataset_version=1,
        camera_id=media.camera_id,
    )
    rows = tuple(
        StepRecord(
            rollout_id=ident.rollout_id,
            step_index=i,
            timestamp_ns=i * 33_333_333,
            modalities={
                "/observation/state": [0.1 + i * 1e-12],
                "action": [float(i)],
                **{key: {"frame_index": i} for key in assets.videos},
            },
            source_timestamps_ns={key: (i * 33_333_333,) for key in assets.videos},
            time_error_ns={key: 0 for key in assets.videos},
            valid={key: True for key in ("/observation/state", "action", *assets.videos)},
        )
        for i in range(4)
    )
    schema = DatasetSchemaSnapshot.create(
        project_id=ident.project_id,
        dataset_id=ident.dataset_id,
        schema_snapshot_id=source.schema_snapshot_id,
        frequency_hz=30,
        fields={
            "/observation/state": "list<float64>",
            "action": "list<float64>",
            **{key: "json" for key in assets.videos},
        },
    )
    with scoped(source):
        with pytest.raises(ProblemException) as rejected:
            authorization.authorize(media.scope, selector)
        assert rejected.value.problem.status == 425
        assert service.repository.current(ident) is None
        collection, ready = service.write_from_aligned_bundle(
            source, rows, schema=schema, metadata={"task": "pre-READY RGB-D fixture"}, **bundle
        )
        assert collection.episodes == (ready.data,)
        restored = service.steps.read_steps(ident, ready.data.data_version_id, 0, 4).steps
        for actual, expected in zip(restored, rows, strict=True):
            assert actual.timestamp_ns == expected.timestamp_ns
            for key in ("/observation/state", "action"):
                assert actual.modalities[key] == expected.modalities[key]
            for key in assets.videos:
                assert actual.modalities[key]["source"] == expected.modalities[key]
                assert actual.modalities[key]["data_version_id"] == ready.data.data_version_id
                assert actual.source_timestamps_ns[key] == expected.source_timestamps_ns[key]
        package = service.files.open(ident, ready.data.data_version_id)
        media_hashes = {
            entry.sha256 for entry in package.manifest.files if entry.path.endswith(".mp4")
        }
        assert media_hashes == {item.sha256 for item in assets.videos.values()}
        assert "lance_version" not in ready.model_dump_json()
        # Internal localization does not implicitly publish the old media endpoint.
        with pytest.raises(ProblemException) as rejected:
            authorization.authorize(media.scope, selector)
        assert rejected.value.problem.status == 425
        assert list(service.bundle_assets.directory.iterdir()) == []


@pytest.mark.integration
def test_prepared_bundle_rejects_forged_receipt_missing_depth_and_foreign_inventory(
    prepared_bundle,
):
    service, source, bundle, assets, _ = prepared_bundle
    with scoped(source):
        for changed, error in (
            ({"depth_contracts": {}}, ValueError),
            ({"inventory": replace(bundle["inventory"], objects=())}, PermissionError),
            ({"inventory": replace(bundle["inventory"], source_sha256="e" * 64)}, PermissionError),
            (
                {
                    "artifacts": tuple(
                        a.model_copy(update={"content_sha256": "f" * 64})
                        for a in bundle["artifacts"]
                    )
                },
                PermissionError,
            ),
        ):
            with pytest.raises(error), service.bundle_assets.load(source, **{**bundle, **changed}):
                pytest.fail("invalid bundle was made available")
        assert service.repository.current(source.identity) is None
        assert list(service.bundle_assets.directory.iterdir()) == []
        # Same-size corruption must fail byte hashing, even if HEAD metadata is unchanged.
        artifact = bundle["artifacts"][0]
        original = artifact.timeline.original_source
        content = bytearray(assets.videos[artifact.camera_id].path.read_bytes())
        content[-1] ^= 1
        storage = service.bundle_assets.storage
        storage._client.put_object(
            Bucket=storage._bucket,
            Key=original.object_key,
            Body=bytes(content),
            Metadata={"sha256": original.content_sha256},
        )
        with (
            pytest.raises(ValueError, match="hash differs"),
            service.bundle_assets.load(source, **bundle),
        ):
            pytest.fail("corrupt input media was accepted")
        assert service.repository.current(source.identity) is None
        assert list(service.bundle_assets.directory.iterdir()) == []
    foreign = source.model_copy(
        update={"identity": source.identity.model_copy(update={"organization_id": "foreign"})}
    )
    with (
        scoped(foreign),
        pytest.raises(PermissionError),
        service.bundle_assets.load(source, **bundle),
    ):
        pytest.fail("foreign scope could localize prepared media")
