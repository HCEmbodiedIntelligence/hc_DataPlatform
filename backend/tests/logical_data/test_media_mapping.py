from datetime import datetime, timezone
from types import SimpleNamespace as N

import pytest

from hc_data_platform.aligned_media.audit import InMemoryAlignedMediaAuditRecorder
from hc_data_platform.aligned_media.models import AlignedMediaScopeV1
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.integrations.logical_samples import EpisodeSampleAdapter, LanceSampleAdapter
from hc_data_platform.security import AuthContext


def test_native_subwindow_maps_to_actual_preview_pts_and_keeps_colorizer():
    data = N(identity="identity", data_version_id="fixed-data")
    binding = N(data=data, value_kind=None, feature_key="depth", start_step=2, end_step=5)
    grant = N(
        data=data,
        frame_count=8,
        fps=30,
        kind="depth-preview",
        media_url="https://media/preview",
        expires_at=datetime.now(timezone.utc),
        width=16,
        height=16,
        media_sha256="a" * 64,
        depth_min_m=0.01,
        depth_max_m=10,
        colorizer={"implementation": "realsense"},
    )
    adapter = EpisodeSampleAdapter(None, N(authorize=lambda *a, **kw: grant))
    stream = N(package_binding=binding, episode_stream_id="depth-stream")
    media = adapter.authorize_media(None, stream, auth=N(subject_id="actor"), snapshot_id="frozen")
    assert media.media_start_seconds == pytest.approx(2 / 30)
    assert media.sample_count == 3 and media.colorizer == grant.colorizer
    grant.data = N(identity="other", data_version_id="other")
    with pytest.raises(ValueError, match="frozen"):
        adapter.authorize_media(None, stream, auth=N(subject_id="actor"), snapshot_id="frozen")


@pytest.mark.parametrize("first_step", [0, 7])
def test_historical_video_keeps_original_offset_and_rejects_changed_receipt(first_step):
    scope = AlignedMediaScopeV1(organization_id="org", project_id="project", region_code="region")
    binding = N(
        artifact_id="frozen-artifact",
        rollout_id="rollout",
        dataset_version=7,
        camera_id="rgb",
        start_step=first_step + 2,
        end_step=first_step + 5,
    )
    grant = N(
        artifact_id="frozen-artifact",
        dataset_id="dataset",
        rollout_id="rollout",
        dataset_version=7,
        camera_id="rgb",
        width=16,
        height=16,
        media_url="https://media/original",
        expires_at=datetime.now(timezone.utc),
        timeline=N(
            first_step=first_step,
            frame_count=8,
            fps=30,
            original_source=N(start_seconds=31.933333333, content_sha256="a" * 64),
        ),
    )
    audit = InMemoryAlignedMediaAuditRecorder()
    adapter = LanceSampleAdapter(None, N(authorize=lambda *a: grant), audit)
    auth = AuthContext(
        subject_id="actor",
        project_ids=frozenset({"project"}),
        region_codes=frozenset({"region"}),
        capabilities=frozenset({"episode.read"}),
        organization_ids=frozenset({"org"}),
        organization_scope_triples=frozenset({("org", "project", "region")}),
    )
    revision, stream = (
        N(scope=scope, dataset_id="dataset"),
        N(aligned_media_binding=binding, episode_stream_id="rgb"),
    )
    token = bind_request_context(
        RequestContext(organization_id="org", project_id="project", region_code="region")
    )
    try:
        result = adapter.authorize_media(revision, stream, auth=auth, snapshot_id="frozen")
        assert result.sample_count == 3
        assert result.media_start_seconds == pytest.approx(31.933333333 + 2 / 30)
        assert len(audit.events) == 1
        grant.artifact_id = "different-artifact"
        with pytest.raises(ValueError, match="frozen"):
            adapter.authorize_media(revision, stream, auth=auth, snapshot_id="frozen")
        assert len(audit.events) == 1
        grant.artifact_id = "frozen-artifact"
        binding.end_step = first_step + 9
        with pytest.raises(ValueError, match="frozen"):
            adapter.authorize_media(revision, stream, auth=auth, snapshot_id="frozen")
    finally:
        reset_request_context(token)
