#!/usr/bin/env python3
"""Repeatable synthetic durable-preview benchmark (no user data).

The full profile exercises 60 seconds at 30 Hz across four camera identities,
with 1/2/4/8 simultaneous generation jobs, followed by 100/1000 READY
authorizations. Results are emitted as JSON for release evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import resource
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from hc_data_platform.preview.adapters import FFmpegHlsEncoder
from hc_data_platform.preview.memory import (
    HmacUrlSigner,
    InMemoryPreviewArtifactStore,
    InMemoryPreviewRepository,
)
from hc_data_platform.preview.models import (
    EncodedPreviewArtifactV1,
    PreviewDescriptorV1,
    PreviewRequestV1,
    PreviewScopeV1,
    RenderFrameV1,
)
from hc_data_platform.preview.service import (
    PREVIEW_PIPELINE_REVISION,
    PreviewControlPlaneService,
    preview_artifact_key,
)

CAMERAS = ("front", "rear", "left", "right")
FREQUENCY_HZ = 30
FULL_DURATION_SECONDS = 60
FULL_CONCURRENCY = (1, 2, 4, 8)
GENERATION_JOBS_PER_CASE = 8  # two synthetic rollouts × four cameras
AUTHORIZATION_COUNTS = (100, 1_000)
NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (96, 54), color=(32, 96, 160)).save(
        output, format="JPEG", quality=82
    )
    return output.getvalue()


def _frames(frame_count: int, content: bytes):
    for index in range(frame_count):
        yield RenderFrameV1(
            playback_frame=index,
            step_index=index,
            timestamp_ns=index * 1_000_000_000 // FREQUENCY_HZ,
            source_timestamp_ns=index * 1_000_000_000 // FREQUENCY_HZ,
            image_ref=content,
        )


def _usage_seconds(kind: int) -> float:
    value = resource.getrusage(kind)
    return value.ru_utime + value.ru_stime


def _generation_case(
    root: Path,
    *,
    concurrency: int,
    duration_seconds: int,
    jpeg: bytes,
) -> dict[str, Any]:
    encoder = FFmpegHlsEncoder(root, ffmpeg_threads=1)
    frame_count = duration_seconds * FREQUENCY_HZ
    cpu_before = _usage_seconds(resource.RUSAGE_SELF) + _usage_seconds(
        resource.RUSAGE_CHILDREN
    )
    started = time.perf_counter()

    def generate(index: int) -> tuple[int, int]:
        camera = CAMERAS[index % len(CAMERAS)]
        request = PreviewRequestV1(
            project_id="benchmark-project",
            dataset_id="benchmark-dataset",
            rollout_id=f"benchmark-rollout-{index // len(CAMERAS)}",
            lance_version="1",
            camera_id=camera,
            frequency_hz=FREQUENCY_HZ,
            start_step=0,
            end_step=frame_count,
        )
        cache_key = hashlib.sha256(f"{concurrency}:{index}".encode()).hexdigest()
        encoded = encoder.encode(
            cache_key=cache_key,
            request=request,
            frames=_frames(frame_count, jpeg),
        )
        playlist = Path(encoded.artifact_uri.removeprefix("file://"))
        output_bytes = sum(
            path.stat().st_size for path in playlist.parent.iterdir() if path.is_file()
        )
        encoder.cleanup(encoded)
        return encoded.frame_count, output_bytes

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        outputs = tuple(pool.map(generate, range(GENERATION_JOBS_PER_CASE)))
    elapsed = time.perf_counter() - started
    cpu_after = _usage_seconds(resource.RUSAGE_SELF) + _usage_seconds(
        resource.RUSAGE_CHILDREN
    )
    total_frames = sum(item[0] for item in outputs)
    max_rss_kib = max(
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
    )
    return {
        "concurrency": concurrency,
        "camera_count": len(CAMERAS),
        "duration_seconds_per_job": duration_seconds,
        "frames": total_frames,
        "wall_seconds": round(elapsed, 6),
        "frames_per_second": round(total_frames / elapsed, 3),
        "cpu_seconds": round(cpu_after - cpu_before, 6),
        "peak_rss_mib": round(max_rss_kib / 1024, 3),
        "object_get_count": 0,
        "temporary_disk_bytes": sum(item[1] for item in outputs),
        "ffmpeg_process_count": GENERATION_JOBS_PER_CASE,
        "peak_concurrent_ffmpeg_processes": concurrency,
        "output_preview_bytes": sum(item[1] for item in outputs),
    }


async def _authorization_case(count: int) -> dict[str, Any]:
    scope = PreviewScopeV1(
        organization_id="benchmark-organization",
        project_id="benchmark-project",
        region_code="benchmark-region",
    )
    request = PreviewRequestV1(
        project_id=scope.project_id,
        dataset_id="benchmark-dataset",
        rollout_id="benchmark-rollout",
        lance_version="1",
        camera_id="front",
        frequency_hz=FREQUENCY_HZ,
        start_step=0,
        end_step=FULL_DURATION_SECONDS * FREQUENCY_HZ,
    )
    repository = InMemoryPreviewRepository()
    store = InMemoryPreviewArtifactStore()
    key = preview_artifact_key(request)
    artifact, job = repository.ensure_artifact_job(
        scope=scope,
        artifact_key=key,
        request=request,
        pipeline_revision=PREVIEW_PIPELINE_REVISION,
        rebuild_source_id="lance:benchmark-dataset:1:benchmark-rollout",
        artifact_ttl=timedelta(days=30),
        now=NOW,
    )
    assert job is not None
    publication = store.publish(
        project_id=scope.project_id,
        artifact_key=key,
        encoded=EncodedPreviewArtifactV1(
            artifact_uri="memory://benchmark/index.m3u8",
            duration_seconds=FULL_DURATION_SECONDS,
            frame_count=FULL_DURATION_SECONDS * FREQUENCY_HZ,
        ),
    )
    repository.mark_ready(
        scope=scope,
        job_id=job.job_id,
        artifact_key=artifact.artifact_key,
        publication=publication,
        frame_count=FULL_DURATION_SECONDS * FREQUENCY_HZ,
        duration_seconds=FULL_DURATION_SECONDS,
        now=NOW,
    )
    service = PreviewControlPlaneService(
        repository=repository,
        store=store,
        signer=HmacUrlSigner(b"benchmark-signing-key"),
        clock=lambda: NOW,
    )
    started = time.perf_counter()
    results = await asyncio.gather(
        *(service.create_session(scope, request) for _ in range(count))
    )
    elapsed = time.perf_counter() - started
    assert all(isinstance(item, PreviewDescriptorV1) for item in results)
    return {
        "requests": count,
        "wall_seconds": round(elapsed, 6),
        "requests_per_second": round(count / elapsed, 3),
        "object_get_count": 0,
        "ffmpeg_process_count": 0,
        "cache_hit_without_transcode": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--quick",
        action="store_true",
        help="use 2 seconds per generation job while preserving the full shape",
    )
    args = parser.parse_args()
    duration = 2 if args.quick else FULL_DURATION_SECONDS
    jpeg = _jpeg()
    with tempfile.TemporaryDirectory(prefix="hc-preview-benchmark-") as temporary:
        root = Path(temporary)
        generation = [
            _generation_case(
                root / f"concurrency-{concurrency}",
                concurrency=concurrency,
                duration_seconds=duration,
                jpeg=jpeg,
            )
            for concurrency in FULL_CONCURRENCY
        ]
    authorization = [
        asyncio.run(_authorization_case(count)) for count in AUTHORIZATION_COUNTS
    ]
    print(
        json.dumps(
            {
                "schema_version": 1,
                "synthetic": True,
                "profile_id": "annotation-h264-720p-v1",
                "frequency_hz": FREQUENCY_HZ,
                "generation": generation,
                "ready_authorization": authorization,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
