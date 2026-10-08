"""Real Episode and historical Lance adapters for the same logical sample contract."""

from hc_data_platform.aligned_media.audit import AlignedMediaAuthorizationAuditEvent
from hc_data_platform.aligned_media.models import AlignedMediaScopeV1, AlignedMediaSelectorV1
from hc_data_platform.capabilities.registry import (
    CapabilityDescriptor,
    ImplementationRef,
    VersionedRegistry,
)
from hc_data_platform.core.context import current_request_context
from hc_data_platform.logical_data.models import LogicalMediaGrant, LogicalSample, LogicalStream
from hc_data_platform.security.http import authorize_scope


def _descriptor(stream, binding):
    return LogicalStream(
        stream_id=stream.episode_stream_id,
        name=stream.channel_path,
        kind=stream.kind,
        value_kind=getattr(binding, "value_kind", None),
        sample_count=binding.end_step - binding.start_step,
        temporal=True,
        start_ns=stream.t_start_ns,
        end_ns=stream.t_end_ns,
    )


def _samples(stream, binding, rows):
    key = getattr(binding, "modality_key", stream.channel_path)
    result = []
    for row in rows:
        index = row.step_index - binding.start_step
        # Existing serializers already bound binary data. Media storage locators
        # never become logical sample values or browser-owned dispatch decisions.
        values = row.model_dump(mode="json")["modalities"]
        if key not in values:
            raise ValueError("committed sample is missing the bound logical stream")
        value = values[key]
        if getattr(binding, "value_kind", None) is None:
            value = {
                "kind": "media_frame",
                "stream_id": stream.episode_stream_id,
                "sample_index": index,
            }
        result.append(
            LogicalSample(
                sample_id=f"{stream.episode_stream_id}/{index}",
                sample_index=index,
                timestamp_ns=str(row.timestamp_ns),
                value=value,
                valid=row.sample_valid and row.valid.get(key, False),
                repeated=row.repeated.get(key, False),
                source_timestamps_ns=tuple(str(t) for t in row.source_timestamps_ns.get(key, ())),
                time_error_ns=str(row.time_error_ns[key])
                if row.time_error_ns.get(key) is not None
                else None,
            )
        )
    return tuple(result)


class EpisodeSampleAdapter:
    ref = ImplementationRef("episode.logical-samples", "1")

    def __init__(self, reader, previews):
        self.reader, self.previews = reader, previews

    @staticmethod
    def matches(stream):
        return stream.package_binding is not None

    def describe(self, stream):
        return _descriptor(stream, stream.package_binding)

    def read(self, revision, stream, start, end):
        binding = stream.package_binding
        data = binding.data
        window = self.reader.read_steps(
            data.identity,
            data.data_version_id,
            binding.start_step + start,
            binding.start_step + end,
            columns=(binding.modality_key,),
        )
        if window.data != data:
            raise ValueError("Episode reader returned another immutable data version")
        return _samples(stream, binding, window.steps)

    def authorize_media(self, revision, stream, *, auth, snapshot_id):
        binding = stream.package_binding
        if binding.value_kind is not None:
            raise ValueError("logical stream is not playable media")
        grant = self.previews.authorize(
            binding.data.identity,
            binding.data.data_version_id,
            binding.feature_key,
            actor_id=auth.subject_id,
        )
        if grant.data != binding.data or binding.end_step > grant.frame_count:
            raise ValueError("preview differs from the frozen stream binding")
        return LogicalMediaGrant(
            snapshot_id=snapshot_id,
            stream_id=stream.episode_stream_id,
            kind="depth-preview" if grant.kind == "depth-preview" else "video",
            media_url=grant.media_url,
            expires_at=grant.expires_at,
            sample_count=binding.end_step - binding.start_step,
            sample_rate_hz=grant.fps,
            media_start_seconds=binding.start_step / grant.fps,
            width=grant.width,
            height=grant.height,
            media_sha256=grant.media_sha256,
            depth_min_m=grant.depth_min_m,
            depth_max_m=grant.depth_max_m,
            colorizer=grant.colorizer,
        )


