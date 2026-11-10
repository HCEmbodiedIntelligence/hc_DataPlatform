"""Lifecycle-configured expiry of verified, reconstructible processing caches."""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from hc_data_platform.core.context import current_request_context
from hc_data_platform.episode_store.service import EpisodePackageService
from hc_data_platform.storage.models import (
    LifecycleAuditEvent,
    LifecyclePolicyAction,
    LifecyclePolicyState,
)

from .retention import retire_recording

logger = logging.getLogger(__name__)


def enabled_policy(repository, project_id):
    policies = [
        p
        for p in repository.all_policies(project_id=project_id)
        if p.action is LifecyclePolicyAction.CLEAN_PROCESSING_CACHE
        and p.state is LifecyclePolicyState.ENABLED
    ]
    return max(policies, key=lambda p: (p.minimum_age_days, p.priority, p.policy_id), default=None)


def expired(policy, completed_at, now):
    return (
        policy is not None
        and policy.state is LifecyclePolicyState.ENABLED
        and policy.action is LifecyclePolicyAction.CLEAN_PROCESSING_CACHE
        and completed_at is not None
        and completed_at <= now - timedelta(days=policy.minimum_age_days)
    )


class ProcessingCacheLifecycle:
    def __init__(
        self, connections, storage, repository, *, clock=lambda: datetime.now(timezone.utc)
    ):
        self.connections, self.storage, self.repository, self.clock = (
            connections,
            storage,
            repository,
            clock,
        )
        self._cursor = {}

    def authorize(self, scope, recording_id):
        context = current_request_context()
        if tuple(scope) != (context.organization_id, context.project_id, context.region_code):
            raise PermissionError("processing cache policy requires the exact scope")
        policy = enabled_policy(self.repository, scope[1])
        if policy is None:
            return None
        with self.connections() as db:
            row = db.execute(
                """SELECT max(e.updated_at)
                FROM ingest.continuous_recordings r
                JOIN ingest.recording_episode_processing e
                USING (organization_id,project_id,region_code,recording_id)
                WHERE r.organization_id=%s AND r.project_id=%s AND r.region_code=%s
                AND r.recording_id=%s AND r.status='SLICED'
                AND r.current_revision=r.finalized_revision
                GROUP BY r.recording_id
                HAVING bool_and(e.status='READY' AND e.data_ref IS NOT NULL)""",
                (*scope, recording_id),
            ).fetchone()
        if row is None or not expired(policy, row[0], self.clock()):
            return None
        return {
            "policy_id": policy.policy_id,
            "policy_version": policy.version,
            "minimum_age_days": policy.minimum_age_days,
            "completed_at": row[0].isoformat(),
        }

    def __call__(self, raw_scope):
        scope = tuple(raw_scope.split("/"))
        if enabled_policy(self.repository, scope[1]) is None:
            return
        with self.connections() as db:
            rows = db.execute(
                """SELECT r.recording_id FROM ingest.continuous_recordings r
                WHERE r.organization_id=%s AND r.project_id=%s AND r.region_code=%s
                AND r.status='SLICED' ORDER BY r.recording_id""",
                scope,
            ).fetchall()
        ids = [row[0] for row in rows]
        self.retire_failed(scope)
        if not ids:
            return
        start = self._cursor.get(raw_scope, 0) % len(ids)
        for offset in range(min(2, len(ids))):
            recording_id = ids[(start + offset) % len(ids)]
            try:
                if self.authorize(scope, recording_id) is None:
                    continue
                with TemporaryDirectory(prefix="cache-lifecycle-") as temporary:
                    packages = EpisodePackageService(
                        self.connections, self.storage, Path(temporary)
                    )
                    try:
                        retire_recording(
                            self.connections,
                            self.storage,
                            packages,
                            scope,
                            recording_id,
                            authorize=self.authorize,
                            on_deleted=lambda item: self.audit(scope, item),
                        )
                    finally:
                        packages.close()
            except Exception:
                logger.exception(
                    "processing cache retained: verification or policy guard did not pass",
                    extra={"recording_id": recording_id},
                )
        self._cursor[raw_scope] = (start + 2) % len(ids)

    def audit(self, scope, item):
        policy = item["lifecycle"]
        self.repository.append_audit(
            LifecycleAuditEvent(
                audit_id=str(uuid4()),
                project_id=scope[1],
                policy_id=policy["policy_id"],
                actor_id="storage-inventory-worker",
                action=item.get("audit_action", "storage.processing_cache.expired"),
                request_id=str(uuid4()),
                details={
                    "recording_id": item["recording_id"],
                    "object_key": item["key"],
                    "reclaimed_bytes": item["size"],
                    "policy_version": policy["policy_version"],
                    "minimum_age_days": policy["minimum_age_days"],
                    "rebuild_verified": item.get("rebuild_verified", True),
                    "raw_verified": True,
                },
            )
        )

    def retire_failed(self, scope):
        """Unregistered failures expire from their last failure time, preserving Raw."""
        from types import SimpleNamespace

        from hc_data_platform.lerobot_imports.mcap_retention import purge_failed
        from hc_data_platform.object_storage.immutable import verify_object

        with self.connections() as db:
            rows = db.execute(
                """SELECT processing_id FROM ingest.mcap_processing_files
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                AND status='FAILED' AND recording_id IS NULL ORDER BY updated_at""",
                scope,
            ).fetchall()
        for (ident,) in rows:
            with self.connections() as lock:
                token = "mcap:" + ident
                if not lock.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (token,)
                ).fetchone()[0]:
                    continue
                try:

                    def guard(ident=ident):
                        policy = enabled_policy(self.repository, scope[1])
                        with self.connections() as db:
                            row = db.execute(
                                """SELECT updated_at, source_document
                                FROM ingest.mcap_processing_files
                                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                                AND processing_id=%s AND status='FAILED'
                                AND recording_id IS NULL""",
                                (*scope, ident),
                            ).fetchone()
                        if row is None or not expired(policy, row[0], self.clock()):
                            raise ValueError(
                                "failed cache is inside retention or its lifecycle policy is paused"
                            )
                        return policy, row[1]

                    policy, original = guard()
                    prefix = "/".join(("derived-mcap", *scope, ident)) + "/"
                    if not any(self.storage.list_object_keys(prefix)):
                        continue
                    verify_object(
                        self.storage, original["object_key"], original["size"], original["sha256"]
                    )
                    reclaimed = purge_failed(
                        SimpleNamespace(connections=self.connections, storage=self.storage),
                        scope,
                        ident,
                        checkpoint=guard,
                    )
                    if reclaimed:
                        self.audit(
                            scope,
                            {
                                "recording_id": None,
                                "key": prefix,
                                "size": reclaimed,
                                "rebuild_verified": False,
                                "audit_action": "storage.failed_processing_cache.expired",
                                "lifecycle": {
                                    "policy_id": policy.policy_id,
                                    "policy_version": policy.version,
                                    "minimum_age_days": policy.minimum_age_days,
                                },
                            },
                        )
                        return
                except Exception:
                    logger.info("failed processing cache retained", extra={"processing_id": ident})
                finally:
                    lock.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (token,))
