"""Review legacy configurations, then import shared drafts without publishing or rebinding.

An explicit HC_CAPTURE_MIGRATION_DSN is required; application defaults are never used.
The apply command requires the exact saved plan and performs one atomic transaction.
"""

import argparse
import json
import os
from pathlib import Path

from hc_data_platform.capture_schemes.legacy_migration import apply_migration, migration_plan
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("dry-run", "apply"))
    parser.add_argument("--organization", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--actor", default="capture-migration-operator")
    args = parser.parse_args()
    if args.command == "apply" and args.plan is None:
        parser.error("apply requires the reviewed --plan file")
    connect = psycopg_connection_factory(os.environ["HC_CAPTURE_MIGRATION_DSN"])
    token = bind_request_context(
        RequestContext(
            organization_id=args.organization, subject_id=args.actor, service_identity=True
        )
    )
    try:
        with connect() as db:
            if args.command == "dry-run":
                # The scoped factory has only installed session RLS settings.
                db.commit()
                db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                result = migration_plan(db, args.organization)
            else:
                result = apply_migration(
                    db, args.organization, json.loads(args.plan.read_text()), args.actor
                )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"command": args.command, "output": str(args.output)}, ensure_ascii=False))
    finally:
        reset_request_context(token)


if __name__ == "__main__":
    main()
