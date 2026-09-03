import { isCanonicalAuditEventName } from "../event-catalog";
import type {
  AuditActorRole,
  AuditBootstrap,
  AuditEventPage,
  AuditEventView,
  AuditExportDownloadAuthorization,
  AuditExportJob,
  AuditFacets,
  AuditLegalHold,
  AuditOutcome,
  AuditRetentionPolicy,
  AuditRisk,
} from "../types";
import type {
  AuditBootstrapWire,
  AuditEventEnvelopeWire,
  AuditEventPageWire,
  AuditEventWire,
  AuditExportDownloadAuthorizationWire,
  AuditExportJobWire,
  AuditFacetsWire,
  AuditIntegrityWire,
  AuditLegalHoldWire,
  AuditRetentionPolicyWire,
} from "./schemas";

const currentRoles = new Set([
  "PROJECT_ADMIN",
  "PROJECT_DEVELOPER",
  "PROJECT_DATA_PROCESSOR",
]);
const outcomes = new Set(["SUCCEEDED", "DENIED", "FAILED", "PARTIAL"]);
const risks = new Set(["LOW", "MEDIUM", "HIGH", "CRITICAL"]);

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`AUDIT_CONTRACT_MISMATCH:${message}`);
}

export function adaptAuditEvent(wire: AuditEventWire): AuditEventView {
  invariant(wire.scope.project_id !== null, "event_project_scope");
  const eventNameKnown = isCanonicalAuditEventName(wire.event_name);
  const actorRoles = wire.actor.role_ids.map(
    (role): AuditActorRole =>
      currentRoles.has(role) ? (role as AuditActorRole) : "HISTORICAL_ROLE",
  );
  const outcome = outcomes.has(wire.outcome.status)
    ? (wire.outcome.status as AuditOutcome)
    : "UNKNOWN";
  const risk = risks.has(wire.risk.level)
    ? (wire.risk.level as AuditRisk)
    : "UNKNOWN";
  const historicalOnly = wire.resource.type === "RETIRED_RESOURCE";
  const allowedActions =
    !eventNameKnown || historicalOnly ? [] : wire.allowed_actions;
  return {
    schemaVersion: 1,
    eventId: wire.event_id,
    eventName: wire.event_name,
    eventNameKnown,
    occurredAt: wire.occurred_at,
    recordedAt: wire.recorded_at,
    actor: {
      type: wire.actor.type,
      principalId: wire.actor.principal_id,
      displayName: wire.actor.display_name,
      roles: actorRoles,
      hasUnknownRole: actorRoles.includes("HISTORICAL_ROLE"),
    },
    scope: {
      organizationId: wire.scope.organization_id,
      projectId: wire.scope.project_id,
      regionCode: wire.scope.region_code,
    },
    resource: {
      type: historicalOnly ? "RETIRED_RESOURCE" : wire.resource.type,
      id: wire.resource.id,
      displayName: historicalOnly
        ? "已退役资源记录"
        : wire.resource.display_name,
      parentRefs: wire.resource.parent_refs,
      historicalOnly,
    },
    request: {
      requestId: wire.request.request_id,
      jobId: wire.request.job_id,
      clientType: wire.request.client_type,
      ipAddress: wire.request.ip_address,
      deviceSummary: wire.request.device_summary,
    },
    outcome: {
      status: outcome,
      reasonCode: wire.outcome.reason_code,
      httpStatus: wire.outcome.http_status,
    },
    risk: { level: risk, signalCodes: wire.risk.signal_codes },
    change:
      wire.change === null
        ? null
        : {
            summaryCode: wire.change.summary_code,
            changedFields: wire.change.changed_fields,
            before: wire.change.before,
            after: wire.change.after,
            omittedFieldClasses: wire.change.omitted_field_classes,
          },
    retention: {
      className: wire.retention.class,
      policyVersion: wire.retention.policy_version,
      retainUntil: wire.retention.retain_until,
      legalHold: wire.retention.legal_hold,
    },
    integrity: {
      status: wire.integrity.status,
      version: wire.integrity.version,
      recordDigest: wire.integrity.record_digest,
      checkpointId: wire.integrity.checkpoint_id,
    },
    allowedActions,
    readOnly: !eventNameKnown || historicalOnly,
    hasUnknownEnum:
      !eventNameKnown ||
      actorRoles.includes("HISTORICAL_ROLE") ||
      outcome === "UNKNOWN" ||
      risk === "UNKNOWN",
  };
}

export function adaptAuditBootstrap(wire: AuditBootstrapWire): AuditBootstrap {
  invariant(
    wire.scope.organization_id === wire.data.scope.organization_id &&
      wire.scope.project_id === wire.data.scope.project_id,
    "bootstrap_scope",
  );
  return {
    metrics: {
      today: BigInt(wire.data.metrics.today),
      highRisk: BigInt(wire.data.metrics.high_risk),
      failed: BigInt(wire.data.metrics.failed),
      activeActors: BigInt(wire.data.metrics.active_actors),
    },
    asOf: wire.data.as_of,
    catalogVersion: wire.data.catalog_version,
    policyVersion: wire.data.policy_version,
    integrity: wire.data.integrity,
    allowedActions: wire.data.allowed_actions,
    blockedReasons: wire.data.blocked_reasons.map((item) => ({
      action: item.action ?? "",
      code: item.code,
      message: item.message ?? item.message_key ?? item.code,
    })),
    requestId: wire.request_id,
  };
}

