"""Measure a bounded MinIO data-plane baseline on explicit pilot hardware."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

TARGET_BYTES_PER_DAY = 5_000_000_000_000
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


def _create_file(path: Path, size_bytes: int) -> str:
    block = bytes(index % 251 for index in range(8 * 1024 * 1024))
    digest = hashlib.sha256()
    remaining = size_bytes
    with path.open("wb", buffering=0) as stream:
        while remaining:
            chunk = block[: min(remaining, len(block))]
            stream.write(chunk)
            digest.update(chunk)
            remaining -= len(chunk)
        os.fsync(stream.fileno())
    return digest.hexdigest()


def run(
    *,
    endpoint: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    size_bytes: int,
    samples: int,
    directory: Path,
) -> dict[str, Any]:
    if size_bytes < 1 or samples < 1:
        raise ValueError("size_bytes and samples must be positive")
    import boto3
    from boto3.s3.transfer import TransferConfig
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", retries={"max_attempts": 2, "mode": "standard"}),
    )
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)
    transfer = TransferConfig(
        multipart_threshold=8 * 1024 * 1024,
        multipart_chunksize=16 * 1024 * 1024,
        max_concurrency=4,
        use_threads=True,
    )
    upload_seconds: list[float] = []
    download_seconds: list[float] = []
    keys: list[str] = []
    with tempfile.TemporaryDirectory(prefix="be12-minio-capacity-", dir=directory) as temporary:
        source = Path(temporary) / "payload.bin"
        expected_sha256 = _create_file(source, size_bytes)
        for sample in range(samples):
            key = f"be12-capacity/sample-{sample:02d}-{expected_sha256[:12]}.bin"
            keys.append(key)
            started = time.perf_counter()
            client.upload_file(str(source), bucket, key, Config=transfer)
            upload_seconds.append(time.perf_counter() - started)

            started = time.perf_counter()
            digest = hashlib.sha256()
            response = client.get_object(Bucket=bucket, Key=key)
            body = response["Body"]
            try:
                for chunk in body.iter_chunks(chunk_size=8 * 1024 * 1024):
                    digest.update(chunk)
            finally:
                body.close()
            download_seconds.append(time.perf_counter() - started)
            if digest.hexdigest() != expected_sha256:
                raise RuntimeError(f"downloaded object {key} failed SHA-256 verification")

    objects = client.list_objects_v2(Bucket=bucket, Prefix="be12-capacity/").get("Contents", [])
    if sorted(value["Key"] for value in objects) != sorted(keys):
        raise RuntimeError("MinIO object inventory differs from completed samples")
    upload_throughput = [size_bytes / duration for duration in upload_seconds]
    download_throughput = [size_bytes / duration for duration in download_seconds]
    return {
        "schema_version": 1,
        "measured_at_unix_seconds": int(time.time()),
        "environment": {
            "endpoint_kind": "disposable MinIO container on the WSL2 Docker host",
            "bucket": bucket,
        },
        "dataset": {
            "kind": "deterministic synthetic binary",
            "size_bytes": size_bytes,
            "samples": samples,
            "sha256": expected_sha256,
        },
        "latency_seconds": {
            "multipart_upload": _summary(upload_seconds),
            "download_sha256": _summary(download_seconds),
        },
        "throughput_bytes_per_second": {
            "multipart_upload": _summary(upload_throughput),
            "download_sha256": _summary(download_throughput),
        },
        "capacity_target": {
            "bytes_per_day": TARGET_BYTES_PER_DAY,
            "headroom_ratio": HEADROOM,
            "required_bytes_per_second": round(REQUIRED_BYTES_PER_SECOND, 3),
        },
        "verdict": {
            "minio_stage_minimum_meets_target": min(
                min(upload_throughput), min(download_throughput)
            )
            >= REQUIRED_BYTES_PER_SECOND,
            "end_to_end_5tb_per_day": "NOT_MEASURED",
            "reason": (
                "This loopback Docker stage excludes deployed API auth, Temporal, PostgreSQL, "
                "Lance, FFmpeg, LeRobot, and production network/storage contention."
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--access-key-env", required=True)
    parser.add_argument("--secret-key-env", required=True)
    parser.add_argument("--size-bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--directory", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    access_key = os.environ.get(args.access_key_env)
    secret_key = os.environ.get(args.secret_key_env)
    if not access_key or not secret_key:
        parser.error("access-key and secret-key environment variables must be non-empty")
    result = run(
        endpoint=args.endpoint,
        bucket=args.bucket,
        access_key=access_key,
        secret_key=secret_key,
        size_bytes=args.size_bytes,
        samples=args.samples,
        directory=args.directory,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
