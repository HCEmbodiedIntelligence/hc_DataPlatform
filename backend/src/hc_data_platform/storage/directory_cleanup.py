"""Retryable cleanup of a specifically confirmed directory deletion."""

from datetime import datetime, timezone
from hashlib import sha256
import logging
import psycopg

from .directory_objects import DirectoryObjectPurger
from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.core.errors import ProblemException
from .directory_files import ASSETS, collect_files, exclude_shared
from .directory_records import delete_records

logger = logging.getLogger(__name__)


def configured_storage(settings, org):
    from hc_data_platform.platform_ops.object_store_config import (
        PostgresObjectStoreConfigurationRepository,
    )
    from hc_data_platform.storage.organization_storage import OrganizationStorage
    from hc_data_platform.runtime import _physical_object_store_clients

    config = settings
    if settings.object_store_owner_organization_id:
        repo = PostgresObjectStoreConfigurationRepository.from_dsn(
            settings.postgres_dsn,
            encryption_key=settings.data_source_credential_key.get_secret_value(),
        )
        try:
            config, _ = OrganizationStorage(settings, repo, None, None).settings_for(org)
        except ProblemException as exc:
            if exc.problem.code != "ORGANIZATION_STORAGE_UNCONFIGURED":
                raise
            config = None
    from hc_data_platform.registry.filesystem_storage import FilesystemRobotModelStorage

    models = lambda: FilesystemRobotModelStorage(
        settings.robot_model_asset_root, signing_secret=settings.cursor_secret
    )
    if config is None:
        objects = DirectoryObjectPurger(
            None, "", alignment_staging_root=settings.alignment_staging_root
        )
        objects.remote_configured = False
        objects.models = models
        return "local-only:" + str(settings.robot_model_asset_root), objects
    client, _, _ = _physical_object_store_clients(config)
    identity = sha256(
        repr(
            (
                config.object_store_provider,
                config.object_store_endpoint,
                config.object_store_bucket,
                config.object_store_prefix,
                str(config.object_store_cache_root),
            )
        ).encode()
    ).hexdigest()
    objects = DirectoryObjectPurger(
        client, config.object_store_bucket, alignment_staging_root=config.alignment_staging_root
    )
    objects.models = models
    return identity, objects


