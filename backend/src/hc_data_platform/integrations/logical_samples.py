"""Real Episode and historical Lance adapters for the same logical sample contract."""

from hc_data_platform.capabilities.registry import (
    CapabilityDescriptor,
    ImplementationRef,
    VersionedRegistry,
)
from hc_data_platform.logical_data.models import LogicalSample, LogicalStream


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

    def __init__(self, reader):
        self.reader = reader

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


class LanceSampleAdapter:
    ref = ImplementationRef("lance.logical-samples", "1")

    def __init__(self, reader):
        self.reader = reader

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
