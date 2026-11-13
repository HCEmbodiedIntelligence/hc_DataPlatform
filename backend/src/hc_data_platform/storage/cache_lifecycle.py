"""Scoped, policy-controlled expiry of reconstructible local preview caches."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from hc_data_platform.continuous_recordings.cache_lifecycle import ProcessingCacheLifecycle
from hc_data_platform.core.context import current_request_context
from hc_data_platform.episode_store.models import DataVersionRef
from hc_data_platform.episode_store.service import EpisodePackageService

from .cache_policy import select_policy
from .local_cache import local_cache_for
from .models import LifecycleAuditEvent, LifecyclePolicyAction

ACTIONS = {
    "recording-previews": LifecyclePolicyAction.CLEAN_RECORDING_PREVIEWS,
    "episode-previews": LifecyclePolicyAction.CLEAN_EPISODE_PREVIEWS,
    "episode-deliveries": LifecyclePolicyAction.CLEAN_EXPORT_ARCHIVES,
}
logger = logging.getLogger(__name__)


class CacheLifecycle:
    def __init__(
        self, connections, storage, repository, *, clock=lambda: datetime.now(timezone.utc)
    ):
        self.connections, self.storage, self.repository, self.clock = (
            connections,
            storage,
            repository,
            clock,
        )
        self.processing = ProcessingCacheLifecycle(connections, storage, repository, clock=clock)

    def __call__(self, raw_scope):
        self.processing(raw_scope)
        scope = tuple(raw_scope.split("/"))
        context = current_request_context()
        if scope != (context.organization_id, context.project_id, context.region_code):
            raise PermissionError("cache retention requires the exact scope")
        cache = local_cache_for(self.storage)
        if cache is not None:
            for kind, action in ACTIONS.items():
                policy = select_policy(self.repository, scope[1], action)
                if policy is None:
                    continue
                for path in (cache.root / kind).glob("*/meta.json"):
                    try:
                        meta = json.loads(path.read_text())
                        owner = meta.get("scope") or meta["key"].split("/")[1:4]
                        if tuple(owner) != scope or cache.directory(meta["key"]) != path.parent:
                            continue
                        cutoff = self.clock().timestamp() - policy.minimum_age_days * 86400
                        if path.stat().st_mtime > cutoff:
                            continue
                        if not self.rebuildable(scope, kind, meta["key"]):
                            continue
                        with cache.lock(meta["key"]):
                            current = select_policy(self.repository, scope[1], action)
                            if (
                                current is None
                                or path.stat().st_mtime
                                > self.clock().timestamp() - current.minimum_age_days * 86400
                            ):
                                continue
                            last_use = path.stat().st_mtime
                            if meta.get("remote"):
                                self.storage.delete_object(meta["key"])
                            cache.remove_directory(path.parent)
                            self.audit(scope, current, meta["key"], meta["size"], last_use)
                    except FileNotFoundError:
                        continue
                    except Exception:
                        logger.exception("cache retained: lifecycle or rebuild guard failed")
        self.test_copies(scope)

    def available(self, packages, data):
        package = packages.files.open(data.identity, data.data_version_id)
        from hc_data_platform.episode_store.native_reference import ready

        if package.receipt.data != data or not ready(package, self.storage):
            return False
        return all(
            (head := self.storage.head(f"{data.package_prefix}/{entry.path}")) is not None
            and head.size == entry.size
            for entry in package.entries.values()
        )

    def rebuildable(self, scope, kind, key):
        with TemporaryDirectory(prefix="lifecycle-verify-") as temp:
            packages = EpisodePackageService(self.connections, self.storage, Path(temp))
            try:
                if kind == "episode-deliveries":
                    from hc_data_platform.episode_store.delivery_repository import public_job

                    row = packages.deliveries.repository.get_job(key.split("/")[2])
                    if row["status"] != "READY" or row["artifact_key"] != key:
                        return False
                    plan = public_job(row).plan
                    return self.annotations_available(packages, plan) and all(
                        self.available(packages, m.data) for m in plan.members
                    )
                if kind == "episode-previews":
                    with self.connections() as db:
                        rows = db.execute(
                            """SELECT receipt FROM episode_store.media_previews
                            WHERE organization_id=%s AND project_id=%s AND region_code=%s
                            AND data_version_id=%s AND status='READY' """,
                            (*scope, key.split("/")[2]),
                        ).fetchall()
                    return bool(rows) and all(
                        self.available(packages, DataVersionRef.model_validate(r[0]["data"]))
                        for r in rows
                    )
                return self.recording_rebuildable(scope, key)

            finally:
                packages.close()

    def audit(self, scope, policy, key, size, last_use):
        self.repository.append_audit(
            LifecycleAuditEvent(
                audit_id=str(uuid4()),
                project_id=scope[1],
                policy_id=policy.policy_id,
                actor_id="storage-inventory-worker",
                action="storage.cache.expired",
                request_id=str(uuid4()),
                details={
                    "cache_action": policy.action.value,
                    "object_key": key,
                    "reclaimed_bytes": size,
                    "policy_version": policy.version,
                    "minimum_age_days": policy.minimum_age_days,
                    "last_used_at": datetime.fromtimestamp(last_use, timezone.utc).isoformat(),
                    "source_available": True,
                },
            )
        )

    def test_copies(self, scope):
        policy = select_policy(
            self.repository, scope[1], LifecyclePolicyAction.CLEAN_OSS_TEST_COPIES
        )
        if policy is None:
            return
        from hc_data_platform.object_storage.immutable import verify_object

        with self.connections() as db:
            rows = db.execute(
                """SELECT object_key,original_key,size_bytes,sha256,created_at
                FROM storage.cache_test_copies WHERE organization_id=%s AND project_id=%s AND region_code=%s
                AND deleted_at IS NULL""",
                scope,
            ).fetchall()
        for key, original, size, checksum, created in rows:
            current = select_policy(
                self.repository, scope[1], LifecyclePolicyAction.CLEAN_OSS_TEST_COPIES
            )
            if (
                current is None
                or created.timestamp() > self.clock().timestamp() - current.minimum_age_days * 86400
            ):
                continue
            if not key.startswith("oss-smoke/") or not original.startswith("raw/"):
                continue
            verify_object(self.storage, original, size, checksum)
            if self.storage.head(key) is not None:
                verify_object(self.storage, key, size, checksum)
                current = select_policy(
                    self.repository, scope[1], LifecyclePolicyAction.CLEAN_OSS_TEST_COPIES
                )
                if (
                    current is None
                    or created.timestamp()
                    > self.clock().timestamp() - current.minimum_age_days * 86400
                ):
                    continue
                self.storage.delete_object(key)
            with self.connections() as db:
                db.execute(
                    """UPDATE storage.cache_test_copies SET deleted_at=now()
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s AND object_key=%s""",
                    (*scope, key),
                )
            self.audit(scope, current, key, size, created.timestamp())

    def recording_rebuildable(self, scope, key):
        from hc_data_platform.continuous_recordings.asset_models import (
            RecordingAsset,
            RecordingUpload,
        )
        from hc_data_platform.continuous_recordings.previews import preview_key

        with self.connections() as db:
            rows = db.execute(
                """SELECT p.source_document,u.upload_document,u.upload_id
                FROM ingest.mcap_processing_files p JOIN ingest.continuous_recordings r
                USING (organization_id,project_id,region_code,recording_id)
                JOIN ingest.recording_uploads u ON u.organization_id=p.organization_id
                AND u.project_id=p.project_id AND u.region_code=p.region_code AND u.upload_id=r.recording_upload_id
                WHERE p.organization_id=%s AND p.project_id=%s AND p.region_code=%s
                AND p.status='READY' AND p.config_reference IS NOT NULL""",
                scope,
            ).fetchall()
            for original, document, upload_id in rows:
                assets = [
                    RecordingAsset.model_validate(r[0])
                    for r in db.execute(
                        """SELECT asset_document FROM ingest.recording_upload_assets
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s AND upload_id=%s
                    AND status='COMMITTED'""",
                        (*scope, upload_id),
                    ).fetchall()
                ]
                upload = RecordingUpload.model_validate(document)
                if any(
                    key.removesuffix(".json")
                    == preview_key(upload, asset, assets, selected=selected)
                    for asset in assets
                    if asset.manifest.role == "RAW_VIDEO"
                    for selected in (True, False)
                ):
                    head = self.storage.head(original["object_key"])
                    return head is not None and head.size == original["size"]
        return self.upload_assets_available(scope, key)

    def annotations_available(self, packages, plan):
        selections = [getattr(member, "annotation", None) for member in plan.members]
        selections = [selected for selected in selections if selected is not None]
        if not selections:
            return True
        from hc_data_platform.annotation.postgres import PostgresAnnotationRepository
        from hc_data_platform.annotation.revision_files import (
            AnnotationRevisionFileService,
            PostgresRevisionFiles,
        )
        from hc_data_platform.annotation.selections import AnnotationSelections
        from hc_data_platform.annotation.service import AnnotationService
        from hc_data_platform.security import AuthContext

        context = current_request_context()
        org, project, region = context.organization_id, context.project_id, context.region_code
        auth = AuthContext(
            subject_id="cache-lifecycle-worker",
            service_identity=True,
            capabilities=frozenset({"annotation_task.read", "export.download"}),
            organization_ids=frozenset({org}),
            project_ids=frozenset({project}),
            region_codes=frozenset({region}),
            scope_pairs=frozenset({(project, region)}),
            organization_scope_triples=frozenset({(org, project, region)}),
        )
        files = AnnotationRevisionFileService(
            AnnotationService(PostgresAnnotationRepository(self.connections)),
            PostgresRevisionFiles(self.connections),
            self.storage,
        )
        service = AnnotationSelections(files, packages.files)
        for selected in selections:
            service.snapshot_for_plan(selected, auth)
        return True

    def upload_assets_available(self, scope, key):
        from hc_data_platform.continuous_recordings.asset_models import (
            RecordingAsset,
            RecordingUpload,
        )
        from hc_data_platform.continuous_recordings.previews import preview_key

        with self.connections() as db:
            uploads = db.execute(
                """SELECT upload_id,upload_document FROM ingest.recording_uploads
                WHERE organization_id=%s AND project_id=%s AND region_code=%s""",
                scope,
            ).fetchall()
            for ident, document in uploads:
                upload = RecordingUpload.model_validate(document)
                assets = [
                    RecordingAsset.model_validate(r[0])
                    for r in db.execute(
                        """SELECT asset_document FROM ingest.recording_upload_assets
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s AND upload_id=%s
                    AND status='COMMITTED'""",
                        (*scope, ident),
                    ).fetchall()
                ]
                if any(
                    key.removesuffix(".json")
                    == preview_key(upload, asset, assets, selected=selected)
                    for asset in assets
                    if asset.manifest.role == "RAW_VIDEO"
                    for selected in (True, False)
                ):
                    return bool(assets) and all(
                        (h := self.storage.head(a.object_key)) is not None
                        and h.size == a.manifest.size
                        for a in assets
                    )
        return False
