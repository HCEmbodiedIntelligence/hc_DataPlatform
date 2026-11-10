"""Backfill episode-owned native capture and verify existing training values."""

import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from hc_data_platform.core.config import get_settings
from hc_data_platform.core.context import RequestContext, bind_request_context
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.episode_store.holobrain_export import synchronized
from hc_data_platform.episode_store.models import CommitReceipt
from hc_data_platform.episode_store.native_sources import native_source
from hc_data_platform.episode_store.normalized_export import frame_selection, selected_source
from hc_data_platform.episode_store.service import EpisodePackageService
from hc_data_platform.runtime import _object_store_clients


def content_hash(path, frames, directory):
    h, count = hashlib.sha256(), 0
    report = {"candidate_frames": 0, "sync_drops": []}
    for row, trace in synchronized(path, directory, report, lambda: None):
        if trace["rgb_time_ns"] in frames:
            h.update(
                json.dumps(
                    row,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=lambda value: {"bytes_sha256": hashlib.sha256(value).hexdigest()},
                ).encode()
            )
            count += 1
    return count, h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--verify-holobrain", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        parser.error("Full native backfill is retired; use verified Raw references to avoid duplicate data")
    if args.apply and not args.verify_holobrain:
        parser.error("--apply requires --verify-holobrain; baseline failures stop migration")
    bind_request_context(
        RequestContext(
            organization_id=args.organization,
            project_id=args.project,
            region_code=args.region,
            service_identity=True,
        )
    )
    s = get_settings()
    cf = psycopg_connection_factory(s.postgres_dsn)
    _, _, storage = _object_store_clients(s)
    with cf() as db:
        rows = db.execute(
            "SELECT receipt_document FROM episode_store.versions "
            "WHERE status='COMMITTED' AND organization_id=%s AND project_id=%s "
            "AND region_code=%s ORDER BY committed_at DESC",
            (args.organization, args.project, args.region),
        ).fetchall()
    if args.limit:
        rows = rows[: args.limit]
    with TemporaryDirectory(prefix="native-retention-") as directory:
        service = EpisodePackageService(cf, storage, Path(directory))
        try:
            for (row,) in rows:
                receipt = CommitReceipt.model_validate(row)
                package = service.files.open(receipt.data.identity, receipt.data.data_version_id)
                selected_source(package)
                if not args.apply:
                    print(
                        "PLAN", receipt.data.data_version_id, receipt.data.frame_count, flush=True
                    )
                    continue
                selected_source(package, holobrain=True)
                selection = SimpleNamespace(package=package, annotation_file=None, track=None)
                frames = frame_selection(selection, compact_invalid=True)

                def verify(before, after, frames=frames, receipt=receipt):
                    with TemporaryDirectory(prefix="native-verify-") as folder:
                        root = Path(folder)
                        (root / "a").mkdir()
                        (root / "b").mkdir()
                        original = content_hash(before, frames, root / "a")
                        migrated = content_hash(after, frames, root / "b")
                        if original != migrated:
                            raise ValueError(
                                f"Native conversion changed training rows: {original} != {migrated}"
                            )
                        print("VERIFIED", receipt.data.data_version_id, *migrated, flush=True)
                        return {
                            "status": "VERIFIED",
                            "method": "holobrain-rows-sha256/v1",
                            "frames": migrated[0],
                            "sha256": migrated[1],
                        }

                file, provenance, audit = native_source(
                    package, storage, allow_backfill=True, verifier=verify
                )
                print("BACKFILLED", receipt.data.data_version_id, file.object.size, flush=True)
        finally:
            service.close()


if __name__ == "__main__":
    main()
