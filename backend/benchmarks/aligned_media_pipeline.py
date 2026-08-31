#!/usr/bin/env python3
"""Local component evidence for the canonical aligned-MP4 ingest architecture.

The default run uses four distinct camera streams at 30 Hz for 60 seconds,
upload concurrency 1/2/4, a global FFmpeg capacity of two, and 100 concurrent
READY authorize + HTTP Range clients per case. It deliberately labels output
as local component evidence; production capacity needs the deployed object
store, PostgreSQL leases, network, and pod limits.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import statistics
import tempfile
import threading
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from mcap.reader import make_reader
from mcap.writer import CompressionType, Writer
from PIL import Image, ImageDraw

from hc_data_platform.aligned_media.artifact_store import LocalAlignedMediaArtifactStore
from hc_data_platform.aligned_media.audit import InMemoryAlignedMediaAuditRecorder
from hc_data_platform.aligned_media.capacity import InMemoryMediaCapacityGate
from hc_data_platform.aligned_media.encoder import FFmpegMp4Encoder
from hc_data_platform.aligned_media.memory import InMemoryAlignedMediaRepository
from hc_data_platform.aligned_media.models import (
    AlignedMediaArtifactV1,
    AlignedMediaGenerationRequestV1,
    AlignedMediaScopeV1,
    AlignedMediaSelectorV1,
    AlignmentStagingArtifactV1,
    EncodedAlignedMediaV1,
)
from hc_data_platform.aligned_media.router import configure_aligned_media
from hc_data_platform.aligned_media.router import router as aligned_media_router
from hc_data_platform.aligned_media.service import (
    AlignedMediaAuthorizationService,
    AlignedMediaGenerationService,
    aligned_media_artifact_key,
)
from hc_data_platform.aligned_media.staging import ArrowAlignedFrameReader
from hc_data_platform.alignment.arrow_writer import ArrowFragmentWriter
from hc_data_platform.alignment.engine import AlignmentEngine
from hc_data_platform.alignment.models import AlignmentProfileV1, ModalityKind, TimedSampleV1
from hc_data_platform.lance_catalog import (
    AlignedFragmentManifestV1,
    DatasetSchemaSnapshot,
    InMemoryCatalogRepository,
    InMemoryDatasetWriterLock,
    LanceAdapter,
    LanceCatalogService,
    compute_fragment_hash,
)
from hc_data_platform.quality.engine import QualityEngine
from hc_data_platform.quality.models import (
    ActionQualityProfileV1,
    QualityInputV1,
    QualityProfileV1,
    QualityStreamObservationV1,
)
from hc_data_platform.runtime import _ArrowStepSequence
from hc_data_platform.security import AuthContext
from hc_data_platform.verification.engine import McapVerifier
from hc_data_platform.verification.ports import RegisteredDecoderProbe
from hc_data_platform.workflow.ingest_plan import (
    _ArrowProjectionWriter,
    _decode_image_projection,
    _projection_rows,
    _projection_sample_rows,
)
from hc_data_platform.workflow.projection_store import LocalProjectionArtifactStore

CAMERAS = (
    "/camera/front/image",
    "/camera/rear/image",
    "/camera/left/image",
    "/camera/right/image",
)
WIDTH = 320
HEIGHT = 180
FREQUENCY_HZ = 30
DEFAULT_DURATION_SECONDS = 60
DEFAULT_CONCURRENCY = (1, 2, 4)
DEFAULT_READY_CLIENTS = 100
GLOBAL_MEDIA_CAPACITY = 2


@dataclass(frozen=True, slots=True)
class PreparedPipeline:
    root: Path
    mcap_path: Path
    projection_path: Path
    source_sha256: str
    rollout_id: str
    frame_count: int
    unique_jpegs: int
    staging: AlignmentStagingArtifactV1
    staging_store: LocalProjectionArtifactStore
    aligned_manifest: Any


class LocalRawStorage:
    def __init__(self, path: Path) -> None:
        self.path = path

    def open_reader(self, object_key: str):  # type: ignore[no-untyped-def]
        if object_key != "raw/source.mcap":
            raise FileNotFoundError(object_key)
        return self.path.open("rb")


class CountingRepository(InMemoryAlignedMediaRepository):
    def __init__(self) -> None:
        super().__init__()
        self.ensure_calls = 0
        self.find_calls = 0
        self.job_ids: set[str] = set()
        self._count_lock = threading.Lock()

    def ensure_generation(self, **kwargs):  # type: ignore[no-untyped-def]
        result = super().ensure_generation(**kwargs)
        with self._count_lock:
            self.ensure_calls += 1
            if result[1] is not None:
                self.job_ids.add(result[1].job_id)
        return result

    def find_by_selector(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        with self._count_lock:
            self.find_calls += 1
        return super().find_by_selector(*args, **kwargs)


class MeasuredEncoder:
    def __init__(self, delegate: FFmpegMp4Encoder) -> None:
        self.delegate = delegate
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.queue_waits: list[float] = []
        self._submitted: dict[str, float] = {}
        self._lock = threading.Lock()

    def submitted(self, artifact_key: str) -> None:
        with self._lock:
            self._submitted[artifact_key] = time.perf_counter()

    def encode(
        self,
        *,
        artifact_key: str,
        request: AlignedMediaGenerationRequestV1,
        frames: Iterable[object],
        cancelled: Any = None,
    ) -> EncodedAlignedMediaV1:
        started = time.perf_counter()
        with self._lock:
            submitted = self._submitted.pop(artifact_key)
            self.queue_waits.append(started - submitted)
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            return self.delegate.encode(
                artifact_key=artifact_key,
                request=request,
                frames=frames,  # type: ignore[arg-type]
                cancelled=cancelled,
            )
        finally:
            with self._lock:
                self.active -= 1

    def cleanup(self, encoded: EncodedAlignedMediaV1) -> None:
        self.delegate.cleanup(encoded)


class CgroupSampler:
    """Sample cgroup-v2 CPU/anonymous memory/IO and temporary disk."""

    def __init__(self, temporary_root: Path) -> None:
        self._temporary_root = temporary_root
        self._root = _cgroup_root()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._samples: list[dict[str, int]] = []
        self._checkpoints: dict[str, dict[str, int | None]] = {}

    def __enter__(self) -> CgroupSampler:
        self._samples.append(self._read())
        self.checkpoint("start")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._samples.append(self._read())

    def checkpoint(self, name: str) -> None:
        sample = self._read()
        self._checkpoints[name] = {
            "cgroup_anon_bytes": sample["anon"],
            "cgroup_memory_bytes": (
                sample["memory"] if (self._root / "memory.current").exists() else None
            ),
            "process_rss_bytes": _process_rss_bytes(),
            "temporary_disk_bytes": sample["temporary"],
        }

    def result(self) -> dict[str, object]:
        first, last = self._samples[0], self._samples[-1]
        memory_current_available = (self._root / "memory.current").exists()
        anon_peak = max(item["anon"] for item in self._samples)
        return {
            "cgroup_path": str(self._root),
            "cpu_seconds": round((last["cpu_usec"] - first["cpu_usec"]) / 1_000_000, 6),
            "cgroup_anon_peak_bytes": anon_peak,
            "cgroup_anon_peak_delta_bytes": max(0, anon_peak - first["anon"]),
            "cgroup_memory_peak_bytes": (
                max(item["memory"] for item in self._samples) if memory_current_available else None
            ),
            "cgroup_memory_current_available": memory_current_available,
            "process_rss_peak_bytes": max(item["process_rss"] for item in self._samples),
            "temporary_disk_peak_bytes": max(item["temporary"] for item in self._samples),
            "io_read_bytes": max(0, last["io_read"] - first["io_read"]),
            "io_write_bytes": max(0, last["io_write"] - first["io_write"]),
            "network_rx_bytes": max(0, last["network_rx"] - first["network_rx"]),
            "network_tx_bytes": max(0, last["network_tx"] - first["network_tx"]),
            "samples": len(self._samples),
            "stage_resource_snapshots": self._checkpoints,
            "cgroup_scope_note": (
                "This host exposes only the root cgroup; process RSS is the isolated "
                "benchmark-process memory measure."
            ),
        }

    def _run(self) -> None:
        while not self._stop.wait(0.25):
            self._samples.append(self._read())

    def _read(self) -> dict[str, int]:
        cpu = _key_values(self._root / "cpu.stat").get("usage_usec", 0)
        memory = _integer(self._root / "memory.current")
        memory_stats = _key_values(self._root / "memory.stat")
        io_read = io_write = 0
        if (self._root / "io.stat").exists():
            for line in (self._root / "io.stat").read_text().splitlines():
                values = _inline_values(line)
                io_read += values.get("rbytes", 0)
                io_write += values.get("wbytes", 0)
        network_rx, network_tx = _network_bytes()
        return {
            "cpu_usec": cpu,
            "memory": memory,
            "anon": memory_stats.get("anon", 0),
            "process_rss": _process_rss_bytes(),
            "io_read": io_read,
            "io_write": io_write,
            "network_rx": network_rx,
            "network_tx": network_tx,
            "temporary": _tree_bytes(self._temporary_root),
        }


def prepare_pipeline(
    root: Path, *, duration_seconds: int
) -> tuple[PreparedPipeline, dict[str, Any]]:
    frame_count = duration_seconds * FREQUENCY_HZ
    mcap_path = root / "raw" / "source.mcap"
    mcap_path.parent.mkdir(parents=True)
    projection_path = root / "projection" / "projection.arrow"
    projection_path.parent.mkdir(parents=True)
    rollout_id = f"capacity-rollout-{uuid4().hex[:12]}"
    started = time.perf_counter()
    with CgroupSampler(root) as sampler:
        unique_jpegs = _write_mcap(mcap_path, frame_count=frame_count)
        source_sha256 = _file_sha256(mcap_path)
        sampler.checkpoint("raw_mcap_written")
        verification = McapVerifier(
            LocalRawStorage(mcap_path),
            RegisteredDecoderProbe(
                {("json", "jsonschema"): lambda _schema, data: json.loads(data)}
            ),
        ).verify(
            rollout_id=rollout_id,
            object_key="raw/source.mcap",
            source_sha256=source_sha256,
            required_topics=set(CAMERAS),
        )
        if verification.status.value != "RAW_VERIFIED":
            raise RuntimeError("representative MCAP did not pass raw verification")
        sampler.checkpoint("raw_verified")
        _materialize_projection(mcap_path, projection_path, source_sha256=source_sha256)
        sampler.checkpoint("bounded_projection_written")
        quality = _quality(projection_path, source_sha256, frame_count, rollout_id)
        if quality.status.value == "REJECT" or (
            duration_seconds >= DEFAULT_DURATION_SECONDS and quality.status.value != "PASS"
        ):
            raise RuntimeError(
                f"representative camera-only projection failed QC: {quality.status.value}"
            )
        sampler.checkpoint("quality_gate_complete")
        aligned_manifest = _align(
            projection_path,
            root / "alignment-writer",
            source_sha256=source_sha256,
            rollout_id=rollout_id,
            frame_count=frame_count,
        )
        aligned_path = Path(aligned_manifest.staging_uri.removeprefix("file://"))
        aligned_sha = _file_sha256(aligned_path)
        staging_store = LocalProjectionArtifactStore(root / "staging-store")
        staging_key = f"staging/alignment/{rollout_id}/{aligned_sha}.arrow"
        aligned_size = staging_store.publish_file(staging_key, aligned_path, sha256=aligned_sha)
        staging = AlignmentStagingArtifactV1(
            object_key=staging_key,
            content_sha256=aligned_sha,
            size_bytes=aligned_size,
            row_count=aligned_manifest.row_count,
            alignment_version=(
                f"{aligned_manifest.converter_version}:{aligned_manifest.profile_id}"
            ),
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        )
        sampler.checkpoint("bounded_alignment_published")
    metrics = sampler.result()
    metrics.update(
        {
            "wall_seconds": round(time.perf_counter() - started, 6),
            "raw_verification_status": verification.status.value,
            "raw_message_count": verification.message_count,
            "raw_mcap_bytes": mcap_path.stat().st_size,
            "projection_bytes": projection_path.stat().st_size,
            "alignment_staging_bytes": staging.size_bytes,
            "alignment_row_count": staging.row_count,
            "quality_status": quality.status.value,
            "quality_finding_count": len(quality.findings),
            "unique_jpegs": unique_jpegs,
            "expected_unique_jpegs": frame_count * len(CAMERAS),
        }
    )
    if unique_jpegs != frame_count * len(CAMERAS):
        raise RuntimeError("representative camera JPEGs are not all distinct")
    return (
        PreparedPipeline(
            root=root,
            mcap_path=mcap_path,
            projection_path=projection_path,
            source_sha256=source_sha256,
            rollout_id=rollout_id,
            frame_count=frame_count,
            unique_jpegs=unique_jpegs,
            staging=staging,
            staging_store=staging_store,
            aligned_manifest=aligned_manifest,
        ),
        metrics,
    )


def generation_case(
    prepared: PreparedPipeline,
    *,
    upload_concurrency: int,
    ready_clients: int,
) -> dict[str, Any]:
    case_root = prepared.root / f"case-{upload_concurrency}"
    repository = CountingRepository()
    store = LocalAlignedMediaArtifactStore(case_root / "objects")
    encoder = MeasuredEncoder(FFmpegMp4Encoder(case_root / "ffmpeg", ffmpeg_threads=1))
    generation = AlignedMediaGenerationService(
        frame_reader=ArrowAlignedFrameReader(prepared.staging_store),
        encoder=encoder,
        repository=repository,
        store=store,
        capacity_gate=InMemoryMediaCapacityGate(GLOBAL_MEDIA_CAPACITY),
        heartbeat_interval=timedelta(seconds=5),
    )
    authorization = AlignedMediaAuthorizationService(repository=repository, store=store)
    audit = InMemoryAlignedMediaAuditRecorder()
    configure_aligned_media(authorization, store, audit)
    lance = LanceCatalogService(
        LanceAdapter(case_root / "lance"),
        InMemoryCatalogRepository(),
        InMemoryDatasetWriterLock(),
    )
    work: list[tuple[AlignedMediaScopeV1, tuple[AlignedMediaGenerationRequestV1, ...]]] = []
    for upload_index in range(upload_concurrency):
        dataset_id = f"capacity-dataset-c{upload_concurrency}-u{upload_index}"
        scope = AlignedMediaScopeV1(
            organization_id="capacity-organization",
            project_id="capacity-project",
            region_code="capacity-region",
        )
        snapshot = DatasetSchemaSnapshot.create(
            project_id=scope.project_id,
            dataset_id=dataset_id,
            schema_snapshot_id=f"{dataset_id}-four-camera-v1",
            frequency_hz=FREQUENCY_HZ,
            fields={camera: "json" for camera in CAMERAS},
        )
        lance.register_schema(snapshot)
        requests = tuple(
            AlignedMediaGenerationRequestV1(
                project_id=scope.project_id,
                dataset_id=dataset_id,
                rollout_id=prepared.rollout_id,
                expected_dataset_version=1,
                camera_id=camera,
                source_sha256=prepared.source_sha256,
                alignment=prepared.staging,
            )
            for camera in CAMERAS
        )
        work.append((scope, requests))

    generation_latencies: list[float] = []
    generation_failures: list[str] = []
    bundle_results: list[tuple[AlignedMediaScopeV1, tuple[AlignedMediaArtifactV1, ...]]] = []
    results_lock = threading.Lock()
    started = time.perf_counter()

    def generate_one(
        scope: AlignedMediaScopeV1, request: AlignedMediaGenerationRequestV1
    ) -> AlignedMediaArtifactV1:
        key = aligned_media_artifact_key(request)
        encoder.submitted(key)
        job_started = time.perf_counter()
        try:
            return generation.generate(scope, request)
        except Exception as exc:
            with results_lock:
                generation_failures.append(type(exc).__name__)
            raise
        finally:
            with results_lock:
                generation_latencies.append(time.perf_counter() - job_started)

    def run_upload(
        item: tuple[AlignedMediaScopeV1, tuple[AlignedMediaGenerationRequestV1, ...]],
    ) -> tuple[AlignedMediaScopeV1, tuple[AlignedMediaArtifactV1, ...]]:
        scope, requests = item
        artifacts: list[AlignedMediaArtifactV1] = []
        # Mirrors the workflow's fixed two-camera activity batches.
        for offset in range(0, len(requests), 2):
            with ThreadPoolExecutor(max_workers=2) as media_pool:
                artifacts.extend(
                    media_pool.map(
                        lambda request: generate_one(scope, request),
                        requests[offset : offset + 2],
                    )
                )
        artifact_tuple = tuple(artifacts)
        if {item.camera_id for item in artifact_tuple} != set(CAMERAS) or any(
            item.frame_count != prepared.frame_count
            or item.duration_seconds != prepared.frame_count / FREQUENCY_HZ
            or item.timeline is None
            or item.timeline.frame_count != prepared.frame_count
            or item.timeline.pts_time_base_numerator != 1
            or item.timeline.pts_time_base_denominator != FREQUENCY_HZ
            for item in artifact_tuple
        ):
            raise RuntimeError("four-camera media bundle has a non-identical timeline")
        dataset_id = requests[0].dataset_id
        snapshot = lance.schema_for(scope.project_id, dataset_id)
        if snapshot is None:
            raise RuntimeError("capacity Dataset schema disappeared")
        with prepared.staging_store.local_file(
            prepared.staging.object_key,
            expected_sha256=prepared.staging.content_sha256,
            expected_size=prepared.staging.size_bytes,
        ) as aligned_path:
            steps: Sequence[Any] = _ArrowStepSequence(
                aligned_path.resolve().as_uri(),
                prepared.frame_count,
                media_artifacts=artifact_tuple,
            )
            manifest = AlignedFragmentManifestV1(
                project_id=scope.project_id,
                dataset_id=dataset_id,
                schema_snapshot_id=snapshot.schema_snapshot_id,
                schema_fingerprint=snapshot.fingerprint,
                frequency_hz=FREQUENCY_HZ,
                rollout_id=prepared.rollout_id,
                source_sha256=prepared.source_sha256,
                converter_version=prepared.aligned_manifest.converter_version,
                attempt_id=f"capacity-{upload_concurrency}-{dataset_id}",
                fragment_uri=aligned_path.resolve().as_uri(),
                step_count=prepared.frame_count,
                content_hash=compute_fragment_hash(steps),
            )
            repository.begin_dataset_commit(
                scope=scope,
                artifact_ids=tuple(item.artifact_id for item in artifact_tuple),
                lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
                now=datetime.now(timezone.utc),
            )
            version, ready = lance.commit_fragment(manifest, steps)
        if version.version != 1 or ready.step_count != prepared.frame_count:
            raise RuntimeError("Lance commit returned an unexpected Dataset identity")
        repository.mark_dataset_committed(
            scope=scope,
            artifact_ids=tuple(item.artifact_id for item in artifact_tuple),
            now=datetime.now(timezone.utc),
        )
        return scope, artifact_tuple

    with CgroupSampler(case_root) as sampler:
        with ThreadPoolExecutor(max_workers=upload_concurrency) as upload_pool:
            bundle_results.extend(upload_pool.map(run_upload, work))
        sampler.checkpoint("four_camera_lance_bundles_committed")
        generation_elapsed = time.perf_counter() - started

        selected_scope, selected_artifacts = bundle_results[0]
        selected = selected_artifacts[0]
        if (
            selected.media_object_key is None
            or store.resolve_local_object(selected.media_object_key) is None
        ):
            raise RuntimeError("committed aligned MP4 is absent before authorization")
        selector = AlignedMediaSelectorV1(
            project_id=selected_scope.project_id,
            dataset_id=selected.dataset_id,
            rollout_id=selected.rollout_id,
            dataset_version=selected.dataset_version,
            camera_id=selected.camera_id,
        )
        auth = AuthContext(
            subject_id="capacity-client",
            project_ids=frozenset({selected_scope.project_id}),
            region_codes=frozenset({selected_scope.region_code}),
            roles=frozenset({"annotator"}),
            organization_ids=frozenset({selected_scope.organization_id}),
            organization_scope_triples=frozenset(
                {
                    (
                        selected_scope.organization_id,
                        selected_scope.project_id,
                        selected_scope.region_code,
                    )
                }
            ),
        )
        app = FastAPI()
        app.include_router(aligned_media_router)

        @app.middleware("http")
        async def install_auth(request: Request, call_next):  # type: ignore[no-untyped-def]
            request.state.auth_context = auth
            return await call_next(request)

        counters_before = {
            "active_ffmpeg": encoder.active,
            "encoder_calls": encoder.calls,
            "ensure_generation_calls": repository.ensure_calls,
            "internal_job_count": len(repository.job_ids),
            "lance_commit_count": len(bundle_results),
        }
        authorization_latencies: list[float] = []
        range_bytes: list[int] = []
        range_failures: list[str] = []
        with TestClient(app) as client:

            def authorize_and_range(_: int) -> None:
                request_started = time.perf_counter()
                response = client.post(
                    "/api/v1/aligned-media/authorize",
                    headers={
                        "X-Organization-Id": selected_scope.organization_id,
                        "X-Region-Code": selected_scope.region_code,
                    },
                    json=selector.model_dump(mode="json"),
                )
                if response.status_code != 200:
                    raise RuntimeError(f"authorization returned HTTP {response.status_code}")
                media = client.get(response.json()["media_url"], headers={"Range": "bytes=0-4095"})
                if media.status_code != 206 or not media.headers.get("content-range"):
                    raise RuntimeError(f"Range GET returned HTTP {media.status_code}")
                with results_lock:
                    authorization_latencies.append(time.perf_counter() - request_started)
                    range_bytes.append(len(media.content))

            def record_authorize(index: int) -> None:
                try:
                    authorize_and_range(index)
                except Exception as exc:
                    with results_lock:
                        range_failures.append(type(exc).__name__)
                    raise

            with ThreadPoolExecutor(max_workers=min(32, ready_clients)) as clients:
                tuple(clients.map(record_authorize, range(ready_clients)))
        counters_after = {
            "active_ffmpeg": encoder.active,
            "encoder_calls": encoder.calls,
            "ensure_generation_calls": repository.ensure_calls,
            "internal_job_count": len(repository.job_ids),
            "lance_commit_count": len(bundle_results),
        }
        sampler.checkpoint("ready_authorize_range_complete")

    all_artifacts = tuple(
        artifact for _scope, artifacts in bundle_results for artifact in artifacts
    )
    cleanup_started = time.perf_counter()
    cleanup_deleted_bytes = sum(store.delete_exact(item.objects) for item in all_artifacts)
    cleanup_elapsed = time.perf_counter() - cleanup_started
    result: dict[str, Any] = {
        "upload_concurrency": upload_concurrency,
        "camera_count_per_upload": len(CAMERAS),
        "generation_jobs": len(all_artifacts),
        "media_activity_batch_size": 2,
        "global_media_capacity": GLOBAL_MEDIA_CAPACITY,
        "max_active_ffmpeg": encoder.max_active,
        "frames_per_artifact": prepared.frame_count,
        "duration_seconds_per_artifact": prepared.frame_count / FREQUENCY_HZ,
        "generation_wall_seconds": round(generation_elapsed, 6),
        "generation_latency_seconds": _distribution(generation_latencies),
        "capacity_queue_wait_seconds": _distribution(encoder.queue_waits),
        "generation_failure_count": len(generation_failures),
        "all_four_camera_bundles_lance_committed": len(bundle_results) == upload_concurrency,
        "all_artifacts_ready_and_timeline_exact": all(
            item.status.value == "READY"
            and item.frame_count == prepared.frame_count
            and item.timeline is not None
            and item.timeline.frame_count == prepared.frame_count
            for item in all_artifacts
        ),
        "output_bytes": sum(item.total_bytes for item in all_artifacts),
        "objects_per_artifact": [len(item.objects) for item in all_artifacts],
        "ready_clients": len(authorization_latencies),
        "authorize_range_latency_seconds": _distribution(authorization_latencies),
        "range_response_bytes": sum(range_bytes),
        "range_failure_count": len(range_failures),
        "authorization_audit_events": len(audit.events),
        "authorization_counters_before": counters_before,
        "authorization_counters_after": counters_after,
        "authorization_zero_work_asserted": counters_before == counters_after,
        "authorization_has_no_lance_dependency": True,
        "raw_retained_after_case": prepared.mcap_path.is_file(),
        "exact_test_teardown": {
            "deleted_bytes": cleanup_deleted_bytes,
            "wall_seconds": round(cleanup_elapsed, 6),
            "uses_prefix_delete": False,
            "note": (
                "Committed objects are deleted only because the benchmark temp root "
                "is teardown scope."
            ),
        },
    }
    result.update(sampler.result())
    if (
        encoder.max_active > GLOBAL_MEDIA_CAPACITY
        or generation_failures
        or range_failures
        or not result["authorization_zero_work_asserted"]
    ):
        raise RuntimeError("aligned media capacity invariant failed")
    return result


def _write_mcap(path: Path, *, frame_count: int) -> int:
    camera_schema = json.dumps(
        {"type": "object", "required": ["data_base64", "encoding", "width", "height"]}
    ).encode()
    hashes: set[str] = set()
    with path.open("wb") as output:
        writer = Writer(
            output,
            compression=CompressionType.ZSTD,
            chunk_size=48 * 1024 * 1024,
            enable_crcs=True,
            enable_data_crcs=True,
        )
        writer.start(profile="hc-capacity-json/v1", library="hc-aligned-media-benchmark/1")
        schema_id = writer.register_schema("hc.camera.JpegEnvelope", "jsonschema", camera_schema)
        channels = {
            camera: writer.register_channel(camera, "json", schema_id) for camera in CAMERAS
        }
        for index in range(frame_count):
            timestamp_ns = index * 1_000_000_000 // FREQUENCY_HZ
            for camera_index, camera in enumerate(CAMERAS):
                jpeg = _jpeg(index, camera_index)
                digest = hashlib.sha256(jpeg).hexdigest()
                hashes.add(digest)
                envelope = json.dumps(
                    {
                        "data_base64": base64.b64encode(jpeg).decode(),
                        "encoding": "jpeg",
                        "height": HEIGHT,
                        "jpeg_sha256": digest,
                        "width": WIDTH,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
                writer.add_message(
                    channels[camera],
                    log_time=timestamp_ns,
                    publish_time=timestamp_ns,
                    sequence=index + 1,
                    data=envelope,
                )
        writer.finish()
    return len(hashes)


def _jpeg(frame_index: int, camera_index: int) -> bytes:
    image = Image.new(
        "RGB",
        (WIDTH, HEIGHT),
        (
            (37 * frame_index + 53 * camera_index) % 256,
            (17 * frame_index + 97 * camera_index) % 256,
            (11 * frame_index + 149 * camera_index) % 256,
        ),
    )
    draw = ImageDraw.Draw(image)
    offset = (frame_index * 13 + camera_index * 71) % WIDTH
    y = (frame_index * 7 + camera_index * 113) % HEIGHT
    draw.line((offset, 0, (offset + frame_index * 3) % WIDTH, HEIGHT), fill="white", width=3)
    draw.rectangle(
        (offset, y, min(WIDTH - 1, offset + 80), min(HEIGHT - 1, y + 50)),
        outline="black",
        width=4,
    )
    draw.text((8, 8), f"camera={camera_index} frame={frame_index}", fill="yellow")
    output = BytesIO()
    image.save(output, format="JPEG", quality=88, optimize=False)
    return output.getvalue()


def _materialize_projection(mcap_path: Path, projection: Path, *, source_sha256: str) -> None:
    writer = _ArrowProjectionWriter(projection, source_sha256=source_sha256)
    last: dict[str, int] = {}
    try:
        with mcap_path.open("rb") as stream:
            for _schema, channel, message in make_reader(stream).iter_messages():
                topic = str(channel.topic)
                timestamp_ns = int(message.log_time)
                observation, image, perceptual_hash = _decode_image_projection(
                    message.data, timestamp_ns=timestamp_ns
                )
                accepted = timestamp_ns > last.get(topic, -1)
                if accepted:
                    last[topic] = timestamp_ns
                writer.append(
                    topic=topic,
                    timestamp_ns=timestamp_ns,
                    is_camera=True,
                    alignment_accepted=accepted,
                    value_json=None,
                    image=image,
                    luma_mean=observation.luma_mean,
                    fingerprint=observation.fingerprint,
                    perceptual_hash=perceptual_hash,
                    corrupt=observation.corrupt,
                )
        writer.close()
    except Exception:
        writer.abort()
        raise


def _quality(projection: Path, source_sha256: str, frame_count: int, rollout_id: str) -> Any:
    data = QualityInputV1(
        rollout_id=rollout_id,
        source_sha256=source_sha256,
        start_ns=0,
        end_ns=frame_count * 1_000_000_000 // FREQUENCY_HZ,
        topic_timestamps_ns={},
    )
    profile = QualityProfileV1(
        profile_id="capacity-four-camera-quality-v1",
        required_topics=frozenset(CAMERAS),
        # The representative source intentionally contains only photos. The
        # camera-only profile therefore does not invent an /action requirement.
        action=ActionQualityProfileV1(
            minimum_observation_count_risk=0,
            minimum_observation_count_reject=0,
        ),
    )
    rows = _projection_rows(
        projection,
        columns=("topic", "timestamp_ns", "is_camera", "luma_mean", "fingerprint", "corrupt"),
    )
    observations = (
        QualityStreamObservationV1(
            topic=str(row["topic"]),
            timestamp_ns=int(row["timestamp_ns"]),
            is_camera=bool(row["is_camera"]),
            luma_mean=None if row["luma_mean"] is None else float(row["luma_mean"]),
            fingerprint=None if row["fingerprint"] is None else str(row["fingerprint"]),
            corrupt=bool(row["corrupt"]),
        )
        for row in rows
    )
    return QualityEngine().evaluate_stream(data, observations, profile)


def _align(
    projection: Path,
    staging_root: Path,
    *,
    source_sha256: str,
    rollout_id: str,
    frame_count: int,
) -> Any:
    samples: Iterable[tuple[str, TimedSampleV1]] = (
        (topic, TimedSampleV1(timestamp_ns=timestamp_ns, value=image or b""))
        for topic, timestamp_ns, is_camera, accepted, _value_json, image in _projection_sample_rows(
            projection
        )
        if is_camera and accepted
    )
    return AlignmentEngine().align_stream_to_writer(
        rollout_id=rollout_id,
        source_sha256=source_sha256,
        attempt_id=f"capacity-{source_sha256[:24]}",
        start_ns=0,
        end_ns=frame_count * 1_000_000_000 // FREQUENCY_HZ,
        stream_kinds={camera: ModalityKind.IMAGE for camera in CAMERAS},
        samples=samples,
        profile=AlignmentProfileV1(
            profile_id="capacity-causal-30hz-v1",
            frequency_hz=FREQUENCY_HZ,
            required_modalities=frozenset(CAMERAS),
            default_tolerance_ns=20_000_000,
        ),
        writer=ArrowFragmentWriter(staging_root),
    )


def _distribution(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {name: 0.0 for name in ("minimum", "p50", "p95", "p99", "maximum", "mean")}
    ordered = sorted(values)
    return {
        "minimum": round(ordered[0], 6),
        "p50": round(_percentile(ordered, 0.50), 6),
        "p95": round(_percentile(ordered, 0.95), 6),
        "p99": round(_percentile(ordered, 0.99), 6),
        "maximum": round(ordered[-1], 6),
        "mean": round(statistics.fmean(ordered), 6),
    }


def _percentile(values: Sequence[float], fraction: float) -> float:
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _cgroup_root() -> Path:
    relative = Path("/")
    for line in Path("/proc/self/cgroup").read_text().splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == "0":
            relative = Path(parts[2].lstrip("/"))
            break
    candidate = Path("/sys/fs/cgroup") / relative
    return candidate if candidate.exists() else Path("/sys/fs/cgroup")


def _key_values(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    return {
        key: int(value) for key, value in (line.split() for line in path.read_text().splitlines())
    }


def _inline_values(line: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for item in line.split()[1:]:
        key, separator, value = item.partition("=")
        if separator:
            values[key] = int(value)
    return values


def _integer(path: Path) -> int:
    if not path.exists():
        return 0
    value = path.read_text().strip()
    return 0 if value == "max" else int(value)


def _network_bytes() -> tuple[int, int]:
    rx = tx = 0
    for line in Path("/proc/self/net/dev").read_text().splitlines()[2:]:
        _interface, values = line.split(":", 1)
        fields = values.split()
        rx += int(fields[0])
        tx += int(fields[8])
    return rx, tx


def _process_rss_bytes() -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    return 0


def _tree_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=int, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--concurrency", default="1,2,4")
    parser.add_argument("--ready-clients", type=int, default=DEFAULT_READY_CLIENTS)
    args = parser.parse_args()
    concurrency_values = tuple(int(value) for value in args.concurrency.split(","))
    if any(value < 1 for value in concurrency_values) or args.ready_clients < 1:
        raise SystemExit("concurrency and ready client counts must be positive")
    run_started = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix="hc-aligned-media-capacity-") as directory:
        prepared, ingest_metrics = prepare_pipeline(
            Path(directory), duration_seconds=args.duration_seconds
        )
        cases = [
            generation_case(
                prepared,
                upload_concurrency=concurrency,
                ready_clients=args.ready_clients,
            )
            for concurrency in concurrency_values
        ]
        raw_retained_before_cleanup = prepared.mcap_path.is_file()
        prepared.staging_store.delete(prepared.staging.object_key)
        staging_deleted_exactly = True
    result = {
        "schema_version": 3,
        "evidence_class": "LOCAL_COMPONENT_CAPACITY_NOT_PRODUCTION",
        "started_at": run_started.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "programmatically_generated": True,
            "width": WIDTH,
            "height": HEIGHT,
            "camera_count": len(CAMERAS),
            "camera_topics": CAMERAS,
            "frequency_hz": FREQUENCY_HZ,
            "duration_seconds": args.duration_seconds,
            "frames_per_camera": prepared.frame_count,
            "unique_jpegs": prepared.unique_jpegs,
        },
        "chain": [
            "raw MCAP retained",
            "MCAP framing/index/CRC/topic verification",
            "bounded Arrow projection",
            "online quality gate",
            "causal fixed-30Hz alignment",
            "bounded immutable Arrow alignment staging",
            "four immutable H.264 CRF20 yuv420p MP4 publications",
            "Lance rows with frame references only",
            "Dataset commit marker",
            "stateless aligned-media authorization",
            "native HTTP Range GET",
        ],
        "ingest": ingest_metrics,
        "cases": cases,
        "cleanup": {
            "raw_retained_until_test_root_teardown": raw_retained_before_cleanup,
            "alignment_staging_deleted_by_exact_key": staging_deleted_exactly,
            "uses_prefix_delete": False,
        },
        "capacity_interpretation": {
            "measured": "single-host local component behavior with real FFmpeg and Lance",
            "not_measured": "deployed PostgreSQL/S3/network/pod scheduling and production headroom",
            "safe_global_concurrency": (
                "min(cpu budget / CPU per job, memory budget / memory per job, "
                "temporary disk budget / disk per job, object-store budget / traffic per job)"
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "evidence_class": result["evidence_class"]}))


if __name__ == "__main__":
    main()
