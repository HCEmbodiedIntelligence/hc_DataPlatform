"""Concrete read-only inspectors for RST3-03 restore reconciliation."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

from hc_data_platform.backup.contracts import canonical_json_bytes
from hc_data_platform.backup.objects import (
    AgeDecryptedReader,
    ObjectBackupArtifact,
    ObjectBackupVerifier,
    ObjectInventoryRecord,
    S3ObjectRestoreAdapter,
)
from hc_data_platform.backup.postgresql import (
    DatabaseVerificationEvidence,
    PostgresEndpoint,
    collect_database_evidence,
)
from hc_data_platform.backup.restore import RestorePlanV1
from hc_data_platform.backup.restore_execution import LoadedRestoreCheckpoint
from hc_data_platform.backup.restore_reconciliation import (
    ReconciliationCheckName,
    RestoreReconciliationCheckV1,
    RestoreReconciliationInspector,
    reconciliation_check,
)
from hc_data_platform.backup.temporal import (
    TemporalAdminVisibilityPort,
    TemporalPolicyDocument,
)

ConnectionFactory = Callable[[PostgresEndpoint], Any]


class RestoreReadOnlyRuntimePort(Protocol):
    def assert_read_only(
        self,
        plan: RestorePlanV1,
        *,
        execution_owner_id: str,
        fencing_token: int,
    ) -> str: ...


@dataclass(frozen=True)
class WorkflowDatabaseFacts:
    running_workflow_runs: tuple[tuple[str, str | None], ...]
    dispatched_workflow_ids: tuple[str, ...]
    unresolved_reconciliation_count: int
    pending_lance_reconciliation_count: int
    total_fact_count: int
    evidence_sha256: str


@dataclass(frozen=True)
class PostgresReconciliationResult:
    checks: Mapping[ReconciliationCheckName, RestoreReconciliationCheckV1]
    workflow: WorkflowDatabaseFacts


@dataclass(frozen=True)
class _ObjectReference:
    domain: str
    value: str
    prefix: bool = False
    expected_size: int | None = None
    expected_sha256: str | None = None


class StaticReconciliationInspector:
    """Expose a precomputed fact from one shared PostgreSQL snapshot."""

    def __init__(self, result: RestoreReconciliationCheckV1) -> None:
        self._result = result

    def inspect(
        self,
        plan: RestorePlanV1,
        checkpoint: LoadedRestoreCheckpoint,
    ) -> RestoreReconciliationCheckV1:
        del plan, checkpoint
        return self._result


class RestoredObjectInventoryInspector:
    """Verify every target object version against authenticated inventory bytes."""

    def __init__(
        self,
        adapter: S3ObjectRestoreAdapter,
        artifact: ObjectBackupArtifact,
        *,
        source_prefix: str,
        decryptor: AgeDecryptedReader | None = None,
    ) -> None:
        self._adapter = adapter
        self._artifact = artifact
        self._source_prefix = source_prefix
        self._decryptor = decryptor

    def inspect(
        self,
        plan: RestorePlanV1,
        checkpoint: LoadedRestoreCheckpoint,
    ) -> RestoreReconciliationCheckV1:
        del checkpoint
        if plan.object_restore_mode == "reuse_external":
            report = self._adapter.verify_external(
                self._artifact,
                source_prefix=self._source_prefix,
                decryptor=self._decryptor,
            )
        else:
            report = self._adapter.verify_restored(
                self._artifact,
                source_prefix=self._source_prefix,
                decryptor=self._decryptor,
            )
        target_matches = (
            report.target_bucket_reference == plan.target_object_store_bucket_reference
            and report.target_prefix == plan.target_object_store_prefix
        )
        return reconciliation_check(
            "object_inventory",
            total_count=report.object_count + 1,
            issue_count=0 if target_matches else 1,
            issue_codes=(() if target_matches else ("OBJECT_TARGET_IDENTITY_MISMATCH",)),
            evidence=report.model_dump(mode="json"),
        )


def authenticated_object_records(
    artifact: ObjectBackupArtifact,
    *,
    source_bucket_reference: str,
    source_prefix: str,
    decryptor: AgeDecryptedReader | None = None,
) -> tuple[ObjectInventoryRecord, ...]:
    """Parse the authenticated inventory once for database-reference matching."""

    return ObjectBackupVerifier().inventory_records(
        artifact,
        decryptor=decryptor,
        expected_bucket_reference=source_bucket_reference,
        expected_prefix=source_prefix,
    )


class TemporalWorkflowInspector:
    def __init__(
        self,
        provider: TemporalAdminVisibilityPort,
        policy: TemporalPolicyDocument,
        database: WorkflowDatabaseFacts,
    ) -> None:
        self._provider = provider
        self._policy = policy
        self._database = database

    def inspect(
        self,
        plan: RestorePlanV1,
        checkpoint: LoadedRestoreCheckpoint,
    ) -> RestoreReconciliationCheckV1:
        del plan, checkpoint
        live = asyncio.run(self._provider.capture_inventory())
        schedule_hash = _sha_json([item.model_dump(mode="json") for item in live.schedules])
        workflow_hash = _sha_json([item.model_dump(mode="json") for item in live.open_workflows])
        issues: dict[str, int] = {}
        if (
            live.namespace != self._policy.namespace
            or live.namespace_identity_sha256 != self._policy.namespace_identity_sha256
            or live.cluster_identity_sha256 != self._policy.cluster_identity_sha256
            or live.service_version != self._policy.service_version
        ):
            issues["TEMPORAL_IDENTITY_MISMATCH"] = 1
        if schedule_hash != self._policy.schedule_inventory_sha256:
            issues["TEMPORAL_SCHEDULE_INVENTORY_MISMATCH"] = 1
        if workflow_hash != self._policy.open_workflow_inventory_sha256:
            issues["TEMPORAL_WORKFLOW_INVENTORY_MISMATCH"] = 1

        live_runs = {(item.workflow_id, item.run_id) for item in live.open_workflows}
        live_ids = {item.workflow_id for item in live.open_workflows}
        database_mismatches = sum(
            1
            for workflow_id, run_id in self._database.running_workflow_runs
            if (
                (workflow_id, run_id) not in live_runs
                if run_id is not None
                else workflow_id not in live_ids
            )
        )
        database_mismatches += sum(
            1
            for workflow_id in self._database.dispatched_workflow_ids
            if workflow_id not in live_ids
        )
        if database_mismatches:
            issues["WORKFLOW_DATABASE_TEMPORAL_MISMATCH"] = database_mismatches
        unresolved = (
            self._database.unresolved_reconciliation_count
            + self._database.pending_lance_reconciliation_count
        )
        if unresolved:
            issues["WORKFLOW_RECONCILIATION_PENDING"] = unresolved
        evidence = {
            "namespace_identity_sha256": live.namespace_identity_sha256,
            "cluster_identity_sha256": live.cluster_identity_sha256,
            "service_version": live.service_version,
            "schedule_inventory_sha256": schedule_hash,
            "open_workflow_inventory_sha256": workflow_hash,
            "database_workflow_evidence_sha256": self._database.evidence_sha256,
            "schedule_count": len(live.schedules),
            "open_workflow_count": len(live.open_workflows),
        }
        return reconciliation_check(
            "workflow_temporal",
            total_count=(
                len(live.schedules) + len(live.open_workflows) + self._database.total_fact_count + 4
            ),
            issue_count=sum(issues.values()),
            issue_codes=tuple(issues),
            evidence=evidence,
        )


class ReadOnlyRuntimeInspector:
    def __init__(self, runtime: RestoreReadOnlyRuntimePort) -> None:
        self._runtime = runtime

    def inspect(
        self,
        plan: RestorePlanV1,
        checkpoint: LoadedRestoreCheckpoint,
    ) -> RestoreReconciliationCheckV1:
        value = checkpoint.checkpoint
        evidence_sha256 = self._runtime.assert_read_only(
            plan,
            execution_owner_id=value.execution_owner_id,
            fencing_token=value.fencing_token,
        )
        return RestoreReconciliationCheckV1(
            name="read_only_runtime",
            status="PASS",
            total_count=1,
            checked_count=1,
            issue_count=0,
            evidence_sha256=evidence_sha256,
        )


def reconciliation_inspector_map(
    *,
    postgres: PostgresReconciliationResult,
    objects: RestoreReconciliationInspector,
    temporal: RestoreReconciliationInspector,
    runtime: RestoreReconciliationInspector,
) -> Mapping[ReconciliationCheckName, RestoreReconciliationInspector]:
    return {
        "database_content": StaticReconciliationInspector(postgres.checks["database_content"]),
        "object_inventory": objects,
        "database_object_references": StaticReconciliationInspector(
            postgres.checks["database_object_references"]
        ),
        "audit_integrity": StaticReconciliationInspector(postgres.checks["audit_integrity"]),
        "outbox": StaticReconciliationInspector(postgres.checks["outbox"]),
        "workflow_temporal": temporal,
        "permissions": StaticReconciliationInspector(postgres.checks["permissions"]),
        "read_only_runtime": runtime,
    }


def inspect_postgresql_restore(
    plan: RestorePlanV1,
    checkpoint: LoadedRestoreCheckpoint,
    *,
    endpoint: PostgresEndpoint,
    source_evidence: DatabaseVerificationEvidence,
    source_server_major: int,
    object_records: Sequence[ObjectInventoryRecord],
    source_object_prefix: str,
    target_object_prefix: str,
    connection_factory: ConnectionFactory | None = None,
) -> PostgresReconciliationResult:
    """Collect every database-backed gate in one read-only repeatable snapshot."""

    factory = connection_factory or _connect_postgres
    connection = factory(endpoint)
    try:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY, DEFERRABLE")
        identity = connection.execute(
            """
            SELECT current_database(),
                   current_setting('server_version_num')::integer / 10000,
                   current_setting('transaction_read_only')::boolean
            """
        ).fetchone()
        if identity is None or not bool(identity[2]):
            raise RuntimeError("PostgreSQL reconciliation is not read-only")
        current = collect_database_evidence(connection)
        database_check = _database_content_check(
            plan,
            checkpoint,
            source_evidence=source_evidence,
            current=current,
            database_name=str(identity[0]),
            server_major=int(identity[1]),
            source_server_major=source_server_major,
        )
        reference_check = _database_object_reference_check(
            connection,
            object_records=object_records,
            source_prefix=source_object_prefix,
            target_prefix=target_object_prefix,
        )
        audit_check = _audit_integrity_check(connection)
        outbox_check = _outbox_check(connection)
        workflow = _workflow_database_facts(connection)
        permissions_check = _permissions_check(connection)
        connection.rollback()
        return PostgresReconciliationResult(
            checks={
                "database_content": database_check,
                "database_object_references": reference_check,
                "audit_integrity": audit_check,
                "outbox": outbox_check,
                "permissions": permissions_check,
            },
            workflow=workflow,
        )
    finally:
        connection.close()


def failed_postgresql_reconciliation(error: Exception) -> PostgresReconciliationResult:
    """Represent an unavailable PostgreSQL snapshot without leaking its message."""

    checks: dict[ReconciliationCheckName, RestoreReconciliationCheckV1] = {}
    for name in (
        "database_content",
        "database_object_references",
        "audit_integrity",
        "outbox",
        "permissions",
    ):
        checks[name] = reconciliation_check(
            name,
            total_count=1,
            issue_count=1,
            issue_codes=("RESTORE_RECONCILIATION_INSPECTOR_ERROR",),
            evidence={"check": name, "exception_type": type(error).__name__},
        )
    workflow = WorkflowDatabaseFacts(
        running_workflow_runs=(),
        dispatched_workflow_ids=(),
        unresolved_reconciliation_count=1,
        pending_lance_reconciliation_count=0,
        total_fact_count=1,
        evidence_sha256=_sha_json(
            {"check": "workflow_temporal", "exception_type": type(error).__name__}
        ),
    )
    return PostgresReconciliationResult(checks=checks, workflow=workflow)


def _connect_postgres(endpoint: PostgresEndpoint) -> Any:
    import psycopg

    return psycopg.connect(
        host=endpoint.host,
        port=endpoint.port,
        dbname=endpoint.database,
        user=endpoint.username,
        password=endpoint.password.get_secret_value(),
        sslmode=endpoint.sslmode,
        connect_timeout=10,
    )


def _database_content_check(
    plan: RestorePlanV1,
    checkpoint: LoadedRestoreCheckpoint,
    *,
    source_evidence: DatabaseVerificationEvidence,
    current: DatabaseVerificationEvidence,
    database_name: str,
    server_major: int,
    source_server_major: int,
) -> RestoreReconciliationCheckV1:
    source_tables = {(item.schema_name, item.table_name): item for item in source_evidence.tables}
    current_tables = {(item.schema_name, item.table_name): item for item in current.tables}
    issues: dict[str, int] = {}
    if database_name != plan.target_postgresql_database:
        issues["DATABASE_TARGET_IDENTITY_MISMATCH"] = 1
    if server_major != source_server_major:
        issues["DATABASE_SERVER_MAJOR_MISMATCH"] = 1
    if (
        current.migration_count != source_evidence.migration_count
        or current.migration_sha256 != source_evidence.migration_sha256
    ):
        issues["DATABASE_MIGRATION_MISMATCH"] = 1
    if (
        current.schema_object_count != source_evidence.schema_object_count
        or current.schema_sha256 != source_evidence.schema_sha256
    ):
        issues["DATABASE_SCHEMA_MISMATCH"] = 1
    if (
        current.constraint_count != source_evidence.constraint_count
        or current.constraint_sha256 != source_evidence.constraint_sha256
    ):
        issues["DATABASE_CONSTRAINT_MISMATCH"] = 1
    if set(current_tables) != set(source_tables):
        issues["DATABASE_TABLE_SET_MISMATCH"] = len(
            set(current_tables).symmetric_difference(source_tables)
        )
    business_mismatches = sum(
        1
        for identity, source in source_tables.items()
        if identity[0] != "platform" and current_tables.get(identity) != source
    )
    if business_mismatches:
        issues["DATABASE_BUSINESS_CONTENT_MISMATCH"] = business_mismatches

    postgresql_receipt = next(
        (
            receipt
            for receipt in checkpoint.checkpoint.receipts
            if receipt.step == "postgresql_restored"
        ),
        None,
    )
    if postgresql_receipt is None:
        issues["DATABASE_RESTORE_RECEIPT_MISSING"] = 1
    platform_table_count = sum(1 for identity in source_tables if identity[0] == "platform")
    evidence = {
        "target_database_sha256": hashlib.sha256(database_name.encode()).hexdigest(),
        "server_major": server_major,
        "migration_count": current.migration_count,
        "migration_sha256": current.migration_sha256,
        "schema_object_count": current.schema_object_count,
        "schema_sha256": current.schema_sha256,
        "constraint_count": current.constraint_count,
        "constraint_sha256": current.constraint_sha256,
        "sequence_count": current.sequence_count,
        "sequence_sha256": current.sequence_sha256,
        "business_table_count": len(source_tables) - platform_table_count,
        "target_local_platform_table_count": platform_table_count,
        "business_content_sha256": _sha_json(
            [
                item.model_dump(mode="json")
                for item in current.tables
                if item.schema_name != "platform"
            ]
        ),
        "postgresql_restore_receipt_sha256": (
            None if postgresql_receipt is None else postgresql_receipt.evidence_sha256
        ),
    }
    return reconciliation_check(
        "database_content",
        total_count=len(source_tables) - platform_table_count + 6,
        issue_count=sum(issues.values()),
        issue_codes=tuple(issues),
        evidence=evidence,
    )


def _database_object_reference_check(
    connection: Any,
    *,
    object_records: Sequence[ObjectInventoryRecord],
    source_prefix: str,
    target_prefix: str,
) -> RestoreReconciliationCheckV1:
    references = _database_object_references(connection)
    issues: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    for reference in references:
        domain_counts[reference.domain] = domain_counts.get(reference.domain, 0) + 1
        record = _match_object_reference(
            reference,
            records=object_records,
            source_prefix=source_prefix,
            target_prefix=target_prefix,
        )
        if record is None:
            issues["DATABASE_OBJECT_REFERENCE_MISSING"] = (
                issues.get("DATABASE_OBJECT_REFERENCE_MISSING", 0) + 1
            )
            continue
        if (
            reference.expected_size is not None and reference.expected_size != record.size_bytes
        ) or (reference.expected_sha256 is not None and reference.expected_sha256 != record.sha256):
            issues["DATABASE_OBJECT_REFERENCE_CONTENT_MISMATCH"] = (
                issues.get("DATABASE_OBJECT_REFERENCE_CONTENT_MISMATCH", 0) + 1
            )
    evidence = {
        "inventory_record_count": len(object_records),
        "inventory_content_sha256": _sha_json(
            [
                {
                    "ordinal": item.ordinal,
                    "object_key_sha256": hashlib.sha256(item.object_key.encode()).hexdigest(),
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                }
                for item in object_records
            ]
        ),
        "reference_count": len(references),
        "domain_counts": dict(sorted(domain_counts.items())),
    }
    return reconciliation_check(
        "database_object_references",
        total_count=len(references),
        issue_count=sum(issues.values()),
        issue_codes=tuple(issues),
        evidence=evidence,
    )


def _database_object_references(connection: Any) -> tuple[_ObjectReference, ...]:
    statements = (
        """
        SELECT 'ingest'::text, object_key, false, file_size, source_sha256
          FROM ingest.rollout_objects
        UNION ALL
        SELECT 'ingest', manifest_key, false, NULL::bigint, NULL::text
          FROM ingest.rollout_objects
        UNION ALL
        SELECT 'ingest', object_key, false, actual_size, actual_sha256
          FROM ingest.upload_objects
         WHERE status = 'COMMITTED'
        """,
        """
        SELECT 'annotation'::text, object_key, false, size_bytes, content_sha256
          FROM annotation.frame_selection_manifests
        """,
        """
        SELECT 'storage'::text, object_key, false,
               physical_bytes::bigint, checksum_sha256
          FROM storage.managed_objects
        """,
        """
        SELECT 'registry'::text, object_key, false, size_bytes, sha256
          FROM registry.robot_model_assets
        UNION ALL
        SELECT 'registry', object_key, false, expected_size, expected_sha256
          FROM registry.robot_model_asset_upload_files
         WHERE status = 'COMPLETED'
        """,
        """
        SELECT 'publishing'::text, artifact_uri, false, size_bytes, content_sha256
          FROM publishing.publication_assets
        UNION ALL
        SELECT 'publishing', artifact_uri, false, NULL::bigint, artifact_content_sha256
          FROM publishing.published_exports
        """,
        """
        SELECT 'aligned_media'::text, member.value->>'key', false,
               (member.value->>'size')::bigint, member.value->>'sha256'
          FROM aligned_media.artifacts AS artifact
          CROSS JOIN LATERAL jsonb_array_elements(artifact.object_manifest) AS member(value)
         WHERE artifact.status = 'READY' AND artifact.dataset_committed_at IS NOT NULL
        """,
        """
        SELECT 'lance'::text, dataset_uri, true, NULL::bigint, NULL::text
          FROM lance_datasets
         WHERE current_version > 0
        UNION ALL
        SELECT 'lance', dataset_uri, true, NULL::bigint, NULL::text
          FROM lance_dataset_versions
        """,
        """
        SELECT 'verification'::text, object_key, false, NULL::bigint, NULL::text
          FROM raw_verification_reports
        """,
    )
    found: list[_ObjectReference] = []
    for statement in statements:
        for row in connection.execute(statement).fetchall():
            if row[1] is None:
                continue
            found.append(
                _ObjectReference(
                    domain=str(row[0]),
                    value=str(row[1]),
                    prefix=bool(row[2]),
                    expected_size=None if row[3] is None else int(row[3]),
                    expected_sha256=None if row[4] is None else str(row[4]),
                )
            )
    return tuple(found)


def _match_object_reference(
    reference: _ObjectReference,
    *,
    records: Sequence[ObjectInventoryRecord],
    source_prefix: str,
    target_prefix: str,
) -> ObjectInventoryRecord | None:
    raw = reference.value.strip()
    parsed = urlparse(raw)
    key = unquote(parsed.path.lstrip("/")) if parsed.scheme else raw.lstrip("/")
    normalized_source = source_prefix.strip("/")
    normalized_target = target_prefix.strip("/")
    candidates = {key}
    if normalized_source and not key.startswith(normalized_source + "/"):
        candidates.add(f"{normalized_source}/{key}")
    if normalized_target and key.startswith(normalized_target + "/"):
        relative = key[len(normalized_target) + 1 :]
        candidates.add(f"{normalized_source}/{relative}" if normalized_source else relative)

    if reference.prefix:
        prefixes = tuple(candidate.rstrip("/") + "/" for candidate in candidates)
        return next(
            (record for record in records if record.object_key.startswith(prefixes)),
            None,
        )
    exact = next((record for record in records if record.object_key in candidates), None)
    if exact is not None:
        return exact
    suffix = "/" + key
    matches = [record for record in records if record.object_key.endswith(suffix)]
    return matches[0] if len(matches) == 1 else None


def _audit_integrity_check(connection: Any) -> RestoreReconciliationCheckV1:
    row = connection.execute(
        """
        WITH visible AS (
            SELECT audit.audit_id,
                   audit.organization_id,
                   audit.project_id,
                   audit.region_code,
                   audit.actor_id,
                   audit.action,
                   audit.resource_type,
                   audit.resource_id,
                   audit.request_id,
                   audit.before_hash,
                   audit.after_hash,
                   audit.details,
                   audit.occurred_at,
                   entry.sequence_no,
                   entry.previous_event_hash,
                   entry.event_hash,
                   row_number() OVER (
                       PARTITION BY audit.organization_id, audit.project_id,
                                    COALESCE(audit.region_code, '')
                       ORDER BY entry.sequence_no NULLS LAST
                   ) AS expected_sequence_no,
                   lag(entry.event_hash) OVER (
                       PARTITION BY audit.organization_id, audit.project_id,
                                    COALESCE(audit.region_code, '')
                       ORDER BY entry.sequence_no NULLS LAST
                   ) AS expected_previous_event_hash
              FROM core.audit_events AS audit
              LEFT JOIN core.audit_integrity_entries AS entry
                ON entry.audit_id = audit.audit_id
               AND entry.organization_id = audit.organization_id
               AND entry.project_id = audit.project_id
               AND entry.region_code = COALESCE(audit.region_code, '')
        ), event_summary AS (
            SELECT count(*)::bigint AS event_count,
                   count(*) FILTER (WHERE
                       sequence_no IS NULL
                       OR sequence_no <> expected_sequence_no
                       OR previous_event_hash IS DISTINCT FROM expected_previous_event_hash
                       OR event_hash IS DISTINCT FROM core.audit_integrity_event_hash(
                           previous_event_hash,
                           audit_id,
                           organization_id,
                           project_id,
                           region_code,
                           actor_id,
                           action,
                           resource_type,
                           resource_id,
                           request_id,
                           before_hash,
                           after_hash,
                           details,
                           occurred_at
                       )
                   )::bigint AS invalid_event_count
              FROM visible
        ), tails AS (
            SELECT DISTINCT ON (organization_id, project_id, COALESCE(region_code, ''))
                   organization_id, project_id, COALESCE(region_code, '') AS region_code,
                   expected_sequence_no AS last_sequence, event_hash AS last_event_hash
              FROM visible
             ORDER BY organization_id, project_id, COALESCE(region_code, ''),
                      expected_sequence_no DESC
        ), head_summary AS (
            SELECT count(*)::bigint AS head_fact_count,
                   count(*) FILTER (WHERE
                       tail.organization_id IS NULL
                       OR head.organization_id IS NULL
                       OR head.last_sequence IS DISTINCT FROM tail.last_sequence
                       OR head.last_event_hash IS DISTINCT FROM tail.last_event_hash
                   )::bigint AS invalid_head_count
              FROM tails AS tail
              FULL OUTER JOIN core.audit_integrity_heads AS head
                ON head.organization_id = tail.organization_id
               AND head.project_id = tail.project_id
               AND head.region_code = tail.region_code
        )
        SELECT event_summary.event_count,
               event_summary.invalid_event_count,
               head_summary.head_fact_count,
               head_summary.invalid_head_count
          FROM event_summary CROSS JOIN head_summary
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("audit integrity query returned no result")
    event_count, invalid_events, head_count, invalid_heads = (int(value) for value in row)
    issues: dict[str, int] = {}
    if invalid_events:
        issues["AUDIT_EVENT_CHAIN_INVALID"] = invalid_events
    if invalid_heads:
        issues["AUDIT_CHAIN_HEAD_INVALID"] = invalid_heads
    return reconciliation_check(
        "audit_integrity",
        total_count=event_count + head_count,
        issue_count=sum(issues.values()),
        issue_codes=tuple(issues),
        evidence={
            "event_count": event_count,
            "chain_head_fact_count": head_count,
            "invalid_event_count": invalid_events,
            "invalid_head_count": invalid_heads,
        },
    )


