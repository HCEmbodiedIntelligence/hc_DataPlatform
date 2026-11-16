from contextlib import nullcontext
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hc_data_platform.episode_store.errors import PackageConflict
from hc_data_platform.episode_store.models import CommitReceipt, DataVersionRef, FileEntry
from hc_data_platform.episode_store.playback_cache import record_verified, verified_frames
from hc_data_platform.episode_store.previews import EpisodePreviews, PreviewReceipt
from hc_data_platform.storage.local_cache import LocalCacheBucket
from tests.episode_store.test_writer import identity


@pytest.fixture
def playback(tmp_path):
    cache = LocalCacheBucket(tmp_path, "test")
    data = DataVersionRef(identity=identity(), data_version_id="dv_test", version=1,
                          content_hash="1" * 64, schema_snapshot_id="s", package_prefix="episodes/test",
                          frame_count=80)
    commit = CommitReceipt(data=data, idempotency_key="2" * 64, manifest_sha256="3" * 64)
    sha = sha256(b"mp4").hexdigest()
    media = FileEntry(path="source.mp4", size=3, sha256=sha, media_type="video/mp4")
    receipt = PreviewReceipt(data=data, feature="rgb", source_hash="4" * 64, source_video_sha256=sha,
                             object_key="episodes/test/source.mp4", file=media, kind="rgb-video",
                             source_reused=True, fps=30, width=2, height=2)
    key = EpisodePreviews._playback_key(receipt)
    cache.put_object(key, b"mp4")
    return cache, key, commit, receipt


def test_verified_binding_and_replacement(playback):
    cache, key, commit, receipt = playback
    assert verified_frames(cache, key, commit, receipt) is None
    record_verified(cache, key, commit, receipt, 75)
    assert verified_frames(cache, key, commit, receipt) == 75
    assert verified_frames(cache, key, commit.model_copy(update={"manifest_sha256": "5" * 64}), receipt) is None
    assert verified_frames(cache, key, commit, receipt.model_copy(update={"fps": 20.0})) is None
    directory = cache.directory(key)
    marker = directory / "playback.json"
    marker.write_text(marker.read_text().replace('"frames":75', '"frames":76'))
    assert verified_frames(cache, key, commit, receipt) is None
    record_verified(cache, key, commit, receipt, 75)
    (directory / "payload").write_bytes(b"bad")
    assert verified_frames(cache, key, commit, receipt) is None
    with pytest.raises(PackageConflict):
        record_verified(cache, key, commit, receipt, 75)
    cache.delete_object(key)
    assert verified_frames(cache, key, commit, receipt) is None


def test_warm_grant_checks_binding_and_audits_without_object_reads(playback, monkeypatch):
    cache, key, commit, receipt = playback
    record_verified(cache, key, commit, receipt, 75)
    files = Mock()
    files.storage = SimpleNamespace(_client=SimpleNamespace(lifecycle_cache=cache))
    files.repository.get.return_value = commit
    cursor = Mock()
    files.repository.transaction.side_effect = lambda: nullcontext(cursor)
    previews = EpisodePreviews(files, None, None, signer=Mock(return_value="/signed-test"))
    previews._row = Mock(return_value=("READY", receipt.source_hash, receipt.model_dump(mode="json")))
    monkeypatch.setattr("hc_data_platform.episode_store.previews.current_request_context",
                        lambda: SimpleNamespace(request_id="req"))
    args = commit.data.identity, commit.data.data_version_id, receipt.feature
    for actor in ("first", "second"):
        assert previews.authorize(*args, actor_id=actor).frame_count == 75
    assert files.repository.get.call_count == previews._row.call_count == cursor.execute.call_count == 2
    assert previews.signer.call_count == 2
    files.open.assert_not_called()
    files.repository.get.side_effect = KeyError("revoked")
    with pytest.raises(KeyError):
        previews.authorize(*args, actor_id="first")
    assert cursor.execute.call_count == 2
