from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock

import pytest

from hc_data_platform.registry.asset_manifest import asset_manifest
from hc_data_platform.registry.models import RobotModelAsset
from hc_data_platform.robotics.scene_models import FrozenRobotBinding, scene_hash
from hc_data_platform.robotics.scene_snapshots import RobotSceneSnapshots


def test_preview_reads_only_the_pinned_version_and_verifies_its_manifest():
    asset = RobotModelAsset(
        asset_id="a", relative_path="a.stl", role="MESH", media_type="model/stl",
        size_bytes=1, sha256="a" * 64, created_at=datetime(2026, 11, 4, tzinfo=timezone.utc),
    )
    localized = asset.model_copy(update={
        "created_at": asset.created_at.astimezone(timezone(timedelta(hours=8)))
    })
    manifest = scene_hash([asset.model_dump(mode="json")])
    inputs = Mock(return_value=("model", manifest, (localized,), scene_hash([]), ()))
    snapshots = RobotSceneSnapshots(MagicMock(), binding_query=Mock(), model_query=inputs)
    reference = FrozenRobotBinding(
        robot_id="robot", binding_id="pinned", model_version_id="first",
        calibration_sha256=scene_hash({}),
    )
    scene = snapshots.resolve_binding("org", reference, {})
    assert scene.assets[0].model_dump(mode="json") == asset.model_dump(mode="json")
    assert inputs.call_args.args[1:] == ("org", "first")
    snapshots.binding_query.assert_not_called()
    inputs.return_value = ("model", "0" * 64, (localized,), scene_hash([]), ())
    with pytest.raises(ValueError, match="digest mismatch"):
        snapshots.resolve_binding("org", reference, {})


def test_no_bound_version_needs_no_registry_access():
    snapshots = RobotSceneSnapshots(Mock(), binding_query=Mock(), model_query=Mock())
    reference = FrozenRobotBinding(
        robot_id="robot", binding_id=None, model_version_id=None,
        calibration_sha256=scene_hash({}),
    )
    assert snapshots.resolve_binding("org", reference, {}).status == "NO_MODEL_BINDING"
    snapshots.connections.assert_not_called()

def test_manifest_timestamps_are_canonical_for_new_versions_and_match_historical_digests():
    utc = datetime(2026, 11, 4, 7, 0, 0, 565186, tzinfo=timezone.utc)
    asset = RobotModelAsset(
        asset_id="a", relative_path="a.stl", role="MESH", media_type="model/stl",
        size_bytes=1, sha256="a" * 64, created_at=utc,
    )
    local = asset.model_copy(update={"created_at": utc.astimezone(timezone(timedelta(hours=8)))})
    other = asset.model_copy(update={"created_at": utc.astimezone(timezone(timedelta(hours=-5)))})
    expected = scene_hash([asset.model_dump(mode="json")])
    for observed in (asset, local, other):
        normalized, checksum = asset_manifest((observed,))
        assert checksum == expected
        assert normalized[0].model_dump(mode="json")["created_at"] == "2026-11-04T07:00:00.565186Z"

    legacy_hash = scene_hash([local.model_dump(mode="json")])
    normalized, checksum = asset_manifest((other,), recorded_hash=legacy_hash)
    assert checksum == legacy_hash
    assert normalized[0].model_dump(mode="json") == local.model_dump(mode="json")
    changed = other.model_copy(update={"sha256": "b" * 64})
    assert asset_manifest((changed,), recorded_hash=legacy_hash)[1] != legacy_hash
