"""Explicit internal CLI for producing sealed storage inventory snapshots."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from hc_data_platform.core.config import get_settings
from hc_data_platform.runtime import build_storage_inventory

from .inventory_worker import run_inventory_cycle


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hc-storage-inventory")
    parser.add_argument(
        "--scope",
        action="append",
        default=[],
        metavar="ORGANIZATION/PROJECT/REGION",
        help=(
            "Exact tenant scope to inventory. Repeat for multiple scopes; defaults to "
            "HC_STORAGE_INVENTORY_SCOPES."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    arguments = _parser().parse_args(argv)
    settings = get_settings()
    scopes = tuple(arguments.scope) or settings.storage_inventory_scopes
    if not scopes:
        raise SystemExit(
            "no inventory scope configured; pass --scope or set HC_STORAGE_INVENTORY_SCOPES"
        )
    runtime = build_storage_inventory(settings, scopes=scopes)
    snapshots = run_inventory_cycle(runtime.producer, scopes=scopes, strict=True)
    print(
        json.dumps(
            [snapshot.model_dump(mode="json") for snapshot in snapshots],
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
