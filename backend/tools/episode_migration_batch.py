"""W7 operator batch prepare/publish/status/rollback with explicit endpoints.

Requires already verified, committed conversion spools. Preparation stages SDK
previews and records an unpublished plan; publish switches storage/product
pointers atomically. Rollback retains old and new data. Use the verified scope.
"""

import argparse
import json
import os
from pathlib import Path

import boto3

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.episode_store.catalog import PostgresLegacyEntries
from hc_data_platform.episode_store.collections import EpisodeCollections
from hc_data_platform.episode_store.errors import PackageConflict
from hc_data_platform.episode_store.migration_batches import MigrationBatches
from hc_data_platform.episode_store.migration_conversion import MigrationEpisodePlan, _save
from hc_data_platform.episode_store.models import CommitReceipt
from hc_data_platform.episode_store.postgres import PostgresEpisodeStore
from hc_data_platform.episode_store.validation import document, package_file
from hc_data_platform.lance_catalog.adapters import (
    LanceAdapter,
    PostgresAdvisoryDatasetLock,
    PostgresCatalogAdapter,
)
from hc_data_platform.lance_catalog.service import LanceCatalogService
from hc_data_platform.object_storage.providers import S3ObjectStorage


def load_spools(directories):
    plans, roots = [], {}
    for directory in directories:
        path = Path(directory)
        plan = MigrationEpisodePlan.model_validate(document(path, "plan.json"))
        receipt = CommitReceipt.model_validate(document(path, "staged.json"))
        converted = document(path, "converted.json")
        relative = converted["package"]
        package_file(path, relative + "/meta/info.json")
        if (
            plan.sha256 in roots
            or receipt.data.identity != plan.legacy.identity
            or receipt.data.data_version_id != plan.source.data_version_id
            or receipt.manifest_sha256 != converted["manifest_sha256"]
        ):
            raise PackageConflict("conversion spool identity/checkpoint differs or repeats")
        plans.append(plan)
        roots[plan.sha256] = path / relative
    return plans, roots


def configured_batches():
    # No application environment fallbacks; the operator pins every endpoint.
    connections = psycopg_connection_factory(os.environ["HC_EPISODE_MIGRATION_DSN"])
    packages = PostgresEpisodeStore(connections)
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
    legacy = LanceCatalogService(
        LanceAdapter(
            os.environ["HC_EPISODE_MIGRATION_LANCE_ROOT"],
            storage_options=json.loads(os.environ.get("HC_EPISODE_MIGRATION_LANCE_OPTIONS", "{}")),
        ),
        PostgresCatalogAdapter(connections),
        PostgresAdvisoryDatasetLock(connections),
    )
    batches = MigrationBatches(
        EpisodeCollections(
            packages, storage, legacy=legacy, legacy_entries=PostgresLegacyEntries(packages, legacy)
        )
    )
    return batches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "publish", "status", "rollback"))
    for name in ("organization", "project", "region", "dataset", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--batch-key", help="stable operator key for prepare (1..128 characters)")
    parser.add_argument("--batch-id", help="persisted mb_* id for publish/status/rollback")
    parser.add_argument(
        "--plan-dir",
        action="append",
        default=[],
        help="repeat for each committed conversion checkpoint directory",
    )
    args = parser.parse_args()
    if args.command == "prepare" and (not args.batch_key or not args.plan_dir):
        parser.error("prepare requires --batch-key and --plan-dir")
    if args.command != "prepare" and not args.batch_id:
        parser.error("publish/status/rollback require --batch-id")
    output = Path(args.output)
    if output.exists() or output.is_symlink():
        parser.error("output already exists; select a new immutable report path")
    plans, roots = load_spools(args.plan_dir)
    batches = configured_batches()
    token = bind_request_context(
        RequestContext(
            organization_id=args.organization,
            project_id=args.project,
            region_code=args.region,
            subject_id="episode-migration-batch",
            service_identity=True,
        )
    )
    try:
        if args.command == "prepare":
            result = batches.prepare(args.dataset, args.batch_key, plans, roots)
        elif args.command == "publish":
            result = batches.publish(args.dataset, args.batch_id, roots)
        elif args.command == "rollback":
            result = batches.rollback(args.dataset, args.batch_id)
        else:
            result = batches.get(args.dataset, args.batch_id)
        output.parent.mkdir(parents=True, exist_ok=True)
        _save(output, result)
        print(
            json.dumps(
                {
                    "batch_id": result["batch_id"],
                    "status": result["status"],
                    "members": len(result["plan"]["members"]),
                    "collection_version_id": result["plan"]["collection"]["collection_version_id"],
                    "command": args.command,
                    "output": str(output),
                },
                indent=2,
            )
        )
    finally:
        reset_request_context(token)


if __name__ == "__main__":
    main()
