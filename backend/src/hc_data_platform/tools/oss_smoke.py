"""Copy one committed MCAP to page-configured OSS and verify bytes and structure."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import PurePosixPath
from uuid import uuid4

from hc_data_platform.core.config import Settings
from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.object_storage.providers import OssObjectStorage, S3ObjectStorage
from hc_data_platform.platform_ops.object_store_config import (
    PostgresObjectStoreConfigurationRepository,
)
from hc_data_platform.storage.oss_client import build_oss_bucket


class SmokeError(RuntimeError):
    pass


def destination_key(prefix: str, source_key: str, run_id: str) -> str:
    prefix = prefix.rstrip("/")
    if (
        not prefix
        or "\\" in prefix
        or any(ord(c) < 32 for c in prefix)
        or any(p in {"", ".", ".."} for p in prefix.split("/"))
    ):
        raise SmokeError("invalid destination prefix")
    name = PurePosixPath(source_key).name
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", run_id) or not name.endswith(".mcap"):
        raise SmokeError("invalid test identity")
    return f"{prefix}/oss-smoke/{run_id}/{name}"


def stage_source(source, key: str, expected_sha: str, max_bytes: int, stream) -> dict:
    info = source.head(key)
    if info is None or info.size <= 0 or info.size > max_bytes:
        raise SmokeError("source missing, empty, or larger than the test limit")
    digest, size = hashlib.sha256(), 0
    for chunk in source.read_chunks(key):
        size += len(chunk)
        if size > max_bytes:
            raise SmokeError("source exceeded the test limit")
        stream.write(chunk)
        digest.update(chunk)
    if size != info.size or digest.hexdigest() != expected_sha:
        raise SmokeError("source checksum or size does not match its committed manifest")
    stream.seek(0)
    from mcap.reader import make_reader

    topics: Counter[str] = Counter()
    first, last = None, None
    for _, channel, message in make_reader(stream, validate_crcs=True).iter_messages(
        log_time_order=False
    ):
        topics[channel.topic] += 1
        first = message.log_time if first is None else min(first, message.log_time)
        last = message.log_time if last is None else max(last, message.log_time)
    if not topics:
        raise SmokeError("MCAP has no messages")
    stream.seek(0)
    return {
        "bytes": size,
        "sha256": digest.hexdigest(),
        "message_count": sum(topics.values()),
        "topics": dict(topics),
        "duration_ns": last - first,
    }


def verify_destination(storage, key: str, stream, expected: dict) -> None:
    info = storage.head(key)
    if info is None or info.size != expected["bytes"]:
        raise SmokeError("OSS object size mismatch")
    digest, size = hashlib.sha256(), 0
    for chunk in storage.read_chunks(key):
        size += len(chunk)
        if size > expected["bytes"]:
            raise SmokeError("OSS readback is larger than the source")
        digest.update(chunk)
    if size != expected["bytes"] or digest.hexdigest() != expected["sha256"]:
        raise SmokeError("OSS readback checksum mismatch")
    for start in sorted({0, max(0, size // 2), max(0, size - 65536)}):
        end = min(start + 65536, size)
        stream.seek(start)
        if storage.read_range(key, start, end) != stream.read(end - start):
            raise SmokeError("OSS range read mismatch")


def source_object(settings, raw_source_id: str):
    import boto3
    import psycopg
    from botocore.config import Config
    from psycopg.rows import dict_row

    if settings.environment != "local" or settings.object_store_provider != "s3":
        raise SmokeError("run this test from the existing local MinIO deployment")
    with psycopg.connect(
        normalize_postgres_dsn(settings.postgres_dsn), row_factory=dict_row
    ) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        rows = connection.execute(
            "SELECT manifest_key FROM ingest.raw_sources "
            "WHERE raw_source_id=%s AND raw_status='COMMITTED' AND source_format='MCAP'",
            (raw_source_id,),
        ).fetchall()
    if len(rows) != 1:
        raise SmokeError("select exactly one committed MCAP source")
    client = boto3.client(
        "s3",
        endpoint_url=settings.object_store_endpoint,
        aws_access_key_id=settings.object_store_access_key,
        aws_secret_access_key=settings.object_store_secret_key,
        region_name=settings.object_store_region,
        config=Config(connect_timeout=10, read_timeout=60, s3={"addressing_style": "path"}),
    )
    source = S3ObjectStorage(client, settings.object_store_bucket)
    body = client.get_object(Bucket=settings.object_store_bucket, Key=rows[0]["manifest_key"])[
        "Body"
    ]
    try:
        payload = body.read(16 * 1024**2 + 1)
    finally:
        body.close()
    if len(payload) > 16 * 1024**2:
        raise SmokeError("source manifest exceeds size limit")
    assets = json.loads(payload).get("upload", {}).get("assets", [])
    candidates = [a for a in assets if a.get("object_key", "").endswith(".mcap")]
    if len(candidates) != 1:
        raise SmokeError("test requires one MCAP in a robot-ingest committed manifest")
    asset = candidates[0]
    sha = asset.get("actual_sha256") or asset.get("expected_sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise SmokeError("source manifest has no SHA-256")
    return source, asset["object_key"], sha


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-source", required=True)
    parser.add_argument("--bucket", required=True, help="must match the platform page")
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--max-bytes", type=int, default=512 * 1024**2)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--check-source", action="store_true")
    args = parser.parse_args()
    try:
        settings = Settings()
        source, source_key, expected_sha = source_object(settings, args.raw_source)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        key = destination_key(args.prefix, source_key, run_id)
        print(
            json.dumps(
                {"stage": "plan", "source_key": source_key, "target": f"oss://{args.bucket}/{key}"}
            ),
            flush=True,
        )
        if not args.execute and not args.check_source:
            return
        bucket = None
        if args.execute:
            repo = PostgresObjectStoreConfigurationRepository.from_dsn(
                settings.postgres_dsn,
                encryption_key=settings.data_source_credential_key.get_secret_value(),
            )
            config = repo.current(settings.platform_environment_id)
            if config is None:
                raise SmokeError("save the OSS test connection in the platform page first")
            if config.bucket != args.bucket:
                raise SmokeError("saved OSS bucket differs from the requested test destination")
            bucket = build_oss_bucket(
                endpoint=config.endpoint,
                bucket=config.bucket,
                access_key=config.access_key,
                secret_key=config.secret_key,
                connect_timeout=60,
            )
        with tempfile.TemporaryFile() as stream:
            evidence = stage_source(source, source_key, expected_sha, args.max_bytes, stream)
            print(json.dumps({"stage": "source_verified", **evidence}), flush=True)
            if bucket is None:
                return
            storage = OssObjectStorage(bucket)
            if storage.head(key) is not None:
                raise SmokeError("target already exists; test will never overwrite it")
            bucket.put_object(
                key,
                stream,
                headers={
                    "x-oss-forbid-overwrite": "true",
                    "Content-Type": "application/octet-stream",
                    "x-oss-meta-sha256": evidence["sha256"],
                },
            )
            print(
                json.dumps({"stage": "uploaded", "target": f"oss://{args.bucket}/{key}"}),
                flush=True,
            )
            verify_destination(storage, key, stream, evidence)
            print(
                json.dumps(
                    {
                        "stage": "verified",
                        "target": f"oss://{args.bucket}/{key}",
                        "sha256": evidence["sha256"],
                        "bytes": evidence["bytes"],
                        "range_read": "passed",
                        "platform_ingestion": "not_run",
                    }
                ),
                flush=True,
            )
    except SmokeError as exc:
        parser.exit(1, f"OSS test stopped: {exc}\n")
    except Exception as exc:
        status = getattr(exc, "status", None)
        parser.exit(
            1,
            f"OSS test failed ({type(exc).__name__}, HTTP status {status}); "
            "no credentials logged.\n",
        )


if __name__ == "__main__":
    main()
