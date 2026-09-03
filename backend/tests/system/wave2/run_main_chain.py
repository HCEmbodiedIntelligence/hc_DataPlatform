"""Run the opt-in BE22 real-network API chain and emit a secret-free artifact."""

from __future__ import annotations

import argparse
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config

from .cleanup import PostgresS3CleanupBackend
from .fixture import CleanupController, RunScope
from .http_adapter import HttpMainChainAdapter, Wave2TestJwtIssuer
from .orchestrator import MainChainRun, passed


def _default_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=_default_run_id())
    parser.add_argument(
        "--artifact",
        type=Path,
        help="defaults to artifacts/test-gates/latest/be22/<run-id>.json",
    )
    args = parser.parse_args()
    scope = RunScope.create(args.run_id)
    artifact_path = args.artifact or (
        Path("artifacts/test-gates/latest/be22") / f"{scope.run_id}.json"
    )

    postgres_dsn = os.environ["HC_TEST_POSTGRES_DSN"]
    bucket = os.environ["HC_MINIO_BUCKET"]
    s3_client = boto3.client(
        "s3",
        endpoint_url=os.environ["HC_MINIO_ENDPOINT"],
        aws_access_key_id=os.environ["HC_MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["HC_MINIO_SECRET_KEY"],
        region_name=os.environ.get("HC_OBJECT_STORE_REGION", "us-east-1"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    cleanup = CleanupController(PostgresS3CleanupBackend(postgres_dsn, s3_client, bucket))
    adapter = HttpMainChainAdapter(
        os.environ.get("HC_WAVE2_BASE_URL", "http://127.0.0.1:8088"),
        issuer=Wave2TestJwtIssuer.from_environment(),
        worker_timeout_seconds=float(os.environ.get("HC_WAVE2_WORKER_TIMEOUT_SECONDS", "90")),
    )
    try:
        artifact = MainChainRun(adapter, cleanup).execute(
            scope,
            artifact_path=artifact_path,
        )
    finally:
        adapter.close()

    print(f"BE22 artifact: {artifact_path}")
    for stage in artifact.stages:
        print(f"{stage.status.value}: {stage.name}")
    print("NOT RUN: full browser E2E remains gated on the completed FE Wave 2 pages")
    return 0 if passed(artifact) else 1


if __name__ == "__main__":
    raise SystemExit(main())
