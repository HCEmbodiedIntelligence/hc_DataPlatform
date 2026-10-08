"""W7 operator inventory and object verification CLI. Explicit endpoints only.

This command cannot mutate business rows, object storage or visible pointers.
G2 application/cutover follows completion of the authorized G1 gates.
"""

import argparse
import json
import os
from pathlib import Path

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.episode_store.migration_inventory import (
    capture_inventory,
    read_inventory,
    save_inventory,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inventory", "verify-objects"])
    for field in ("organization", "project", "region", "dataset", "output"):
        parser.add_argument("--" + field, required=True)
    parser.add_argument("--max-rows", type=int, default=10000)
    parser.add_argument("--inventory", help="frozen inventory required by verify-objects")
    parser.add_argument("--max-objects", type=int, default=100000)
    parser.add_argument("--max-total-bytes", type=int, default=32 * 1024**3)
    args = parser.parse_args()
    # Never infer the source/target from application or production defaults.
    connections = psycopg_connection_factory(os.environ["HC_EPISODE_MIGRATION_DSN"])
    token = bind_request_context(
        RequestContext(
            organization_id=args.organization,
            project_id=args.project,
            region_code=args.region,
            subject_id="episode-migration-inventory",
            service_identity=True,
        )
    )
    try:
        result = capture_inventory(connections, args.dataset, max_rows=args.max_rows)
        if args.command == "verify-objects":
            if not args.inventory:
                parser.error("verify-objects requires --inventory")
            import boto3

            from hc_data_platform.episode_store.errors import PackageConflict
            from hc_data_platform.episode_store.migration_conversion import _save
            from hc_data_platform.episode_store.migration_objects import InventoryObjectVerifier
            from hc_data_platform.object_storage.providers import S3ObjectStorage

            frozen = read_inventory(args.inventory)
            if frozen != result:
                raise PackageConflict(
                    "database inventory changed; capture a fresh snapshot before verification"
                )
            storage = S3ObjectStorage(
                boto3.client(
                    "s3",
                    endpoint_url=os.environ["HC_EPISODE_MIGRATION_OBJECT_ENDPOINT"],
                    aws_access_key_id=os.environ["HC_EPISODE_MIGRATION_OBJECT_ACCESS_KEY"],
                    aws_secret_access_key=os.environ["HC_EPISODE_MIGRATION_OBJECT_SECRET_KEY"],
                    region_name=os.environ.get("HC_EPISODE_MIGRATION_OBJECT_REGION", "us-east-1"),
                ),
                os.environ["HC_EPISODE_MIGRATION_OBJECT_BUCKET"],
            )
            verified = InventoryObjectVerifier(
                storage, max_objects=args.max_objects, max_total_bytes=args.max_total_bytes
            ).verify(frozen)
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            _save(output, verified)
            report = verified["verification"]
            print(
                json.dumps(
                    {
                        "verification_sha256": verified["verification_sha256"],
                        **{
                            key: report[key]
                            for key in (
                                "inventory_sha256",
                                "scope",
                                "status",
                                "unique_objects",
                                "unique_declared_bytes",
                                "remaining_checks",
                            )
                        },
                        "issues": len(report["issues"]),
                    },
                    indent=2,
                )
            )
            if report["issues"]:
                raise SystemExit(2)
            return
        save_inventory(args.output, result)
        print(
            json.dumps(
                {
                    "inventory_sha256": result["inventory_sha256"],
                    "scope": result["inventory"]["scope"],
                    "rows": {k: len(v) for k, v in result["inventory"]["tables"].items()},
                    "dry_run": result["inventory"]["dry_run"],
                },
                indent=2,
            )
        )
    finally:
        reset_request_context(token)


if __name__ == "__main__":
    main()
