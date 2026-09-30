"""Time/receipt attacks rejected before a historical media asset can be frozen."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from hc_data_platform.aligned_media.models import AlignedMediaFrameReferenceV1
from hc_data_platform.episode_store.migration_native import NativeMigrationAssets
from hc_data_platform.episode_store.postgres import PackageConflict


def media_contract():
    artifact = {
        "status": "READY",
        "retired_at": None,
        "deleted_at": None,
        "source_sha256": "a" * 64,
        "rollout_id": "rollout",
        "dataset_version": 7,
        "camera_id": "/camera/head/image",
        "frame_count": 2,
        "fps": 30,
        "artifact_id": "artifact",
        "alignment_version": "alignment/1",
        "media_object_key": "frozen/media.mp4",
        "content_sha256": "b" * 64,
        "object_manifest": [{"key": "frozen/media.mp4", "size": 200, "sha256": "b" * 64}],
        "timeline_json": {
            "first_step": 0,
            "fps": 30,
            "frame_count": 2,
            "start_timestamp_ns": "1780000000000000001",
        },
    }
    binding = {
        "rollout_id": "rollout",
        "dataset_version": 7,
        "camera_id": artifact["camera_id"],
        "start_step": 0,
        "end_step": 2,
        "fps": 30,
    }
    rows = [
        SimpleNamespace(timestamp_ns=1780000000000000001 + i * 1_000_000_000 // 30)
        for i in range(2)
    ]
    refs = [
        AlignedMediaFrameReferenceV1(
            camera_id=artifact["camera_id"],
            artifact_id=artifact["artifact_id"],
            object_key=artifact["media_object_key"],
            alignment_version=artifact["alignment_version"],
            frame_index=i,
            pts=i,
            pts_time_base_numerator=1,
            pts_time_base_denominator=30,
            timestamp_ns=row.timestamp_ns,
            valid=True,
            placeholder=False,
            repeated=False,
            dropped=False,
        )
        for i, row in enumerate(rows)
    ]
    reference = SimpleNamespace(identity=SimpleNamespace(rollout_id="rollout"), frame_count=2)
    return [
        artifact,
        binding,
        refs,
        rows,
        reference,
        SimpleNamespace(source_sha256="a" * 64),
        SimpleNamespace(frequency_hz=30),
    ]


def test_whole_rgb_receipt_is_frozen_without_reencoding():
    contract = media_contract()
    obj = NativeMigrationAssets._whole_media(*contract)
    assert (obj.key, obj.sha256, obj.size) == ("frozen/media.mp4", "b" * 64, 200)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "GENERATING"),
        ("retired_at", "2026-01-01"),
        ("deleted_at", "2026-01-01"),
        ("source_sha256", "c" * 64),
        ("rollout_id", "foreign"),
        ("camera_id", "/foreign"),
        ("dataset_version", 8),
        ("frame_count", 3),
        ("fps", 29),
        ("content_sha256", "c" * 64),
        ("object_manifest", []),
    ],
)
def test_changed_artifact_rejected(field, value):
    contract = media_contract()
    contract[0][field] = value
    with pytest.raises(PackageConflict):
        NativeMigrationAssets._whole_media(*contract)


@pytest.mark.parametrize(
    "field,value",
    [
        ("artifact_id", "foreign"),
        ("object_key", "unbound.mp4"),
        ("camera_id", "/foreign"),
        ("alignment_version", "later"),
        ("frame_index", 0),
        ("pts", 0),
        ("pts_time_base_denominator", 29),
        ("timestamp_ns", 1780000000033333335),
    ],
)
def test_single_changed_frame_and_nanosecond_time_rejected(field, value):
    contract = media_contract()
    contract[2][1] = contract[2][1].model_copy(update={field: value})
    with pytest.raises(PackageConflict, match="source-frame/PTS"):
        NativeMigrationAssets._whole_media(*contract)


@pytest.mark.parametrize(
    "field,value",
    [
        ("first_step", 1),
        ("start_timestamp_ns", "1780000000000000002"),
        ("frame_count", 1),
        ("fps", 29),
    ],
)
def test_changed_timeline_rejected(field, value):
    contract = media_contract()
    contract[0]["timeline_json"][field] = value
    with pytest.raises(PackageConflict, match="timeline"):
        NativeMigrationAssets._whole_media(*contract)


def test_shared_original_video_cannot_be_mislabeled_as_whole_episode():
    contract = media_contract()
    contract[0]["timeline_json"]["original_source"] = {"start_seconds": 0, "end_seconds": 100}
    with pytest.raises(ValueError, match="window preparation"):
        NativeMigrationAssets._whole_media(*contract)


@pytest.mark.parametrize("size", [0, -1, 100001, True])
def test_frame_limit_must_be_explicit_and_bounded(tmp_path, size):
    with pytest.raises(ValueError, match="frame limit"):
        NativeMigrationAssets(None, None, None, tmp_path, max_frames=size)


def test_foreign_inventory_stops_before_reading_legacy(tmp_path):
    from hc_data_platform.episode_store.migration_inventory import FORMAT
    from hc_data_platform.episode_store.models import PackageInput, digest
    from tests.episode_store.test_commit_services import scoped
    from tests.episode_store.test_writer import identity

    source = PackageInput(
        identity=identity(),
        source_sha256="a" * 64,
        processor_version="test",
        configuration_sha256="b" * 64,
        schema_snapshot_id="schema",
        required_features=("observation.state",),
    )
    payload = {
        "format": FORMAT,
        "scope": {
            key: getattr(source.identity, key)
            for key in ("organization_id", "project_id", "region_code", "dataset_id")
        },
    }
    for key in payload["scope"]:
        foreign = deepcopy(payload)
        foreign["scope"][key] = "foreign"
        inventory = {"inventory": foreign, "inventory_sha256": digest(foreign)}
        reference = SimpleNamespace(identity=source.identity)
        with (
            scoped(source),
            pytest.raises(PermissionError, match="hash/scope"),
            NativeMigrationAssets(None, None, None, tmp_path).load(inventory, reference),
        ):
            pytest.fail("foreign inventory must never produce assets")


@pytest.mark.parametrize("change", [None, "fps", "dimensions", "truncated", "extra", "depth"])
def test_decode_checks_actual_pts_shape_count_and_h265_rgb(tmp_path, change):
    from tests.episode_store.test_writer import build_rgbd_package

    _, _, _, assets = build_rgbd_package(tmp_path)
    artifact = {"fps": 30, "media_width": 16, "media_height": 16}
    refs = [None] * 4
    path = assets.videos["observation.images.head_rgb"].path
    if change == "fps":
        artifact["fps"] = 29
    elif change == "dimensions":
        artifact["media_width"] = 32
    elif change == "truncated":
        refs = refs[:3]
    elif change == "extra":
        refs.append(None)
    elif change == "depth":
        path = assets.videos["observation.images.head_depth"].path
    if change is None:
        # Uploaded H.265 RGB remains RGB; gray12le applies only to depth.
        NativeMigrationAssets._video_times(path, refs, artifact)
    else:
        with pytest.raises(PackageConflict):
            NativeMigrationAssets._video_times(path, refs, artifact)
