"""W7 operator inventory CLI. Uses only the explicitly configured database.

This command cannot mutate business rows, object storage or visible pointers.
G2 application/cutover must remain a separate, reviewed operation.
"""

import argparse
import json
import os

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.episode_store.migration_inventory import capture_inventory, save_inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inventory"])
    for field in ("organization", "project", "region", "dataset", "output"):
        parser.add_argument("--" + field, required=True)
    parser.add_argument("--max-rows", type=int, default=10000)
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