class LanceSampleAdapter:
    ref = ImplementationRef("lance.logical-samples", "1")

    def __init__(self, reader, media, audit):
        self.reader, self.media, self.audit = reader, media, audit

    @staticmethod
    def matches(stream):
        return stream.package_binding is None and (
            stream.data_binding is not None or stream.aligned_media_binding is not None
        )

    def describe(self, stream):
        return _descriptor(stream, stream.data_binding or stream.aligned_media_binding)

    def read(self, revision, stream, start, end):
        binding = stream.data_binding or stream.aligned_media_binding
        version = binding.lance_version if stream.data_binding else binding.dataset_version
        key = getattr(binding, "modality_key", stream.channel_path)
        window = self.reader.read_steps(
            revision.dataset_id,
            binding.rollout_id,
            binding.start_step + start,
            binding.start_step + end,
            project_id=revision.scope.project_id,
            version=version,
            columns=(key,),
        )
        if (
            window.project_id != revision.scope.project_id
            or window.dataset_id != revision.dataset_id
            or window.dataset_version != version
            or window.rollout_id != binding.rollout_id
        ):
            raise ValueError("Lance reader returned another immutable data version")
        return _samples(stream, binding, window.steps)

    def authorize_media(self, revision, stream, *, auth, snapshot_id):
        binding = stream.aligned_media_binding
        if binding is None:
            raise ValueError("logical stream is not playable media")
        scope = revision.scope
        authorize_scope(
            auth, scope.project_id, "episode.read", scope.region_code, scope.organization_id
        )
        grant = self.media.authorize(
            AlignedMediaScopeV1(**scope.model_dump()),
            AlignedMediaSelectorV1(
                project_id=scope.project_id,
                dataset_id=revision.dataset_id,
                rollout_id=binding.rollout_id,
                dataset_version=binding.dataset_version,
                camera_id=binding.camera_id,
            ),
        )
        timeline = grant.timeline
        if grant.artifact_id != binding.artifact_id or not (
            timeline.first_step
            <= binding.start_step
            < binding.end_step
            <= timeline.first_step + timeline.frame_count
        ):
            raise ValueError("media receipt differs from the frozen stream binding")
        original = timeline.original_source
        offset = (original.start_seconds if original else 0) + (
            binding.start_step - timeline.first_step
        ) / timeline.fps
        self.audit.append_authorization(
            AlignedMediaAuthorizationAuditEvent(
                project_id=scope.project_id,
                region_code=scope.region_code,
                actor_id=auth.subject_id,
                request_id=current_request_context().request_id,
                artifact_id=grant.artifact_id,
                dataset_id=grant.dataset_id,
                rollout_id=grant.rollout_id,
                dataset_version=grant.dataset_version,
                camera_id=grant.camera_id,
                grant_expires_at=grant.expires_at,
            )
        )
        return LogicalMediaGrant(
            snapshot_id=snapshot_id,
            stream_id=stream.episode_stream_id,
            kind="video",
            media_url=grant.media_url,
            expires_at=grant.expires_at,
            sample_count=binding.end_step - binding.start_step,
            sample_rate_hz=timeline.fps,
            media_start_seconds=offset,
            width=grant.width,
            height=grant.height,
            media_sha256=original.content_sha256 if original else None,
        )


class SampleAdapters:
    def __init__(self, adapters):
        self.registry = VersionedRegistry()
        self.adapters = tuple(adapters)
        for adapter in self.adapters:
            self.registry.register(CapabilityDescriptor(adapter.ref), adapter)

    def for_stream(self, stream):
        matches = [a for a in self.adapters if a.matches(stream)]
        if len(matches) != 1:
            raise ValueError("logical stream requires one unambiguous registered reader")
        ref = matches[0].ref
        return self.registry.select(
            ref.key, version=ref.version, available_inputs=frozenset()
        ).implementation
