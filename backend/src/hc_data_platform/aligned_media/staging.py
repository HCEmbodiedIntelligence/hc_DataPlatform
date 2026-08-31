from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from hc_data_platform.alignment.canonical import denormalize_from_json
from hc_data_platform.alignment.models import AlignedValueV1
from hc_data_platform.workflow.projection_store import ProjectionArtifactStorePort

from .models import AlignedMediaGenerationRequestV1, AlignmentCameraStagingArtifactV1
from .ports import AlignedFrameV1


@dataclass(frozen=True, slots=True)
class AlignedCameraFrame:
    step_index: int
    timestamp_ns: int
    image: bytes | None
    valid: bool
    repeated: bool


class ArrowAlignedFrameReader:
    """Read one camera lazily from short-lived Arrow staging, never from Lance."""

    def __init__(self, store: ProjectionArtifactStorePort) -> None:
        self._store = store

    def read_camera_frames(
        self, request: AlignedMediaGenerationRequestV1
    ) -> Iterator[AlignedFrameV1]:
        staging = request.alignment
        shard = staging.camera_shards.get(request.camera_id)
        if shard is not None:
            yield from self._read_camera_shard(request, shard)
            return
        with self._store.local_file(
            staging.object_key,
            expected_sha256=staging.content_sha256,
            expected_size=staging.size_bytes,
        ) as path:
            try:
                import pyarrow as pa
                import pyarrow.ipc as ipc
            except ImportError as exc:
                raise RuntimeError("aligned media staging requires PyArrow") from exc

            observed = 0
            with pa.memory_map(str(path), "r") as source:
                reader = ipc.open_file(source)
                for batch_index in range(reader.num_record_batches):
                    batch = reader.get_batch(batch_index)
                    rollouts = batch.column("rollout_id")
                    indexes = batch.column("step_index")
                    timestamps = batch.column("timestamp_ns")
                    modalities_json = batch.column("modalities_json")
                    for row_index in range(batch.num_rows):
                        if str(rollouts[row_index].as_py()) != request.rollout_id:
                            raise ValueError("aligned staging rollout does not match media request")
                        step_index = int(indexes[row_index].as_py())
                        if step_index != observed:
                            raise ValueError("aligned staging steps must be contiguous from zero")
                        encoded = modalities_json[row_index].as_py()
                        payload = denormalize_from_json(json.loads(bytes(encoded).decode("utf-8")))
                        if not isinstance(payload, dict):
                            raise ValueError("aligned staging modalities must be an object")
                        raw_value: Any = payload.get(request.camera_id)
                        if raw_value is None:
                            raise ValueError("camera is absent from aligned staging")
                        aligned = AlignedValueV1.model_validate(raw_value)
                        image = aligned.value if isinstance(aligned.value, bytes) else None
                        yield AlignedCameraFrame(
                            step_index=step_index,
                            timestamp_ns=int(timestamps[row_index].as_py()),
                            image=image,
                            valid=aligned.valid and image is not None,
                            repeated=aligned.repeated,
                        )
                        observed += 1
                    del batch
                    pa.default_memory_pool().release_unused()
            if observed != staging.row_count:
                raise ValueError("aligned staging row count does not match its manifest")

    def _read_camera_shard(
        self,
        request: AlignedMediaGenerationRequestV1,
        shard: AlignmentCameraStagingArtifactV1,
    ) -> Iterator[AlignedCameraFrame]:
        with self._store.local_file(
            shard.object_key,
            expected_sha256=shard.content_sha256,
            expected_size=shard.size_bytes,
        ) as path:
            try:
                import pyarrow as pa
                import pyarrow.ipc as ipc
            except ImportError as exc:
                raise RuntimeError("aligned media staging requires PyArrow") from exc

            observed = 0
            with pa.memory_map(str(path), "r") as source:
                reader = ipc.open_file(source)
                metadata = reader.schema.metadata or {}
                if metadata.get(b"hc.schema") != b"aligned-camera/v1":
                    raise ValueError("camera staging schema marker is invalid")
                if metadata.get(b"hc.camera_id", b"").decode() != request.camera_id:
                    raise ValueError("camera staging shard does not match media request")
                for batch_index in range(reader.num_record_batches):
                    batch = reader.get_batch(batch_index)
                    rollouts = batch.column("rollout_id")
                    indexes = batch.column("step_index")
                    timestamps = batch.column("timestamp_ns")
                    images = batch.column("image")
                    valid = batch.column("valid")
                    repeated = batch.column("repeated")
                    for row_index in range(batch.num_rows):
                        if str(rollouts[row_index].as_py()) != request.rollout_id:
                            raise ValueError("camera staging rollout does not match media request")
                        step_index = int(indexes[row_index].as_py())
                        if step_index != observed:
                            raise ValueError("camera staging steps must be contiguous from zero")
                        raw_image = images[row_index].as_py()
                        image = None if raw_image is None else bytes(raw_image)
                        yield AlignedCameraFrame(
                            step_index=step_index,
                            timestamp_ns=int(timestamps[row_index].as_py()),
                            image=image,
                            valid=bool(valid[row_index].as_py()) and image is not None,
                            repeated=bool(repeated[row_index].as_py()),
                        )
                        observed += 1
                    del batch
                    pa.default_memory_pool().release_unused()
            if observed != shard.row_count or observed != request.alignment.row_count:
                raise ValueError("camera staging row count does not match its manifest")
