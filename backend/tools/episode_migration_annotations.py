"""Prepare/apply/status of explicitly selected legacy annotation equivalence.

Uses published or archived verified migration batches, explicit operator scope
and endpoints. Original history stays intact; new tasks require new submission
and review. No HTTP operator endpoint or implicit production connection exists.
"""

import argparse
import json
from pathlib import Path

from episode_migration_batch import configured_batches

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.episode_store.migration_annotations import MigrationAnnotations
from hc_data_platform.episode_store.migration_conversion import _save
from hc_data_platform.episode_store.models import LegacyEpisodeRef


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "apply", "status"))
    for name in ("organization", "project", "region", "dataset", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--batch-id")
    parser.add_argument("--source-task-id")
    parser.add_argument("--source-revision", type=int)
    parser.add_argument(
        "--source-reference", type=Path, help="JSON of a complete pinned LegacyEpisodeRef"
    )
    parser.add_argument("--mapping-id")
    args = parser.parse_args()
    if args.command == "prepare" and (
        not args.batch_id
        or not args.source_task_id
        or args.source_revision is None
        or not args.source_reference
    ):
        parser.error("prepare requires batch, explicit source task/revision and complete reference")
    if args.command != "prepare" and not args.mapping_id:
        parser.error("apply/status require mapping-id")
    output = Path(args.output)
    if output.exists() or output.is_symlink():
        parser.error("output already exists; select a new immutable report path")
    source = None
    if args.command == "prepare":
        if (
            args.source_reference.is_symlink()
            or not 0 < args.source_reference.stat().st_size <= 65536
        ):
            parser.error("source reference must be a bounded regular JSON file")
        source = LegacyEpisodeRef.model_validate_json(args.source_reference.read_bytes())
    service = MigrationAnnotations(configured_batches())
    token = bind_request_context(
        RequestContext(
            organization_id=args.organization,
            project_id=args.project,
            region_code=args.region,
            subject_id="episode-annotation-migration",
            service_identity=True,
        )
    )
    try:
        if args.command == "prepare":
            result = service.prepare(
                args.dataset, args.batch_id, args.source_task_id, args.source_revision, source
            )
        elif args.command == "apply":
            result = service.apply(args.dataset, args.mapping_id)
        else:
            result = service.get(args.dataset, args.mapping_id)
        output.parent.mkdir(parents=True, exist_ok=True)
        _save(output, result)
        print(
            json.dumps(
                {
                    "mapping_id": result["mapping_id"],
                    "status": result["status"],
                    "target_task_id": result["plan"]["target_task_id"],
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
