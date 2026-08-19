"""Exercise 20 GB metadata semantics and 50 concurrent upload-session requests."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1)]


def _request(
    base_url: str,
    index: int,
    rollout_size: int,
    timeout: float,
    bearer_token: str | None,
) -> dict[str, Any]:
    started_at = datetime(2026, 8, 14, tzinfo=timezone.utc) + timedelta(seconds=index)
    manifest = {
        "project_id": "be12-pilot",
        "task_id": "capacity",
        "collection_job_id": "be12-50-concurrent",
        "rollout_id": f"rollout-{index:04d}",
        "collection_session_id": "capacity-session",
        "recording_request_id": f"request-{index:04d}",
        "data_package_id": f"package-{index:04d}",
        "sequence_no": index + 1,
        "robot_id": "capacity-probe",
        "start_time": started_at.isoformat(),
        "end_time": (started_at + timedelta(minutes=10)).isoformat(),
        "expected_topics": ["/camera/front/image", "/joint_states", "/action", "/points"],
        "actual_topics": ["/camera/front/image", "/joint_states", "/action", "/points"],
        "cameras": [{"camera_id": "front", "topic": "/camera/front/image"}],
        "topics": [
            {"name": name, "required": True}
            for name in ["/camera/front/image", "/joint_states", "/action", "/points"]
        ],
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
        "recorder_version": "be12-capacity/1",
    }
    body = json.dumps({"manifest": manifest, "part_numbers": [1, 160, 320]}).encode("utf-8")
    url = f"{base_url.rstrip('/')}/api/v1/projects/be12-pilot/regions/cn-hz/upload-sessions"
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": f"be12-{index:04d}",
    }
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        response_body = exc.read()
        status = exc.code
    return {
        "index": index,
        "status": status,
        "latency_seconds": time.perf_counter() - started,
        "response_bytes": len(response_body),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--rollout-size", type=int, default=20 * 1024**3)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--bearer-token-env",
        help="environment variable containing the pilot bearer token (never printed)",
    )
    args = parser.parse_args()
    bearer_token = os.environ.get(args.bearer_token_env) if args.bearer_token_env else None
    if args.bearer_token_env and not bearer_token:
        parser.error(f"{args.bearer_token_env} is not set or is empty")

    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        results = list(
            executor.map(
                lambda index: _request(
                    args.base_url,
                    index,
                    args.rollout_size,
                    args.timeout,
                    bearer_token,
                ),
                range(args.concurrency),
            )
        )
    elapsed = time.perf_counter() - started
    latencies = [item["latency_seconds"] for item in results]
    payload = {
        "schema_version": 1,
        "concurrency": args.concurrency,
        "rollout_size_bytes": args.rollout_size,
        "elapsed_seconds": elapsed,
        "sessions_per_second": args.concurrency / elapsed,
        "latency_seconds": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
        },
        "statuses": {
            str(status): sum(item["status"] == status for item in results)
            for status in sorted({item["status"] for item in results})
        },
        "passed": all(item["status"] == 201 for item in results),
        "results": results,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not payload["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
