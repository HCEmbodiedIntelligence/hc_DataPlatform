"""Exact-integer fixed-frequency timeline and strategy-specific alignment."""

from __future__ import annotations

import bisect
import hashlib
from collections.abc import Iterator
from typing import Any

from .canonical import canonical_json_bytes
from .models import (
    AlignedFragmentManifestV1,
    AlignedRowV1,
    AlignedValueV1,
    AlignmentInputV1,
    AlignmentProfileV1,
    AlignmentStrategy,
    ModalityKind,
    ModalityStreamV1,
    TimedSampleV1,
)
from .ports import FakeFragmentWriter, FragmentWriterPort

_DEFAULT_STRATEGIES: dict[ModalityKind, AlignmentStrategy] = {
    ModalityKind.IMAGE: AlignmentStrategy.NEAREST,
    ModalityKind.POINT_CLOUD: AlignmentStrategy.NEAREST,
    ModalityKind.CONTINUOUS: AlignmentStrategy.LINEAR,
    ModalityKind.ACTION: AlignmentStrategy.CAUSAL,
    ModalityKind.DISCRETE: AlignmentStrategy.RECENT,
    ModalityKind.IMU: AlignmentStrategy.WINDOW_MEAN,
    ModalityKind.FORCE: AlignmentStrategy.WINDOW_MEAN,
}

_NANOSECONDS_PER_SECOND = 1_000_000_000


