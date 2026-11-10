"""Live lifecycle policy lookup shared by cache collectors and local admission."""

from datetime import timedelta
from uuid import uuid4

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)

from .models import LifecycleAuditEvent, LifecyclePolicyAction, LifecyclePolicyState


def select_policy(repository, project_id, action):
    policies = [
        p
        for p in repository.all_policies(project_id=project_id)
        if p.action == action and p.state is LifecyclePolicyState.ENABLED
    ]
    return max(policies, key=lambda p: (p.minimum_age_days, p.priority, p.policy_id), default=None)


class SharedCachePolicy:
    """Unattributed shared staging uses the longest policy of every owning scope.

    Missing/paused policies or failed discovery retain the cache. No environment
    fallback TTL may bypass a project retention policy.
    """

    def __init__(self, settings, action):
        self.settings, self.action = settings, LifecyclePolicyAction(action)

    def policies(self):
        from hc_data_platform.core.dbapi import psycopg_connection_factory

        from .inventory_worker import local_inventory_scopes
        from .postgres import PostgresStorageRepository

        scopes = self.settings.storage_inventory_scopes or local_inventory_scopes(
            self.settings.postgres_dsn
        )
        result = []
        for raw in scopes:
            org, project, region = raw.split("/")
            token = bind_request_context(
                RequestContext(
                    organization_id=org,
                    project_id=project,
                    region_code=region,
                    service_identity=True,
                    subject_id="cache-lifecycle-worker",
                )
            )
            try:
                repo = PostgresStorageRepository(
                    psycopg_connection_factory(self.settings.postgres_dsn)
                )
                policy = select_policy(repo, project, self.action)
                if policy is None:
                    return []
                result.append((raw, policy))
            finally:
                reset_request_context(token)
        return result

    def __call__(self):
        policies = self.policies()
        return timedelta(days=max(p.minimum_age_days for _, p in policies)) if policies else None

    def audit(self, deleted):
        if not deleted:
            return
        from hc_data_platform.core.dbapi import psycopg_connection_factory

        from .postgres import PostgresStorageRepository

        for raw, policy in self.policies():
            org, project, region = raw.split("/")
            token = bind_request_context(
                RequestContext(
                    organization_id=org,
                    project_id=project,
                    region_code=region,
                    service_identity=True,
                    subject_id="cache-lifecycle-worker",
                )
            )
            try:
                PostgresStorageRepository(
                    psycopg_connection_factory(self.settings.postgres_dsn)
                ).append_audit(
                    LifecycleAuditEvent(
                        audit_id=str(uuid4()),
                        project_id=project,
                        policy_id=policy.policy_id,
                        actor_id="cache-lifecycle-worker",
                        action="storage.shared_cache.expired",
                        request_id=str(uuid4()),
                        details={
                            "cache_action": self.action.value,
                            "deleted_entries": deleted,
                            "policy_version": policy.version,
                            "minimum_age_days": policy.minimum_age_days,
                            "shared_scope": True,
                        },
                    )
                )
            finally:
                reset_request_context(token)
