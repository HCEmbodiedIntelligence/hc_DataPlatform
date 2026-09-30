"""Plan/stage one historical native Episode against explicit isolated endpoints.

Planning only reads source storage and writes a local immutable plan. Staging
commits a verified package with activation disabled; batch publication is separate.
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
from hc_data_platform.episode_store.commit import EpisodePackageCommitter
from hc_data_platform.episode_store.migration_conversion import (
    LegacyEpisodeConverter,
    MigrationEpisodePlan,
    _save,
)
from hc_data_platform.episode_store.migration_inventory import read_inventory
from hc_data_platform.episode_store.migration_native import NativeMigrationAssets
from hc_data_platform.episode_store.models import LegacyEpisodeRef
from hc_data_platform.episode_store.postgres import PackageConflict, PostgresEpisodeStore
from hc_data_platform.ingest.adapters import S3ObjectStorage
from hc_data_platform.lance_catalog.adapters import (
    LanceAdapter,
    PostgresAdvisoryDatasetLock,
    PostgresCatalogAdapter,
)
from hc_data_platform.lance_catalog.service import LanceCatalogService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "stage"))
    for name in (
        "organization",
        "project",
        "region",
        "dataset",
        "episode",
        "inventory",
        "directory",
        "output",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--version", type=int, required=True, help="exact legacy logical version")
    parser.add_argument("--max-frames", type=int, default=20000)
    parser.add_argument("--plan", help="previous immutable plan, required by stage")
    args = parser.parse_args()
    if args.command == "stage" and not args.plan:
        parser.error("stage requires --plan")
    output = Path(args.output)
    if output.exists() or output.is_symlink():
        parser.error("output exists; choose a new immutable report path")
    inventory = read_inventory(args.inventory)
    candidates = [
        c
        for c in inventory["inventory"]["legacy_candidates"]
        if c["state"] == "AWAITING_VERIFICATION"
        and c["episode_id"] == args.episode
        and c["version_id"] == f"version_lance_{args.version}"
    ]
    if len(candidates) != 1:
        parser.error("Episode/version is absent or ambiguous in the frozen inventory")
    reference = LegacyEpisodeRef.model_validate(candidates[0]["legacy"])
    if reference.identity.dataset_id != args.dataset:
        parser.error("dataset differs from the inventoried Episode")
    expected = (
        MigrationEpisodePlan.model_validate_json(Path(args.plan).read_bytes())
        if args.plan
        else None
    )
    # Deliberately no application defaults or public READY export authorizer.
    connections = psycopg_connection_factory(os.environ["HC_EPISODE_MIGRATION_DSN"])
    repository = PostgresEpisodeStore(connections)
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
            storage_options=json.loads(os.environ["HC_EPISODE_MIGRATION_LANCE_OPTIONS"]),
        ),
        PostgresCatalogAdapter(connections),
        PostgresAdvisoryDatasetLock(connections),
    )
    token = bind_request_context(
        RequestContext(
            organization_id=args.organization,
            project_id=args.project,
            region_code=args.region,
            subject_id="episode-migration-stage",
            service_identity=True,
        )
    )
    try:
        loader = NativeMigrationAssets(
            connections,
            storage,
            legacy,
            Path(args.directory) / "assets",
            max_frames=args.max_frames,
        )
        with loader.load(inventory, reference) as (plan, assets):
            if expected is not None and expected != plan:
                raise PackageConflict("prepared historical inputs differ from the frozen plan")
            if args.command == "plan":
                result = plan.model_dump(mode="json")
            else:
                converter = LegacyEpisodeConverter(
                    legacy,
                    EpisodePackageCommitter(repository, storage),
                    Path(args.directory) / "spool",
                )
                receipt, report, root = converter.stage(plan, assets)
                result = {
                    "plan_sha256": plan.sha256,
                    "receipt": receipt.model_dump(mode="json"),
                    "equivalence": report,
                    "package": str(root),
                    "spool": str(root.parent.parent),
                }
            output.parent.mkdir(parents=True, exist_ok=True)
            _save(output, result)
            print(
                json.dumps(
                    {
                        "command": args.command,
                        "plan_sha256": plan.sha256,
                        "episode_id": args.episode,
                        "output": str(output),
                        "visible_pointers_changed": False,
                    },
                    indent=2,
                )
            )
    finally:
        reset_request_context(token)


if __name__ == "__main__":
    main()
