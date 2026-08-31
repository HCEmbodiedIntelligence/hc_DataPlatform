"""Measure the real FastAPI upload route without claiming network capacity."""

from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.health import ReadinessProbe
from hc_data_platform.ingest.ports import InMemoryObjectStorage
from hc_data_platform.ingest.router import get_service
from hc_data_platform.security.auth import JwtVerifier

JWT_ISSUER = "https://be12-load.invalid"
JWT_AUDIENCE = "hc-data-platform-be12-load"
JWT_KEY = "be12-load-only-signing-key-32-bytes"


class _ReadyProbe:
    async def check(self) -> None:
        return None


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1)]


def _manifest(index: int, rollout_size: int) -> dict[str, Any]:
    started_at = datetime(2026, 8, 14, tzinfo=timezone.utc) + timedelta(seconds=index)
    return {
        "project_id": "be12-load",
        "task_id": "capacity",
        "collection_job_id": "inprocess-http-50",
        "rollout_id": f"rollout-{index:04d}",
        "collection_session_id": "capacity-session",
        "recording_request_id": f"request-{index:04d}",
        "data_package_id": f"package-{index:04d}",
        "sequence_no": index + 1,
        "robot_id": "load-robot",
        "start_time": started_at.isoformat(),
        "end_time": (started_at + timedelta(minutes=10)).isoformat(),
        "expected_topics": ["/camera/front/image"],
        "actual_topics": ["/camera/front/image"],
        "cameras": [{"camera_id": "front", "topic": "/camera/front/image"}],
        "topics": [{"name": "/camera/front/image", "required": True}],
        "files": [
            {
                "path": "recording.mcap",
                "size": rollout_size,
                "sha256": f"{index + 1:064x}",
                "crc64": str(index + 1),
                "role": "RAW_MCAP",
            }
        ],
        "file_size": rollout_size,
        "sha256": f"{index + 1:064x}",
        "crc64": str(index + 1),
        "compression": "zstd",
        "recorder_version": "be12-load/1",
    }


def _app() -> tuple[FastAPI, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "be12-capacity-probe",
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": now,
            "exp": now + 300,
            "project_ids": ["be12-load"],
            "region_codes": ["cn-hz"],
            "roles": ["uploader"],
        },
        JWT_KEY,
        algorithm="HS256",
    )
    verifier = JwtVerifier(
        issuer=JWT_ISSUER,
        audience=JWT_AUDIENCE,
        algorithms=("HS256",),
        key=JWT_KEY,
    )
    ready: ReadinessProbe = _ReadyProbe()
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory"),
        readiness_probes={
            "postgresql": ready,
            "temporal": ready,
            "object_storage": ready,
        },
        jwt_verifier=verifier,
    )
    return app, token


def run_probe(*, concurrency: int = 50, rollout_size: int = 20 * 1024**3) -> dict[str, Any]:
    """Run concurrent requests through FastAPI validation, auth, routing, and service state."""

    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    get_service.cache_clear()
    app, token = _app()
    path = "/api/v1/projects/be12-load/regions/cn-hz/upload-sessions"

    def request(index: int, client: TestClient) -> dict[str, Any]:
        started = time.perf_counter()
        response = client.post(
            path,
            json={"manifest": _manifest(index, rollout_size), "part_numbers": [1, 160, 320]},
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": f"be12-http-{index:04d}",
                "X-Project-ID": "be12-load",
                "X-Region-Code": "cn-hz",
            },
        )
        return {
            "index": index,
            "status": response.status_code,
            "latency_seconds": time.perf_counter() - started,
            "session_id": response.json().get("session", {}).get("session_id"),
        }

    started = time.perf_counter()
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(lambda index: request(index, client), range(concurrency)))
    elapsed = time.perf_counter() - started
    latencies = [item["latency_seconds"] for item in results]
    session_ids = [item["session_id"] for item in results]
    statuses = {
        str(status): sum(item["status"] == status for item in results)
        for status in {item["status"] for item in results}
    }
    service = get_service()
    storage = service.storage
    if not isinstance(storage, InMemoryObjectStorage):
        raise TypeError("the in-process probe requires InMemoryObjectStorage")
    return {
        "schema_version": 1,
        "scope": "in-process FastAPI control plane; excludes network and uploaded bodies",
        "concurrency": concurrency,
        "rollout_size_bytes": rollout_size,
        "elapsed_seconds": elapsed,
        "sessions_per_second": concurrency / elapsed,
        "latency_seconds": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
        },
        "statuses": statuses,
        "unique_sessions": len(set(session_ids)),
        "stored_object_count": len(storage.objects),
        "passed": all(item["status"] == 201 for item in results)
        and len(set(session_ids)) == concurrency
        and len(storage.objects) == 0,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--rollout-size", type=int, default=20 * 1024**3)
    args = parser.parse_args()
    result = run_probe(concurrency=args.concurrency, rollout_size=args.rollout_size)
    # Runtime producers intentionally write one JSON object per stdout line. Keep this
    # harness result framed the same way so callers can select the final JSON document
    # without suppressing or redirecting the production logging path under test.
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
