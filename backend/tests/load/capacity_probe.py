"""Measure local pilot-host I/O and SHA throughput without claiming end-to-end capacity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

DECIMAL_TB = 1_000_000_000_000
TARGET_BYTES_PER_DAY = 5 * DECIMAL_TB
HEADROOM = 1.30
REQUIRED_BYTES_PER_SECOND = TARGET_BYTES_PER_DAY * HEADROOM / 86_400


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) * quantile) + 0.999999) - 1))
    return ordered[index]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "p50": round(_percentile(values, 0.50), 6),
        "p95": round(_percentile(values, 0.95), 6),
        "p99": round(_percentile(values, 0.99), 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "mean": round(statistics.fmean(values), 6),
    }


def _memory_bytes() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", maxsplit=1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _hardware(path: Path) -> dict[str, Any]:
    filesystem = os.statvfs(path)
    return {
        "hostname": platform.node(),
        "kernel": platform.release(),
        "platform": platform.platform(),
        "cpu_model": _cpu_model(),
        "logical_cpus": os.cpu_count(),
        "memory_bytes": _memory_bytes(),
        "filesystem_block_size": filesystem.f_frsize,
        "filesystem_available_bytes": filesystem.f_bavail * filesystem.f_frsize,
        "probe_directory": str(path),
    }


def run(*, size_bytes: int, samples: int, directory: Path) -> dict[str, Any]:
    if size_bytes < 1 or samples < 1:
        raise ValueError("size_bytes and samples must be positive")
    block = bytes(index % 251 for index in range(8 * 1024 * 1024))
    write_seconds: list[float] = []
    sha_seconds: list[float] = []
    with tempfile.TemporaryDirectory(prefix="hc-be12-capacity-", dir=directory) as temporary:
        path = Path(temporary) / "probe.bin"
        started = time.perf_counter()
        remaining = size_bytes
        with path.open("wb", buffering=0) as stream:
            while remaining:
                chunk = block[: min(remaining, len(block))]
                stream.write(chunk)
                remaining -= len(chunk)
            os.fsync(stream.fileno())
        write_seconds.append(time.perf_counter() - started)

        expected_digest: str | None = None
        for _ in range(samples):
            digest = hashlib.sha256()
            started = time.perf_counter()
            with path.open("rb", buffering=0) as stream:
                while chunk := stream.read(len(block)):
                    digest.update(chunk)
            sha_seconds.append(time.perf_counter() - started)
            actual_digest = digest.hexdigest()
            if expected_digest is None:
                expected_digest = actual_digest
            elif expected_digest != actual_digest:
                raise RuntimeError("probe file changed between samples")

    sha_throughput = [size_bytes / duration for duration in sha_seconds]
    return {
        "schema_version": 1,
        "measured_at_unix_seconds": int(time.time()),
        "hardware": _hardware(directory),
        "dataset": {
            "kind": "deterministic synthetic local file",
            "size_bytes": size_bytes,
            "samples": samples,
            "sha256": expected_digest,
        },
        "commands": {
            "probe": (
                f"python tests/load/capacity_probe.py --size-bytes {size_bytes} --samples {samples}"
            ),
        },
        "latency_seconds": {
            "durable_local_write": _summary(write_seconds),
            "sequential_read_sha256": _summary(sha_seconds),
        },
        "throughput_bytes_per_second": {
            "sequential_read_sha256": _summary(sha_throughput),
        },
        "capacity_target": {
            "bytes_per_day": TARGET_BYTES_PER_DAY,
            "headroom_ratio": HEADROOM,
            "required_bytes_per_second": round(REQUIRED_BYTES_PER_SECOND, 3),
        },
        "verdict": {
            "local_sha_stage_p95_meets_target": _percentile(sha_throughput, 0.05)
            >= REQUIRED_BYTES_PER_SECOND,
            "end_to_end_5tb_per_day": "NOT_MEASURED",
            "reason": (
                "No complete OSS/Temporal/PostgreSQL/Lance/FFmpeg/LeRobot workload was exercised."
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size-bytes", type=int, default=512 * 1024 * 1024)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--directory", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(size_bytes=args.size_bytes, samples=args.samples, directory=args.directory)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
