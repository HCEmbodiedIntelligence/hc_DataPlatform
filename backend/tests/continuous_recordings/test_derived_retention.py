import hashlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from hc_data_platform.continuous_recordings import retention
from hc_data_platform.continuous_recordings.derived_cache import DerivedCache
from hc_data_platform.continuous_recordings.mcap_native import prepare
from hc_data_platform.object_storage.memory import InMemoryObjectStorage
from tests.continuous_recordings.test_mcap_native import synthetic_mcap


def test_rebuild_evicted_video_from_frozen_raw_without_publishing(tmp_path):
    raw = synthetic_mcap(tmp_path / "raw.mcap")
    original = {
        "object_key": "raw/capture.mcap",
        "size": raw.stat().st_size,
        "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
    }
    cfg = {
        "state_topic": "/joint_states",
        "action_topic": "/joint_cmd",
        "primary_camera": "/color",
        "depth_scale_m_per_unit": 0.001,
        "cameras": [
            {
                "camera_id": "head",
                "topic": "/color",
                "kind": "rgb",
                "message_type": "sensor_msgs/msg/CompressedImage",
            },
            {
                "camera_id": "head_depth",
                "topic": "/depth",
                "kind": "depth",
                "message_type": "sensor_msgs/msg/CompressedImage",
            },
        ],
    }
    baseline = tmp_path / "baseline"
    prepare(raw, baseline, cfg, preserve_source=True)
    relative = "videos/head_depth.mp4"
    expected = (baseline / relative).read_bytes()
    sha = hashlib.sha256(expected).hexdigest()
    ident = "a" * 64
    key = f"derived-mcap/o/p/r/{ident}/{sha}/{relative}"
    store = InMemoryObjectStorage()
    store.objects[original["object_key"]] = raw.read_bytes()
    initial = dict(store.objects)

    @contextmanager
    def connections():
        yield SimpleNamespace(
            execute=lambda *args: SimpleNamespace(
                fetchone=lambda: (
                    original,
                    cfg,
                    {"schema_version": "capture-recording-snapshot/v1"},
                )
            )
        )

    recovery = DerivedCache(store, connections, ("o", "p", "r"), tmp_path / "cache")
    rebuilt = recovery.recover(key, relative, len(expected), sha)
    assert rebuilt.read_bytes() == expected
    assert store.objects == initial
    with pytest.raises(ValueError, match="differs from frozen receipt"):
        recovery.recover(key, relative, len(expected) + 1, sha)
    assert recovery.recover(key, relative, len(expected), sha, force=True) == rebuilt
    assert (
        DerivedCache(store, connections, ("other", "p", "r"), tmp_path).recover(
            key, relative, len(expected), sha
        )
        is None
    )


@pytest.mark.parametrize("corrupt", [False, True])
def test_retirement_verifies_exact_candidate_before_deleting(monkeypatch, corrupt):
    body = b"prepared media"
    key = "derived-mcap/o/p/r/" + "a" * 64 + "/sha/videos/head.mp4"
    item = {"key": key, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    store = InMemoryObjectStorage()
    store.objects = {
        "raw/original": b"raw",
        "episodes/version/native.mcap": b"episode",
        "recording-previews/media.mp4": b"preview",
        key: b"bad" if corrupt else body,
    }
    writes = []

    def execute(sql, params):
        if "UPDATE" in sql:
            writes.append(params)
        return SimpleNamespace(fetchone=lambda: ("asset-id",))

    @contextmanager
    def connections():
        yield SimpleNamespace(execute=execute, commit=lambda: None)

    monkeypatch.setattr(retention, "retirement_plan", lambda *args: [item])
    if corrupt:
        with pytest.raises(Exception):
            retention.retire_recording(connections, store, None, ("o", "p", "r"), "recording", authorize=lambda *args: {"policy_id": "test"})
        assert key in store.objects
        assert not writes
    else:
        assert retention.retire_recording(
            connections, store, None, ("o", "p", "r"), "recording", authorize=lambda *args: {"policy_id": "test"}
        ) == [{**item, "lifecycle": {"policy_id": "test"}}]
        assert key not in store.objects
        assert len(writes) == 1
    assert set(store.objects) >= {
        "raw/original",
        "episodes/version/native.mcap",
        "recording-previews/media.mp4",
    }


def test_pause_after_rebuild_prevents_cache_deletion(monkeypatch):
    body = b'cache'
    store = InMemoryObjectStorage()
    store.objects['cache'] = body
    item = {'key': 'cache', 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
    monkeypatch.setattr(retention, 'retirement_plan', lambda *args: [item])
    calls = iter([{'policy_id':'test'}, None])
    @contextmanager
    def connections():
        yield SimpleNamespace(execute=lambda *args: None)
    assert retention.retire_recording(connections, store, None, ('o','p','r'), 'recording',
                                      authorize=lambda *args: next(calls)) == []
    assert store.objects == {'cache':body}
