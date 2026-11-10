"""Compact inventory history under persisted, scope-specific lifecycle policies."""

from uuid import uuid4

from hc_data_platform.core.context import current_request_context

from .models import CapacitySnapshot, LifecycleAuditEvent


def lock_inventory(cursor, project_id):
    context = current_request_context()
    if context.project_id != project_id:
        raise PermissionError("inventory retention requires the exact project scope")
    cursor.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"inventory:{context.organization_id}:{project_id}",),
    )


class InventoryRetention:
    def __init__(self, connections, *, batch_size=100):
        if not 1 <= batch_size <= 500:
            raise ValueError("inventory retention batch must be between 1 and 500")
        self.connections, self.batch_size = connections, batch_size

    def __call__(self, raw_scope):
        context = current_request_context()
        if tuple(raw_scope.split("/")) != (
            context.organization_id,
            context.project_id,
            context.region_code,
        ):
            raise PermissionError("inventory retention requires the exact scope")
        return self.run(project_id=context.project_id)

    def run(self, *, project_id):
        from .postgres import _FACT_COLUMNS, _POLICY_COLUMNS, PostgresStorageRepository, _rows
        from .service import StorageGovernanceService

        result = {"deleted_entries": 0, "summarized_snapshots": 0, "deleted_summaries": 0}
        for action in ("CLEAN_INVENTORY_DETAILS", "CLEAN_INVENTORY_HISTORY"):
            connection = self.connections()
            try:
                cursor = connection.cursor()
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                lock_inventory(cursor, project_id)
                # The lock makes a pause/edit wait for this bounded batch to finish.
                cursor.execute(
                    f"SELECT {_POLICY_COLUMNS} FROM storage.lifecycle_policies "
                    "WHERE project_id=%s AND action=%s AND state='ENABLED' FOR SHARE",
                    (project_id, action),
                )
                policies = [
                    PostgresStorageRepository._policy(row)
                    for row in _rows(cursor, cursor.fetchall())
                ]
                policy = max(
                    policies,
                    key=lambda p: (p.minimum_age_days, p.priority, p.policy_id),
                    default=None,
                )
                if policy is None:
                    connection.commit()
                    continue
                details = action == "CLEAN_INVENTORY_DETAILS"
                marker = "IS NULL" if details else "IS NOT NULL"
                cursor.execute(
                    f"""SELECT snapshot_id, summary, physical_total_bytes,
                    physical_instance_count, candidate_business_total_bytes, duplicate_inventory_rows_ignored
                    FROM storage.inventory_snapshots
                    WHERE project_id=%s AND sealed AND details_expired_at {marker}
                      AND observed_at < statement_timestamp() - %s * interval '1 day'
                      AND snapshot_id <> (SELECT snapshot_id FROM storage.inventory_snapshots
                        WHERE project_id=%s ORDER BY observed_at DESC, snapshot_id DESC LIMIT 1)
                    ORDER BY observed_at, snapshot_id LIMIT %s FOR UPDATE""",
                    (project_id, policy.minimum_age_days, project_id, self.batch_size),
                )
                snapshots = _rows(cursor, cursor.fetchall())
                deleted = 0
                for row in snapshots:
                    key = (project_id, row["snapshot_id"])
                    if details:
                        if row["summary"] is None:
                            cursor.execute(
                                f"SELECT {_FACT_COLUMNS} FROM storage.inventory_facts "
                                "WHERE project_id=%s AND snapshot_id=%s",
                                key,
                            )
                            facts = tuple(
                                PostgresStorageRepository._fact(f)
                                for f in _rows(cursor, cursor.fetchall())
                            )
                            summary = StorageGovernanceService._reconcile(
                                project_id=project_id,
                                snapshot_id=row["snapshot_id"],
                                facts=facts,
                                duplicate_rows_ignored=int(row["duplicate_inventory_rows_ignored"]),
                            )
                            if (
                                int(summary.physical_total_bytes) != row["physical_total_bytes"]
                                or summary.physical_instance_count != row["physical_instance_count"]
                                or int(summary.candidate_business_total_bytes)
                                != row["candidate_business_total_bytes"]
                            ):
                                raise ValueError(
                                    "inventory summary does not reconcile; retain facts"
                                )
                            cursor.execute(
                                "UPDATE storage.inventory_snapshots SET summary=%s::jsonb "
                                "WHERE project_id=%s AND snapshot_id=%s",
                                (summary.model_dump_json(), *key),
                            )
                        else:
                            CapacitySnapshot.model_validate(row["summary"])
                        cursor.execute(
                            "UPDATE storage.inventory_snapshots SET details_expired_at=statement_timestamp() "
                            "WHERE project_id=%s AND snapshot_id=%s",
                            key,
                        )
                        cursor.execute(
                            "DELETE FROM storage.inventory_facts WHERE project_id=%s AND snapshot_id=%s",
                            key,
                        )
                        deleted += cursor.rowcount
                    else:
                        cursor.execute(
                            "DELETE FROM storage.inventory_snapshots WHERE project_id=%s AND snapshot_id=%s",
                            key,
                        )
                        deleted += cursor.rowcount
                if snapshots:
                    PostgresStorageRepository._insert_audit(
                        cursor,
                        LifecycleAuditEvent(
                            audit_id=str(uuid4()),
                            project_id=project_id,
                            policy_id=policy.policy_id,
                            actor_id="inventory-lifecycle-worker",
                            action="storage.inventory.expired",
                            request_id=str(uuid4()),
                            details={
                                "cache_action": action,
                                "deleted_entries": deleted,
                                "summarized_snapshots": len(snapshots) if details else 0,
                                "deleted_summaries": 0 if details else deleted,
                                "minimum_age_days": policy.minimum_age_days,
                                "policy_version": policy.version,
                                "oldest_snapshot_id": snapshots[0]["snapshot_id"],
                                "newest_snapshot_id": snapshots[-1]["snapshot_id"],
                            },
                        ),
                    )
                connection.commit()
                result["deleted_entries" if details else "deleted_summaries"] += deleted
                if details:
                    result["summarized_snapshots"] += len(snapshots)
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        return result