def _outbox_check(connection: Any) -> RestoreReconciliationCheckV1:
    row = connection.execute(
        """
        SELECT count(*)::bigint,
               count(*) FILTER (WHERE published_at IS NULL)::bigint,
               count(*) FILTER (
                   WHERE claimed_until IS NOT NULL
                     AND claimed_until > statement_timestamp()
               )::bigint,
               count(*) FILTER (
                   WHERE published_at IS NOT NULL AND claim_token IS NOT NULL
               )::bigint,
               count(*) FILTER (
                   WHERE envelope ? 'organization_id'
                     AND envelope->>'organization_id' IS DISTINCT FROM organization_id
               )::bigint
          FROM core.outbox_events
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("outbox reconciliation query returned no result")
    total, pending, active_claims, published_claims, invalid_envelopes = (
        int(value) for value in row
    )
    issues: dict[str, int] = {}
    if active_claims:
        issues["OUTBOX_CLAIM_ACTIVE"] = active_claims
    if published_claims:
        issues["OUTBOX_PUBLISHED_CLAIM_RETAINED"] = published_claims
    if invalid_envelopes:
        issues["OUTBOX_SCOPE_MISMATCH"] = invalid_envelopes
    return reconciliation_check(
        "outbox",
        total_count=total,
        issue_count=sum(issues.values()),
        issue_codes=tuple(issues),
        evidence={
            "event_count": total,
            "pending_count": pending,
            "published_count": total - pending,
            "active_claim_count": active_claims,
            "published_claim_count": published_claims,
            "invalid_scope_count": invalid_envelopes,
        },
    )


def _workflow_database_facts(connection: Any) -> WorkflowDatabaseFacts:
    running = tuple(
        (str(row[0]), None if row[1] is None else str(row[1]))
        for row in connection.execute(
            """
            SELECT workflow_id, workflow_run_id
              FROM workflow.jobs
             WHERE status = 'RUNNING'
             ORDER BY workflow_id COLLATE "C"
            """
        ).fetchall()
    )
    dispatched = tuple(
        str(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT trigger.workflow_id
              FROM ingest.workflow_triggers AS trigger
             JOIN workflow.jobs AS job ON job.workflow_id = trigger.workflow_id
             WHERE trigger.status = 'DISPATCHED' AND job.status = 'RUNNING'
             ORDER BY 1
            """
        ).fetchall()
    )
    workflow_counts = connection.execute(
        """
        SELECT count(*)::bigint,
               count(*) FILTER (WHERE status IN ('PENDING', 'RUNNING', 'FAILED'))::bigint
          FROM workflow.reconciliation_items
        """
    ).fetchone()
    lance_counts = connection.execute(
        "SELECT count(*)::bigint FROM lance_pending_reconciliation"
    ).fetchone()
    job_count = connection.execute("SELECT count(*)::bigint FROM workflow.jobs").fetchone()
    trigger_count = connection.execute(
        "SELECT count(*)::bigint FROM ingest.workflow_triggers"
    ).fetchone()
    if None in (workflow_counts, lance_counts, job_count, trigger_count):
        raise RuntimeError("workflow reconciliation query returned no result")
    assert workflow_counts is not None
    assert lance_counts is not None
    assert job_count is not None
    assert trigger_count is not None
    unresolved = int(workflow_counts[1])
    pending_lance = int(lance_counts[0])
    evidence = {
        "running_workflow_runs_sha256": _sha_json(running),
        "dispatched_workflow_ids_sha256": _sha_json(dispatched),
        "job_count": int(job_count[0]),
        "trigger_count": int(trigger_count[0]),
        "reconciliation_item_count": int(workflow_counts[0]),
        "unresolved_reconciliation_count": unresolved,
        "pending_lance_reconciliation_count": pending_lance,
    }
    return WorkflowDatabaseFacts(
        running_workflow_runs=running,
        dispatched_workflow_ids=dispatched,
        unresolved_reconciliation_count=unresolved,
        pending_lance_reconciliation_count=pending_lance,
        total_fact_count=(
            int(job_count[0]) + int(trigger_count[0]) + int(workflow_counts[0]) + pending_lance
        ),
        evidence_sha256=_sha_json(evidence),
    )


