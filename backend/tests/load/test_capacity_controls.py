from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hc_data_platform.ingest.models import RolloutManifestV1
from hc_data_platform.ingest.ports import InMemoryObjectStorage
from hc_data_platform.ingest.service import UploadSessionService

TARGET_BYTES_PER_DAY = 5_000_000_000_000
HEADROOM = 1.30
REQUIRED_BYTES_PER_SECOND = TARGET_BYTES_PER_DAY * HEADROOM / 86_400


def test_20gb_rollout_control_plane_fixture_is_sparse(tmp_path: Path) -> None:
    path = tmp_path / "rollout-20gib.mcap"
    logical_size = 20 * 1024**3
    with path.open("wb") as stream:
        stream.truncate(logical_size)
    stat = path.stat()
    assert stat.st_size == logical_size
    assert stat.st_blocks * 512 < 1024 * 1024


def test_5tb_daily_target_includes_30_percent_headroom() -> None:
    assert TARGET_BYTES_PER_DAY == 5_000_000_000_000
    assert HEADROOM == 1.30
    assert REQUIRED_BYTES_PER_SECOND == TARGET_BYTES_PER_DAY * HEADROOM / 86_400
    assert REQUIRED_BYTES_PER_SECOND > 75_000_000
    assert os.path.exists("/proc/cpuinfo")


def test_50_concurrent_20gib_sessions_keep_unique_control_state_without_bodies() -> None:
    storage = InMemoryObjectStorage()
    service = UploadSessionService(storage)
    started = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)

    def create(index: int) -> str:
        manifest = RolloutManifestV1(
            project_id="be12-load",
            task_id="capacity",
            collection_job_id="concurrent-50",
            rollout_id=f"rollout-{index:04d}",
            sequence_no=index + 1,
            robot_id="load-robot",
            start_time=started + timedelta(seconds=index),
            end_time=started + timedelta(seconds=index + 1),
            expected_topics=["/camera/front/image"],
            actual_topics=["/camera/front/image"],
            file_size=20 * 1024**3,
            sha256=f"{index + 1:064x}",
            crc64=index + 1,
            compression="zstd",
            recorder_version="be12-load/1",
        )
        return service.create_upload(
            manifest=manifest,
            region_code="cn-hz",
            idempotency_key=f"load-{index:04d}",
            part_numbers=[1, 160, 320],
        ).session.session_id

    with ThreadPoolExecutor(max_workers=50) as executor:
        session_ids = list(executor.map(create, range(50)))

    assert len(set(session_ids)) == 50
    assert storage.objects == {}
    assert len(storage._uploads) == 50


def test_50_concurrent_20gib_manifests_pass_the_real_fastapi_route() -> None:
    script = Path(__file__).with_name("inprocess_http_probe.py")
    completed = subprocess.run(
        [sys.executable, str(script)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["passed"] is True
    assert result["statuses"] == {"201": 50}
    assert result["unique_sessions"] == 50
    assert result["stored_object_count"] == 0


def test_retained_minio_evidence_does_not_overclaim_end_to_end_capacity() -> None:
    result_path = Path(__file__).parent / "results" / "minio-capacity.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))

    assert result["dataset"]["size_bytes"] == 256 * 1024**2
    assert result["dataset"]["samples"] == 7
    assert result["verdict"]["minio_stage_minimum_meets_target"] is False
    assert result["verdict"]["end_to_end_5tb_per_day"] == "NOT_MEASURED"
    upload_p50 = result["throughput_bytes_per_second"]["multipart_upload"]["p50"]
    assert upload_p50 < REQUIRED_BYTES_PER_SECOND