export function adaptAuditFacets(wire: AuditFacetsWire): AuditFacets {
  const eventNames = wire.data.event_names.filter(isCanonicalAuditEventName);
  return {
    eventNames,
    actorIds: wire.data.actor_ids,
    resourceTypes: wire.data.resource_types.filter(
      (type) => type !== "RETIRED_RESOURCE",
    ),
    outcomes: wire.data.outcomes,
    riskLevels: wire.data.risk_levels,
    hasUnknownEventName: eventNames.length !== wire.data.event_names.length,
    requestId: wire.request_id,
  };
}

export function adaptAuditEventPage(wire: AuditEventPageWire): AuditEventPage {
  const items = wire.items.map(adaptAuditEvent);
  invariant(
    new Set(items.map((item) => item.eventId)).size === items.length,
    "duplicate_event",
  );
  for (let index = 1; index < items.length; index += 1) {
    const previous = items[index - 1]!;
    const current = items[index]!;
    invariant(
      previous.occurredAt > current.occurredAt ||
        (previous.occurredAt === current.occurredAt &&
          previous.eventId > current.eventId),
      "unstable_sort",
    );
  }
  if (items.length === 0)
    invariant(
      wire.page_info.start_cursor === null &&
        wire.page_info.end_cursor === null,
      "empty_cursor",
    );
  return {
    items,
    pageInfo: {
      hasNextPage: wire.page_info.has_next_page,
      hasPreviousPage: wire.page_info.has_previous_page,
      startCursor: wire.page_info.start_cursor,
      endCursor: wire.page_info.end_cursor,
    },
    snapshotAt: wire.snapshot_at,
    redactionPolicyVersion: wire.redaction.policy_version,
    omittedFieldClasses: wire.redaction.omitted_field_classes,
    requestId: wire.request_id,
    hasUnknownEnum: items.some((item) => item.hasUnknownEnum),
  };
}

export function adaptAuditEventEnvelope(
  wire: AuditEventEnvelopeWire,
): AuditEventView {
  return adaptAuditEvent(wire.data);
}

export function adaptAuditIntegrity(wire: AuditIntegrityWire) {
  return {
    status: wire.data.status,
    version: wire.data.version,
    checkedAt: wire.data.checked_at,
    checkedEventCount: BigInt(wire.data.checked_event_count),
    checkedChainCount: BigInt(wire.data.checked_chain_count),
    verifiedThrough: wire.data.verified_through,
    requestId: wire.request_id,
  };
}

function adaptScope(wire: {
  organization_id: string;
  project_id: string | null;
  region_code: string | null;
}) {
  invariant(wire.project_id !== null, "governance_project_scope");
  return {
    organizationId: wire.organization_id,
    projectId: wire.project_id,
    regionCode: wire.region_code,
  };
}

export function adaptAuditRetentionPolicy(
  wire: AuditRetentionPolicyWire,
): AuditRetentionPolicy {
  return {
    scope: adaptScope(wire.scope),
    policyVersion: wire.policy_version,
    standardDays: wire.standard_days,
    securityDays: wire.security_days,
    etag: wire.etag,
    updatedBy: wire.updated_by,
    updatedAt: wire.updated_at,
  };
}

export function adaptAuditLegalHold(wire: AuditLegalHoldWire): AuditLegalHold {
  return {
    holdId: wire.hold_id,
    scope: adaptScope(wire.scope),
    reason: wire.reason,
    occurredFrom: wire.occurred_from,
    occurredTo: wire.occurred_to,
    status: wire.status,
    createdBy: wire.created_by,
    createdAt: wire.created_at,
    releasedBy: wire.released_by,
    releasedAt: wire.released_at,
  };
}

export function adaptAuditExportJob(wire: AuditExportJobWire): AuditExportJob {
  return {
    jobId: wire.job_id,
    scope: adaptScope(wire.scope),
    status: wire.status,
    exportedEventCount: BigInt(wire.progress.exported_event_count),
    scannedPageCount: BigInt(wire.progress.scanned_page_count),
    occurredFrom: wire.occurred_from,
    occurredTo: wire.occurred_to,
    createdBy: wire.created_by,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
    artifact: wire.artifact
      ? {
          mediaType: wire.artifact.media_type,
          sha256: wire.artifact.sha256,
          sizeBytes: BigInt(wire.artifact.size_bytes),
        }
      : null,
    errorCode: wire.error_code,
    errorMessage: wire.error_message,
  };
}

export function adaptAuditExportDownloadAuthorization(
  wire: AuditExportDownloadAuthorizationWire,
): AuditExportDownloadAuthorization {
  return {
    jobId: wire.job_id,
    downloadUrl: wire.download_url,
    expiresAt: wire.expires_at,
    artifact: {
      mediaType: wire.artifact.media_type,
      sha256: wire.artifact.sha256,
      sizeBytes: BigInt(wire.artifact.size_bytes),
    },
  };
}