class AlignmentEngine:
    def align_to_writer(
        self,
        data: AlignmentInputV1,
        profile: AlignmentProfileV1,
        writer: FragmentWriterPort,
    ) -> AlignedFragmentManifestV1:
        missing = profile.required_modalities - data.streams.keys()
        if missing:
            raise ValueError(f"required modality streams are absent: {sorted(missing)}")
        configured = profile.stream_strategies.keys() | profile.stream_tolerance_ns.keys()
        unknown = configured - data.streams.keys()
        if unknown:
            raise ValueError(f"alignment profile configures absent streams: {sorted(unknown)}")
        schema_sha256 = self._schema_hash(data, profile)
        digest = hashlib.sha256()
        row_count = 0
        writer.begin(rollout_id=data.rollout_id, attempt_id=data.attempt_id)
        try:
            for row in self.iter_rows(data, profile):
                writer.write_row(row)
                digest.update(canonical_json_bytes(row.model_dump(mode="python")))
                digest.update(b"\n")
                row_count += 1
            content_sha256 = digest.hexdigest()
            staging_uri = writer.commit(
                row_count=row_count,
                content_sha256=content_sha256,
                schema_sha256=schema_sha256,
            )
        except Exception:
            writer.abort()
            raise
        return AlignedFragmentManifestV1(
            rollout_id=data.rollout_id,
            source_sha256=data.source_sha256,
            attempt_id=data.attempt_id,
            profile_id=profile.profile_id,
            converter_version=profile.converter_version,
            frequency_hz=profile.frequency_hz,
            row_count=row_count,
            schema_sha256=schema_sha256,
            content_sha256=content_sha256,
            staging_uri=staging_uri,
        )

    def align_in_memory(
        self,
        data: AlignmentInputV1,
        profile: AlignmentProfileV1,
    ) -> tuple[AlignedFragmentManifestV1, tuple[AlignedRowV1, ...]]:
        writer = FakeFragmentWriter()
        manifest = self.align_to_writer(data, profile, writer)
        return manifest, tuple(writer.rows)

    def iter_rows(
        self, data: AlignmentInputV1, profile: AlignmentProfileV1
    ) -> Iterator[AlignedRowV1]:
        previous_sources: dict[str, tuple[int, ...]] = {}
        for step_index, timestamp_ns in enumerate(self.iter_timestamps(data, profile)):
            modalities: dict[str, AlignedValueV1] = {}
            for name, stream in sorted(data.streams.items()):
                strategy = profile.stream_strategies.get(name, _DEFAULT_STRATEGIES[stream.kind])
                tolerance = profile.stream_tolerance_ns.get(name, profile.default_tolerance_ns)
                aligned = self._select(stream, timestamp_ns, strategy, tolerance)
                repeated = (
                    aligned.valid
                    and len(aligned.source_timestamps_ns) == 1
                    and previous_sources.get(name) == aligned.source_timestamps_ns
                )
                if aligned.valid and len(aligned.source_timestamps_ns) == 1:
                    previous_sources[name] = aligned.source_timestamps_ns
                else:
                    previous_sources.pop(name, None)
                modalities[name] = aligned.model_copy(update={"repeated": repeated})
            sample_valid = all(modalities[name].valid for name in profile.required_modalities)
            yield AlignedRowV1(
                rollout_id=data.rollout_id,
                step_index=step_index,
                timestamp_ns=timestamp_ns,
                modalities=modalities,
                sample_valid=sample_valid,
            )

    @staticmethod
    def iter_timestamps(data: AlignmentInputV1, profile: AlignmentProfileV1) -> Iterator[int]:
        """Yield an exact integer [start, end) grid without accumulated float error."""

        duration_ns = data.end_ns - data.start_ns
        row_count = (
            duration_ns * profile.frequency_hz + _NANOSECONDS_PER_SECOND - 1
        ) // _NANOSECONDS_PER_SECOND
        for step_index in range(row_count):
            timestamp_ns = (
                data.start_ns + step_index * _NANOSECONDS_PER_SECOND // profile.frequency_hz
            )
            if timestamp_ns < data.end_ns:
                yield timestamp_ns

    def _select(
        self,
        stream: ModalityStreamV1,
        target_ns: int,
        strategy: AlignmentStrategy,
        tolerance_ns: int,
    ) -> AlignedValueV1:
        if not stream.samples:
            return self._invalid(strategy)
        if strategy == AlignmentStrategy.NEAREST:
            selected = self._nearest_sample(stream.samples, target_ns)
            return self._single(selected, target_ns, strategy, tolerance_ns)
        if strategy in (AlignmentStrategy.CAUSAL, AlignmentStrategy.RECENT):
            index = (
                bisect.bisect_right(
                    stream.samples, target_ns, key=lambda sample: sample.timestamp_ns
                )
                - 1
            )
            if index < 0:
                first = stream.samples[0]
                return self._invalid(
                    strategy,
                    source_timestamps_ns=(first.timestamp_ns,),
                    time_error_ns=first.timestamp_ns - target_ns,
                )
            return self._single(stream.samples[index], target_ns, strategy, tolerance_ns)
        if strategy == AlignmentStrategy.LINEAR:
            return self._linear(stream.samples, target_ns, tolerance_ns)
        if strategy == AlignmentStrategy.WINDOW_MEAN:
            return self._window_mean(stream.samples, target_ns, tolerance_ns)
        raise ValueError(f"unsupported alignment strategy: {strategy}")

    @staticmethod
    def _single(
        sample: TimedSampleV1,
        target_ns: int,
        strategy: AlignmentStrategy,
        tolerance_ns: int,
    ) -> AlignedValueV1:
        error = abs(sample.timestamp_ns - target_ns)
        if error > tolerance_ns:
            return AlignmentEngine._invalid(
                strategy,
                source_timestamps_ns=(sample.timestamp_ns,),
                time_error_ns=error,
            )
        return AlignedValueV1(
            value=sample.value,
            source_timestamps_ns=(sample.timestamp_ns,),
            time_error_ns=error,
            valid=True,
            strategy=strategy,
        )

    def _linear(
        self,
        samples: tuple[TimedSampleV1, ...],
        target_ns: int,
        tolerance_ns: int,
    ) -> AlignedValueV1:
        index = bisect.bisect_left(samples, target_ns, key=lambda sample: sample.timestamp_ns)
        if index < len(samples) and samples[index].timestamp_ns == target_ns:
            return self._single(samples[index], target_ns, AlignmentStrategy.LINEAR, tolerance_ns)
        if index == 0 or index == len(samples):
            nearest = samples[0] if index == 0 else samples[-1]
            return self._invalid(
                AlignmentStrategy.LINEAR,
                source_timestamps_ns=(nearest.timestamp_ns,),
                time_error_ns=abs(nearest.timestamp_ns - target_ns),
            )
        left, right = samples[index - 1], samples[index]
        error = max(target_ns - left.timestamp_ns, right.timestamp_ns - target_ns)
        source_timestamps_ns = (left.timestamp_ns, right.timestamp_ns)
        if error > tolerance_ns:
            return self._invalid(
                AlignmentStrategy.LINEAR,
                source_timestamps_ns=source_timestamps_ns,
                time_error_ns=error,
            )
        ratio = (target_ns - left.timestamp_ns) / (right.timestamp_ns - left.timestamp_ns)
        try:
            value = self._interpolate(left.value, right.value, ratio)
        except (TypeError, ValueError):
            return self._invalid(
                AlignmentStrategy.LINEAR,
                source_timestamps_ns=source_timestamps_ns,
                time_error_ns=error,
            )
        return AlignedValueV1(
            value=value,
            source_timestamps_ns=source_timestamps_ns,
            time_error_ns=error,
            valid=True,
            strategy=AlignmentStrategy.LINEAR,
        )

    def _window_mean(
        self,
        samples: tuple[TimedSampleV1, ...],
        target_ns: int,
        tolerance_ns: int,
    ) -> AlignedValueV1:
        left = bisect.bisect_left(
            samples,
            target_ns - tolerance_ns,
            key=lambda sample: sample.timestamp_ns,
        )
        right = bisect.bisect_right(
            samples,
            target_ns + tolerance_ns,
            key=lambda sample: sample.timestamp_ns,
        )
        selected = samples[left:right]
        if not selected:
            nearest = self._nearest_sample(samples, target_ns)
            return self._invalid(
                AlignmentStrategy.WINDOW_MEAN,
                source_timestamps_ns=(nearest.timestamp_ns,),
                time_error_ns=abs(nearest.timestamp_ns - target_ns),
            )
        source_times = tuple(item.timestamp_ns for item in selected)
        error = max(abs(item - target_ns) for item in source_times)
        try:
            value = self._mean([item.value for item in selected])
        except (TypeError, ValueError):
            return self._invalid(
                AlignmentStrategy.WINDOW_MEAN,
                source_timestamps_ns=source_times,
                time_error_ns=error,
            )
        return AlignedValueV1(
            value=value,
            source_timestamps_ns=source_times,
            time_error_ns=error,
            valid=True,
            strategy=AlignmentStrategy.WINDOW_MEAN,
        )

    @staticmethod
    def _invalid(
        strategy: AlignmentStrategy,
        *,
        source_timestamps_ns: tuple[int, ...] = (),
        time_error_ns: int | None = None,
    ) -> AlignedValueV1:
        return AlignedValueV1(
            value=None,
            source_timestamps_ns=source_timestamps_ns,
            time_error_ns=time_error_ns,
            valid=False,
            repeated=False,
            strategy=strategy,
        )

    @staticmethod
    def _nearest_sample(samples: tuple[TimedSampleV1, ...], target_ns: int) -> TimedSampleV1:
        index = bisect.bisect_left(samples, target_ns, key=lambda sample: sample.timestamp_ns)
        candidates = [
            candidate for candidate in (index - 1, index) if 0 <= candidate < len(samples)
        ]
        selected = min(
            candidates,
            key=lambda candidate: (
                abs(samples[candidate].timestamp_ns - target_ns),
                samples[candidate].timestamp_ns,
            ),
        )
        return samples[selected]

    def _interpolate(self, left: Any, right: Any, ratio: float) -> Any:
        if isinstance(left, bool) or isinstance(right, bool):
            raise TypeError("booleans are not continuous")
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return left + (right - left) * ratio
        if (
            isinstance(left, (list, tuple))
            and isinstance(right, (list, tuple))
            and len(left) == len(right)
        ):
            return [self._interpolate(a, b, ratio) for a, b in zip(left, right, strict=True)]
        if isinstance(left, dict) and isinstance(right, dict) and left.keys() == right.keys():
            return {key: self._interpolate(left[key], right[key], ratio) for key in sorted(left)}
        raise TypeError("values are not shape-compatible numeric data")

    def _mean(self, values: list[Any]) -> Any:
        if not values:
            raise ValueError("cannot average an empty window")
        if any(isinstance(value, bool) for value in values):
            raise TypeError("booleans cannot be averaged")
        if all(isinstance(value, (int, float)) for value in values):
            return sum(values) / len(values)
        if all(isinstance(value, (list, tuple)) for value in values):
            lengths = {len(value) for value in values}
            if len(lengths) != 1:
                raise TypeError("window values have different shapes")
            return [
                self._mean([value[index] for value in values]) for index in range(len(values[0]))
            ]
        if all(isinstance(value, dict) for value in values):
            keys = [set(value) for value in values]
            if any(key_set != keys[0] for key_set in keys[1:]):
                raise TypeError("window values have different keys")
            return {key: self._mean([value[key] for value in values]) for key in sorted(keys[0])}
        raise TypeError("window values are not numeric")

    def _schema_hash(self, data: AlignmentInputV1, profile: AlignmentProfileV1) -> str:
        schema = {
            "format": "aligned-row/v1",
            "frequency_hz": profile.frequency_hz,
            "fields": {
                "modalities": "map<string,aligned-value/v1>",
                "rollout_id": "string",
                "sample_valid": "bool",
                "step_index": "int64",
                "timestamp_ns": "int64",
            },
            "streams": [
                {
                    "name": name,
                    "kind": stream.kind.value,
                    "strategy": profile.stream_strategies.get(
                        name, _DEFAULT_STRATEGIES[stream.kind]
                    ).value,
                }
                for name, stream in sorted(data.streams.items())
            ],
        }
        return hashlib.sha256(canonical_json_bytes(schema)).hexdigest()
