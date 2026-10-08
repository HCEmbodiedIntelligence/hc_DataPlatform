"""Snapshot authorization and pagination, with explicitly registered physical readers."""

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem

from .models import LogicalSampleBatch, LogicalSnapshot, LogicalSnapshotView


class LogicalDataService:
    def __init__(self, revisions, adapters, *, scenes=None):
        self.revisions, self.adapters = revisions, adapters
        self.scenes = scenes

    def _resolve(self, selection, auth, snapshot_token):
        # Public product read enforces scope, current permission, frozen version
        # membership, snapshot token and the existing per-read audit policy.
        revision = self.revisions.episode_revision(
            **selection.model_dump(),
            auth=auth,
            snapshot_token=snapshot_token,
            request_id=current_request_context().request_id,
        ).data
        if (
            revision.scope.organization_id != selection.organization_id
            or revision.scope.project_id != selection.project_id
            or revision.scope.region_code != selection.region_code
            or revision.dataset_id != selection.dataset_id
            or revision.version_id != selection.version_id
            or revision.revision_id != selection.revision_id
        ):
            raise ValueError("resolved revision differs from authorized snapshot selection")
        streams = tuple(self.adapters.for_stream(s).describe(s) for s in revision.streams)
        snapshot = LogicalSnapshot(
            selection=selection,
            episode_id=revision.episode_id,
            content_sha256=revision.content_sha256.lower(),
            streams=streams,
        )
        return revision, LogicalSnapshotView(snapshot_id=snapshot.snapshot_id, snapshot=snapshot)

    def snapshot(self, selection, *, auth, snapshot_token):
        return self._resolve(selection, auth, snapshot_token)[1]

    def _stream(self, selection, stream_id, *, auth, snapshot_token, snapshot_id):
        revision, view = self._resolve(selection, auth, snapshot_token)
        if view.snapshot_id != snapshot_id:
            raise problem(
                status=409,
                code="LOGICAL_SNAPSHOT_MISMATCH",
                title="Snapshot differs",
                detail="The requested samples must use the exact frozen logical snapshot.",
            )
        stream = next((s for s in revision.streams if s.episode_stream_id == stream_id), None)
        if stream is None:
            raise KeyError("stream is absent from the frozen snapshot")
        adapter = self.adapters.for_stream(stream)
        descriptor = adapter.describe(stream)
        return revision, view, stream, adapter, descriptor

    def read_samples(self, selection, stream_id, start, end, *, auth, snapshot_token, snapshot_id):
        revision, view, stream, adapter, descriptor = self._stream(
            selection, stream_id, auth=auth, snapshot_token=snapshot_token, snapshot_id=snapshot_id
        )
        if not 0 <= start <= end <= descriptor.sample_count or end - start > 4096:
            raise ValueError("invalid or oversized logical sample window")
        samples = adapter.read(revision, stream, start, end)
        return LogicalSampleBatch(
            snapshot_id=view.snapshot_id,
            stream_id=stream_id,
            start=start,
            end=end,
            samples=samples,
        )

    def authorize_media(self, selection, stream_id, *, auth, snapshot_token, snapshot_id):
        revision, view, stream, adapter, _ = self._stream(
            selection, stream_id, auth=auth, snapshot_token=snapshot_token, snapshot_id=snapshot_id
        )
        return adapter.authorize_media(revision, stream, auth=auth, snapshot_id=view.snapshot_id)

    def iter_samples(
        self, selection, stream_id, *, auth, snapshot_token, snapshot_id, batch_size=128
    ):
        if not 1 <= batch_size <= 4096:
            raise ValueError("batch size must be between 1 and 4096")
        view = self.snapshot(selection, auth=auth, snapshot_token=snapshot_token)
        if view.snapshot_id != snapshot_id:
            raise ValueError("logical snapshot differs from the frozen consumer specification")
        stream = next((s for s in view.snapshot.streams if s.stream_id == stream_id), None)
        if stream is None:
            raise KeyError("stream is absent from the frozen snapshot")
        for start in range(0, stream.sample_count, batch_size):
            # Reauthorize on each bounded read, including warm-cache/revoked callers.
            yield self.read_samples(
                selection,
                stream_id,
                start,
                min(start + batch_size, stream.sample_count),
                auth=auth,
                snapshot_token=snapshot_token,
                snapshot_id=snapshot_id,
            )

    def _scene(self, selection, *, auth, snapshot_token, snapshot_id):
        revision, snapshot = self._resolve(selection, auth, snapshot_token)
        if snapshot.snapshot_id != snapshot_id:
            raise problem(
                status=409,
                code="LOGICAL_SNAPSHOT_MISMATCH",
                title="Snapshot differs",
                detail="Robot replay requires the exact fixed data snapshot.",
            )
        if self.scenes is None:
            raise problem(
                status=503,
                code="LOGICAL_SCENE_UNAVAILABLE",
                title="Robot scene unavailable",
                detail="The application scene reader is not configured.",
            )
        return revision, self.scenes.scene(revision, snapshot_id)

    def robot_scene(self, selection, *, auth, snapshot_token, snapshot_id):
        return self._scene(
            selection, auth=auth, snapshot_token=snapshot_token, snapshot_id=snapshot_id
        )[1]

    def authorize_robot_asset(self, selection, asset_id, *, auth, snapshot_token, snapshot_id):
        revision, view = self._scene(
            selection, auth=auth, snapshot_token=snapshot_token, snapshot_id=snapshot_id
        )
        return self.scenes.authorize_asset(revision, view, asset_id, auth)