def _permissions_check(connection: Any) -> RestoreReconciliationCheckV1:
    statements = {
        "PERMISSION_ACTIVE_SESSION_INVALID": """
            SELECT count(*)::bigint
              FROM access_control.sessions AS session
              LEFT JOIN access_control.accounts AS account
                ON account.principal_id = session.principal_id
             WHERE session.revoked_at IS NULL
               AND (
                   account.principal_id IS NULL
                   OR account.status <> 'ACTIVE'
                   OR account.deleted_at IS NOT NULL
                   OR session.credential_revision <> account.credential_revision
               )
        """,
        "PERMISSION_ACTIVE_MEMBERSHIP_INVALID": """
            SELECT count(*)::bigint
              FROM access_control.memberships AS membership
              LEFT JOIN access_control.accounts AS account
                ON account.principal_id = membership.principal_id
              LEFT JOIN access_control.organization_memberships AS organization_membership
                ON organization_membership.principal_id = membership.principal_id
               AND organization_membership.organization_id = membership.organization_id
               AND organization_membership.active
             WHERE membership.active
               AND (
                   account.principal_id IS NULL
                   OR account.status <> 'ACTIVE'
                   OR account.deleted_at IS NOT NULL
                   OR organization_membership.principal_id IS NULL
               )
        """,
        "PERMISSION_ACTIVE_GRANT_WITHOUT_MEMBERSHIP": """
            SELECT count(*)::bigint
              FROM access_control.capability_grants AS capability_grant
              LEFT JOIN access_control.memberships AS membership
                ON membership.principal_id = capability_grant.principal_id
               AND membership.organization_id = capability_grant.organization_id
               AND membership.project_id = capability_grant.project_id
               AND membership.active
             WHERE capability_grant.active AND membership.principal_id IS NULL
        """,
        "PERMISSION_PLATFORM_DUTY_CONFLICT": """
            SELECT count(*)::bigint
              FROM (
                  SELECT principal_id
                    FROM access_control.platform_capability_grants
                   WHERE active AND capability_key IN (
                       'platform.operations.read',
                       'platform.maintenance.operate',
                       'platform.release.operate',
                       'platform.maintenance.verify',
                       'platform.break_glass'
                   )
                   GROUP BY principal_id
                  HAVING count(*) > 1
              ) AS conflict
        """,
        "PERMISSION_PLATFORM_CAPABILITY_INVALID": """
            SELECT count(*)::bigint
              FROM access_control.platform_capability_grants
             WHERE capability_key NOT IN (
                 'platform.admin',
                 'platform.account.read',
                 'platform.account.manage',
                 'platform.account_security.manage',
                 'platform.operations.read',
                 'platform.maintenance.operate',
                 'platform.release.operate',
                 'platform.maintenance.verify',
                 'platform.break_glass'
             )
        """,
        "PERMISSION_RLS_NOT_FORCED": """
            SELECT count(*)::bigint
              FROM pg_policy AS policy
              JOIN pg_class AS relation ON relation.oid = policy.polrelid
             WHERE policy.polname = 'hc_scope_isolation'
               AND (NOT relation.relrowsecurity OR NOT relation.relforcerowsecurity)
        """,
    }
    failures: dict[str, int] = {}
    counts: dict[str, int] = {}
    for code, statement in statements.items():
        row = connection.execute(statement).fetchone()
        if row is None:
            raise RuntimeError("permission reconciliation query returned no result")
        count = int(row[0])
        counts[code] = count
        if count:
            failures[code] = count
    totals = connection.execute(
        """
        SELECT
            (SELECT count(*) FROM access_control.sessions WHERE revoked_at IS NULL)::bigint,
            (SELECT count(*) FROM access_control.memberships WHERE active)::bigint,
            (SELECT count(*) FROM access_control.capability_grants WHERE active)::bigint,
            (SELECT count(*) FROM access_control.platform_capability_grants WHERE active)::bigint,
            (SELECT count(*) FROM pg_policy WHERE polname = 'hc_scope_isolation')::bigint
        """
    ).fetchone()
    if totals is None:
        raise RuntimeError("permission reconciliation totals returned no result")
    total_facts = sum(int(value) for value in totals)
    return reconciliation_check(
        "permissions",
        total_count=total_facts,
        issue_count=sum(failures.values()),
        issue_codes=tuple(failures),
        evidence={
            "active_session_count": int(totals[0]),
            "active_membership_count": int(totals[1]),
            "active_capability_grant_count": int(totals[2]),
            "active_platform_capability_grant_count": int(totals[3]),
            "scoped_policy_count": int(totals[4]),
            "invalid_counts": dict(sorted(counts.items())),
        },
    )


def _sha_json(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
