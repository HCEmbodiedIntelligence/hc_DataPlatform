from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hc_data_platform.core.capacity_evidence import (
    REQUIRED_BYTES_PER_SECOND as EVIDENCE_REQUIRED_BYTES_PER_SECOND,
)
from hc_data_platform.core.capacity_evidence import validate_capacity_evidence
from hc_data_platform.ingest.models import ManifestFileV1, RolloutManifestV1
from hc_data_platform.ingest.ports import InMemoryObjectStorage
from hc_data_platform.ingest.service import UploadSessionService

TARGET_BYTES_PER_DAY = 5_000_000_000_000
HEADROOM = 1.30
REQUIRED_BYTES_PER_SECOND = TARGET_BYTES_PER_DAY * HEADROOM / 86_400


def _passing_capacity_evidence() -> dict[str, object]:
    latency = {"p50": 0.1, "p95": 0.2, "p99": 0.3}
    throughput = {
        "minimum": EVIDENCE_REQUIRED_BYTES_PER_SECOND + 1,
        "p50": EVIDENCE_REQUIRED_BYTES_PER_SECOND + 2,
        "p95": EVIDENCE_REQUIRED_BYTES_PER_SECOND + 3,
        "p99": EVIDENCE_REQUIRED_BYTES_PER_SECOND + 4,
    }
    rates = {
        "operations_total": 100,
        "errors_total": 0,
        "retries_total": 1,
        "error_rate": 0.0,
        "retry_rate": 0.01,
    }
    byte_stages = {"object_storage", "lance", "preview", "export"}
    stages = {
        name: {
            "status": "PASS",
            "latency_seconds": dict(latency),
            "throughput_bytes_per_second": dict(throughput) if name in byte_stages else None,
            **rates,
        }
        for name in (
            "api",
            "postgresql",
            "object_storage",
            "temporal",
            "quality_control",
            "lance",
            "preview",
            "export",
        )
    }
    resources = {
        name: {
            "cpu_percent_p95": 70.0,
            "memory_percent_p95": 75.0,
            "disk_percent_p95": 60.0,
            "network_percent_p95": 50.0,
            "saturated": False,
        }
        for name in (
            "api",
            "postgresql",
            "object_storage",
            "temporal_worker",
            "lance",
            "preview",
            "export",
        )
    }
    return {
        "schema_version": "hc-capacity-evidence/v1",
        "run": {
            "run_id": "production-like-capacity-001",
            "deployment_id": "pilot-cluster-immutable-revision",
            "environment_kind": "PRODUCTION_LIKE",
            "mock_mode": "off",
            "started_at": "2026-08-24T00:00:00Z",
            "finished_at": "2026-08-24T00:30:00Z",
            "duration_seconds": 1800,
        },
        "target": {
            "bytes_per_day": TARGET_BYTES_PER_DAY,
            "headroom_ratio": HEADROOM,
            "required_bytes_per_second": EVIDENCE_REQUIRED_BYTES_PER_SECOND,
        },
        "workload": {
            "object_sizes_bytes": [256 * 1024**2, 20 * 1024**3],
            "concurrency": 50,
            "completed_rollouts": 100,
            "completed_bytes": int(EVIDENCE_REQUIRED_BYTES_PER_SECOND * 1800) + 1,
        },
        "end_to_end": {
            "throughput_bytes_per_second": throughput,
            "latency_seconds": latency,
            **rates,
        },
        "stages": stages,
        "saturation": resources,
        "recovery": {
            "faults_injected": ["object_storage_interruption", "worker_restart"],
            "all_recovered": True,
            "duplicate_side_effects": 0,
        },
        "cleanup": {
            "verified": True,
            "residual_database_rows": 0,
            "residual_objects": 0,
        },
        "verdict": {"end_to_end_5tb_per_day": "PASS"},
    }


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
            collection_session_id="capacity-session",
            recording_request_id=f"request-{index:04d}",
            data_package_id=f"package-{index:04d}",
            sequence_no=index + 1,
            robot_id="load-robot",
            start_time=started + timedelta(seconds=index),
            end_time=started + timedelta(seconds=index + 1),
            expected_topics=["/camera/front/image"],
            actual_topics=["/camera/front/image"],
            cameras=[{"camera_id": "front", "topic": "/camera/front/image"}],
            topics=[{"name": "/camera/front/image", "required": True}],
            files=[
                ManifestFileV1(
                    path="recording.mcap",
                    size=20 * 1024**3,
                    sha256=f"{index + 1:064x}",
                    crc64=index + 1,
                )
            ],
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


def test_capacity_evidence_accepts_only_a_complete_sustained_full_chain() -> None:
    evidence = _passing_capacity_evidence()

    validation = validate_capacity_evidence(evidence)

    assert validation.accepted is True
    assert validation.errors == ()


def test_capacity_evidence_rejects_short_local_partial_or_inconsistent_pass_claims() -> None:
    minimal_fake = {"verdict": {"end_to_end_5tb_per_day": "PASS"}}
    assert validate_capacity_evidence(minimal_fake).accepted is False

    invalid: list[tuple[str, dict[str, object]]] = []

    short = deepcopy(_passing_capacity_evidence())
    short_run = short["run"]
    assert isinstance(short_run, dict)
    short_run["finished_at"] = "2026-08-24T00:29:59Z"
    short_run["duration_seconds"] = 1799
    invalid.append(("short", short))

    local = deepcopy(_passing_capacity_evidence())
    local_run = local["run"]
    assert isinstance(local_run, dict)
    local_run["environment_kind"] = "LOCAL"
    invalid.append(("local", local))

    slow = deepcopy(_passing_capacity_evidence())
    slow_end_to_end = slow["end_to_end"]
    assert isinstance(slow_end_to_end, dict)
    slow_throughput = slow_end_to_end["throughput_bytes_per_second"]
    assert isinstance(slow_throughput, dict)
    slow_throughput["minimum"] = EVIDENCE_REQUIRED_BYTES_PER_SECOND - 1
    invalid.append(("slow", slow))

    missing_stage = deepcopy(_passing_capacity_evidence())
    missing_stages = missing_stage["stages"]
    assert isinstance(missing_stages, dict)
    del missing_stages["export"]
    invalid.append(("missing_stage", missing_stage))

    bad_rate = deepcopy(_passing_capacity_evidence())
    bad_end_to_end = bad_rate["end_to_end"]
    assert isinstance(bad_end_to_end, dict)
    bad_end_to_end["errors_total"] = 2
    bad_end_to_end["error_rate"] = 0.0
    invalid.append(("bad_rate", bad_rate))

    saturated = deepcopy(_passing_capacity_evidence())
    saturation = saturated["saturation"]
    assert isinstance(saturation, dict)
    saturated_api = saturation["api"]
    assert isinstance(saturated_api, dict)
    saturated_api["cpu_percent_p95"] = 95.0
    invalid.append(("saturated", saturated))

    no_recovery = deepcopy(_passing_capacity_evidence())
    recovery = no_recovery["recovery"]
    assert isinstance(recovery, dict)
    recovery["faults_injected"] = ["worker_restart"]
    invalid.append(("no_recovery", no_recovery))

    residue = deepcopy(_passing_capacity_evidence())
    cleanup = residue["cleanup"]
    assert isinstance(cleanup, dict)
    cleanup["residual_objects"] = 1
    invalid.append(("residue", residue))

    for label, document in invalid:
        validation = validate_capacity_evidence(document)
        assert validation.accepted is False, label
        assert validation.errors, label
