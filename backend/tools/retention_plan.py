"""Verify recoverability; optionally evict only the exact verified cache objects."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from hc_data_platform.continuous_recordings.retention import retirement_plan, retire_recording
from hc_data_platform.core.config import get_settings
from hc_data_platform.core.context import RequestContext, bind_request_context
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.episode_store.service import EpisodePackageService
from hc_data_platform.runtime import _object_store_clients


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--organization", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--region", required=True)
    p.add_argument("--recording")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--workers", type=int, choices=range(1, 5), default=1)
    args = p.parse_args()
    scope = (args.organization, args.project, args.region)
    bind_request_context(
        RequestContext(
            organization_id=scope[0],
            project_id=scope[1],
            region_code=scope[2],
            service_identity=True,
        )
    )
    s = get_settings()
    cf = psycopg_connection_factory(s.postgres_dsn)
    _, _, store = _object_store_clients(s)
    from hc_data_platform.continuous_recordings.cache_lifecycle import ProcessingCacheLifecycle
    from hc_data_platform.storage.postgres import PostgresStorageRepository
    lifecycle = ProcessingCacheLifecycle(cf, store, PostgresStorageRepository(cf))
    with cf() as db:
        records = db.execute(
            "SELECT recording_id FROM ingest.continuous_recordings "
            "WHERE status='SLICED' AND organization_id=%s AND project_id=%s "
            "AND region_code=%s ORDER BY created_at DESC",
            (args.organization, args.project, args.region),
        ).fetchall()
    if args.recording:
        records = [r for r in records if r[0] == args.recording]

    def inspect_one(row):
        recording = row[0]
        bind_request_context(
            RequestContext(
                organization_id=scope[0],
                project_id=scope[1],
                region_code=scope[2],
                service_identity=True,
            )
        )
        with TemporaryDirectory(prefix="retention-plan-") as folder:
            service = EpisodePackageService(cf, store, Path(folder))
            try:
                operation = retire_recording if args.apply else retirement_plan
                kwargs = {"authorize": lifecycle.authorize, "on_deleted": lambda item: lifecycle.audit(scope, item)} if args.apply else {}
                items = operation(cf, store, service, scope, recording, **kwargs)
                return {
                    "recording": recording,
                    "files": len(items),
                    "bytes": sum(x["size"] for x in items),
                    "items": items,
                }
            except Exception as error:
                return {"recording": recording, "blocked": str(error)}
            finally:
                service.close()

    candidates = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(inspect_one, records):
            candidates.extend(result.get("items", ()))
            print(json.dumps(result, ensure_ascii=False), flush=True)
    print(
        json.dumps(
            {
                "deleted_files" if args.apply else "verified_files": len(candidates),
                "deleted_bytes" if args.apply else "verified_bytes": sum(x["size"] for x in candidates),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
