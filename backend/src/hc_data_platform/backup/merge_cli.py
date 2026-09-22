"""Maintenance CLI used by deploy/scripts/merge-dev-data.py; no credentials in argv."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import boto3
import psycopg
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from temporalio.client import Client

from hc_data_platform.backup.merge_bundle import (
    check_objects,
    copy_objects,
    export_bundle,
    file_hash,
    load_bundle,
    write_json,
)
from hc_data_platform.backup.merge_database import MergeError, merge_database
from hc_data_platform.core.dbapi import normalize_postgres_dsn


def _progress(value: dict[str, Any]) -> None:
    print(json.dumps(value), file=sys.stderr, flush=True)


async def _open_workflows() -> int:
    client = await Client.connect(os.environ.get("HC_TEMPORAL_TARGET", "temporal:7233"))
    result = await client.count_workflows('ExecutionStatus = "Running"')
    return result.count


def _require_quiet(connection: psycopg.Connection[Any]) -> None:
    if os.environ.get("HC_MERGE_QUIESCED") != "true":
        raise MergeError("MERGE_USE_COMPOSE_WRAPPER")
    if asyncio.run(asyncio.wait_for(_open_workflows(), timeout=15)):
        raise MergeError("MERGE_ACTIVE_TEMPORAL_WORKFLOWS")
    if connection.execute(
        "SELECT 1 FROM core.outbox_events WHERE published_at IS NULL LIMIT 1"
    ).fetchone():
        raise MergeError("MERGE_PENDING_OUTBOX_EVENTS")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="离线业务数据合并；使用 Compose 包装脚本运行")
    parser.add_argument("command", choices=("export", "plan", "import", "check"))
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--rollback-dump", type=Path)
    parser.add_argument("--report", type=Path)
    return parser


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if os.environ.get("HC_ENVIRONMENT", "local") != "local":
        raise MergeError("MERGE_LOCAL_COMPOSE_ONLY")
    endpoint = os.environ["HC_OBJECT_STORE_ENDPOINT"]
    bucket = os.environ["HC_OBJECT_STORE_BUCKET"]
    credential_key = os.environ.get(
        "HC_DATA_SOURCE_CREDENTIAL_KEY", "local-data-source-credential-key-change-me"
    )
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["HC_OBJECT_STORE_ACCESS_KEY"],
        aws_secret_access_key=os.environ["HC_OBJECT_STORE_SECRET_KEY"],
        region_name=os.environ.get("HC_OBJECT_STORE_REGION", "us-east-1"),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=10,
            read_timeout=90,
            retries={"max_attempts": 2},
        ),
    )
    with psycopg.connect(
        normalize_postgres_dsn(os.environ["HC_POSTGRES_DSN"]), autocommit=True
    ) as connection:
        _require_quiet(connection)
        if args.command == "check":
            result: dict[str, Any] = {"status": "quiescent"}
        elif args.command == "export":
            if args.bundle is None:
                raise MergeError("MERGE_BUNDLE_REQUIRED")
            result = export_bundle(
                connection,
                client,
                bucket,
                args.bundle,
                credential_key=credential_key,
                progress=_progress,
            )
        else:
            if args.bundle is None:
                raise MergeError("MERGE_BUNDLE_REQUIRED")
            manifest = load_bundle(args.bundle, bucket=bucket, credential_key=credential_key)
            objects = check_objects(client, bucket, manifest)
            # A real rollback transaction tests all constraints, identities and audit
            # chains before any new object is written into the destination.
            result = merge_database(connection, args.bundle, manifest, dry_run=True)
            result["objects"] = objects
            if args.command == "import" and result["status"] != "already_imported":
                if args.rollback_dump is None or args.rollback_dump.is_symlink():
                    raise MergeError("MERGE_ROLLBACK_DUMP_REQUIRED")
                with args.rollback_dump.open("rb") as stream:
                    if stream.read(5) != b"PGDMP":
                        raise MergeError("MERGE_ROLLBACK_DUMP_INVALID")
                copied: dict[str, Any] = {}

                def transfer() -> None:
                    copied.update(
                        copy_objects(client, bucket, args.bundle, manifest, progress=_progress)
                    )

                result = merge_database(
                    connection, args.bundle, manifest, dry_run=False, before_commit=transfer
                )
                result["objects"] = copied
                result["rollback_dump_sha256"] = file_hash(args.rollback_dump)
        if args.report is not None:
            write_json(args.report, result)
        summary_keys = (
            "status",
            "bundle_id",
            "project_count",
            "dataset_count",
            "kept_target_accounts",
            "renamed_accounts",
            "objects",
            "object_bytes",
            "tables",
        )
        print(
            json.dumps(
                {k: result[k] for k in summary_keys if k in result},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except MergeError as exc:
        print(
            json.dumps({"status": "failed", "code": exc.code, "table": exc.table}), file=sys.stderr
        )
        raise SystemExit(2) from None
    except psycopg.Error as exc:
        # Constraint names help diagnosis; DETAIL can contain passwords/entire rows.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "MERGE_DATABASE_REJECTED",
                    "sqlstate": exc.sqlstate,
                    "table": exc.diag.table_name,
                    "constraint": exc.diag.constraint_name,
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    except (BotoCoreError, ClientError):
        print('{"status":"failed","code":"MERGE_OBJECT_STORE_UNAVAILABLE"}', file=sys.stderr)
        raise SystemExit(2) from None
    except Exception:
        print('{"status":"failed","code":"MERGE_INPUT_OR_RUNTIME_INVALID"}', file=sys.stderr)
        raise SystemExit(3) from None


if __name__ == "__main__":
    main()