class DirectoryCleanup:
    def __init__(self, dsn, storage):
        self.dsn = normalize_postgres_dsn(dsn)
        self.storage = storage

    def run_once(self):
        with psycopg.connect(self.dsn) as db:
            row = db.execute("SELECT * FROM access_control.next_directory_cleanup()").fetchone()
            if not row:
                return 0
            org, project = row
            if not db.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s,0))", ("directory-cleanup/" + org,)
            ).fetchone()[0]:
                return 0
            try:
                with db.transaction():
                    return self._process(db, org, project)
            except Exception as exc:
                if isinstance(exc, ProblemException):
                    code = exc.problem.code
                else:
                    code = getattr(exc, "sqlstate", None) or type(exc).__name__
                db.execute(
                    "SELECT set_config('app.platform_admin','true',true)"
                )  # Only after a verified due job.
                db.execute(
                    """UPDATE access_control.retired_scopes SET next_attempt_at=now()+interval '1 minute',last_error=%s
                    WHERE organization_id=%s AND project_id=%s AND completed_at IS NULL""",
                    (str(code), org, project),
                )
                logger.warning("Directory cleanup pending for %s/%s (%s)", org, project, code)
                return 0

    def _process(self, db, org, project):
        from psycopg import sql

        if not db.execute(
            "SELECT 1 FROM access_control.next_directory_cleanup(%s,%s)", (org, project)
        ).fetchone():
            return 0
        db.execute(
            "SELECT set_config('app.platform_admin','true',true),set_config('app.is_admin','true',true),set_config('app.organization_id',%s,true),set_config('app.project_id',%s,true)",
            (org, project),
        )
        lock = db.execute(
            """SELECT cleanup_planned_at FROM access_control.retired_scopes
            WHERE organization_id=%s AND project_id=%s AND status='DELETED' AND purge_after<=now() AND completed_at IS NULL
            FOR UPDATE""",
            (org, project),
        ).fetchone()
        if not lock:
            return 0
        # The platform-admin transaction markers are the existing read policies
        # for shared-reference discovery. Keep RLS enabled for the service role.
        if lock[0] is None:
            has_assets = any(
                db.execute(
                    sql.SQL(
                        "SELECT 1 FROM {} WHERE organization_id=%s AND (%s='' OR project_id=%s) LIMIT 1"
                    ).format(sql.Identifier(*table.split("."))),
                    (org, project, project),
                ).fetchone()
                for table in ASSETS
            )
            if has_assets:
                identity, objects = self.storage(org)
                files = collect_files(db, org, project, objects, datetime.now(timezone.utc))
                if not getattr(objects, "remote_configured", True) and any(
                    kind != "local" and not key.startswith("model://") for kind, key, _ in files
                ):
                    raise RuntimeError("Directory storage not configured")
                with db.cursor() as c:
                    c.executemany(
                        """INSERT INTO access_control.directory_cleanup_files
                        (organization_id,project_id,file_kind,locator,upload_id,storage_identity) VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT DO NOTHING""",
                        ((org, project, *item, identity) for item in sorted(files)),
                    )
            # Detach business data and commit its exact file manifest together.
            delete_records(db, org, project)
            db.execute(
                """UPDATE access_control.retired_scopes SET cleanup_planned_at=now(),last_error=NULL,next_attempt_at=NULL
                WHERE organization_id=%s AND project_id=%s""",
                (org, project),
            )
            return 1
        rows = db.execute(
            """SELECT file_kind,locator,upload_id,storage_identity FROM access_control.directory_cleanup_files
            WHERE organization_id=%s AND project_id=%s ORDER BY file_kind,locator LIMIT 64""",
            (org, project),
        ).fetchall()
        if not rows:
            db.execute(
                """UPDATE access_control.retired_scopes SET completed_at=now(),last_error=NULL,next_attempt_at=NULL
                WHERE organization_id=%s AND project_id=%s""",
                (org, project),
            )
            return 1
        identity, objects = self.storage(org)
        if any(row[3] != identity for row in rows):
            raise RuntimeError("Directory storage changed after deletion")
        selected = {row[:3] for row in rows}
        owned = exclude_shared(db, org, project, objects, selected)
        objects.purge(
            {
                "objects": [key for kind, key, _ in owned if kind == "object"],
                "local_files": [key for kind, key, _ in owned if kind == "local"],
                "multiparts": [(key, upload) for kind, key, upload in owned if kind == "multipart"],
            }
        )
        with db.cursor() as c:
            c.executemany(
                """DELETE FROM access_control.directory_cleanup_files WHERE organization_id=%s AND project_id=%s
                AND file_kind=%s AND locator=%s AND upload_id=%s""",
                ((org, project, *item) for item in selected),
            )
        return 1


async def serve_directory_cleanup(settings, gate, instance_id):
    import asyncio
    from hc_data_platform.platform_ops.maintenance import writer_permit_scope

    cleanup = DirectoryCleanup(settings.postgres_dsn, lambda org: configured_storage(settings, org))

    def run():
        with writer_permit_scope(
            gate,
            environment_id=settings.platform_environment_id,
            writer_id="directory-cleanup:" + instance_id,
            writer_kind="maintenance_controller",
        ):
            return cleanup.run_once()

    while True:
        try:
            progress = await asyncio.to_thread(run)
        except Exception as exc:
            logger.warning("Directory cleanup unavailable (%s)", type(exc).__name__)
            progress = 0
        await asyncio.sleep(0.2 if progress else 10)
