import type { CanonicalAuditEventName } from "./event-catalog";

export type AuditScope = Readonly<{
  organizationId: string;
  projectId: string;
  regionCode: string | null;
}>;

export type AuditOutcome =
  | "SUCCEEDED"
  | "DENIED"
  | "FAILED"
  | "PARTIAL"
  | "UNKNOWN";
export type AuditRisk = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | "UNKNOWN";
export type AuditActorRole =
  | "PROJECT_ADMIN"
  | "PROJECT_DEVELOPER"
  | "PROJECT_DATA_PROCESSOR"
  | "HISTORICAL_ROLE";

export type AuditEventView = Readonly<{
  schemaVersion: 1;
  eventId: string;
  eventName: string;
  eventNameKnown: boolean;
  occurredAt: string;
  recordedAt: string;
  actor: Readonly<{
    type: string;
    principalId: string | null;
    displayName: string;
    roles: readonly AuditActorRole[];
    hasUnknownRole: boolean;
  }>;
  scope: AuditScope;
  resource: Readonly<{
    type: string;
    id: string;
    displayName: string;
    parentRefs: readonly Readonly<{ type: string; id: string }>[];
    historicalOnly: boolean;
  }>;
  request: Readonly<{
    requestId: string;
    jobId: string | null;
    clientType: string;
    ipAddress: string | null;
    deviceSummary: string | null;
  }>;
  outcome: Readonly<{
    status: AuditOutcome;
    reasonCode: string | null;
    httpStatus: number | null;
  }>;
  risk: Readonly<{ level: AuditRisk; signalCodes: readonly string[] }>;
  change: null | Readonly<{
    summaryCode: string;
    changedFields: readonly string[];
    before: Readonly<
      Record<
        string,
        | string
        | number
        | boolean
        | null
        | readonly (string | number | boolean | null)[]
      >
    >;
    after: Readonly<
      Record<
        string,
        | string
        | number
        | boolean
        | null
        | readonly (string | number | boolean | null)[]
      >
    >;
    omittedFieldClasses: readonly string[];
  }>;
  retention: Readonly<{
    className: string;
    policyVersion: string;
    retainUntil: string | null;
    legalHold: boolean;
  }>;
  integrity: Readonly<{
    status: string;
    version: string;
    recordDigest: string | null;
    checkpointId: string | null;
  }>;
  allowedActions: readonly string[];
  readOnly: boolean;
  hasUnknownEnum: boolean;
}>;

export type AuditBootstrap = Readonly<{
  metrics: Readonly<{
    today: bigint;
    highRisk: bigint;
    failed: bigint;
    activeActors: bigint;
  }>;
  asOf: string;
  catalogVersion: string;
  policyVersion: string;
  integrity: "PASSED" | "FAILED" | "UNKNOWN";
  allowedActions: readonly string[];
  blockedReasons: readonly Readonly<{
    action: string;
    code: string;
    message: string;
  }>[];
  requestId: string;
}>;

export type AuditIntegrityCheck = Readonly<{
  status: "PASSED" | "FAILED";
  version: string;
  checkedAt: string;
  checkedEventCount: bigint;
  checkedChainCount: bigint;
  verifiedThrough: string | null;
  requestId: string;
}>;

export type AuditFacets = Readonly<{
  eventNames: readonly CanonicalAuditEventName[];
  actorIds: readonly string[];
  resourceTypes: readonly string[];
  outcomes: readonly Exclude<AuditOutcome, "UNKNOWN">[];
  riskLevels: readonly Exclude<AuditRisk, "UNKNOWN">[];
  hasUnknownEventName: boolean;
  requestId: string;
}>;

export type AuditEventPage = Readonly<{
  items: readonly AuditEventView[];
  pageInfo: Readonly<{
    hasNextPage: boolean;
    hasPreviousPage: boolean;
    startCursor: string | null;
    endCursor: string | null;
  }>;
  snapshotAt: string;
  redactionPolicyVersion: string;
  omittedFieldClasses: readonly string[];
  requestId: string;
  hasUnknownEnum: boolean;
}>;

export type AuditRetentionPolicy = Readonly<{
  scope: AuditScope;
  policyVersion: number;
  standardDays: number;
  securityDays: number;
  etag: string;
  updatedBy: string;
  updatedAt: string;
}>;

export type AuditLegalHold = Readonly<{
  holdId: string;
  scope: AuditScope;
  reason: string;
  occurredFrom: string;
  occurredTo: string;
  status: "ACTIVE" | "RELEASED";
  createdBy: string;
  createdAt: string;
  releasedBy: string | null;
  releasedAt: string | null;
}>;

export type AuditExportJob = Readonly<{
  jobId: string;
  scope: AuditScope;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
  exportedEventCount: bigint;
  scannedPageCount: bigint;
  occurredFrom: string;
  occurredTo: string;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
  artifact: null | Readonly<{
    mediaType: "application/x-ndjson";
    sha256: string;
    sizeBytes: bigint;
  }>;
  errorCode: string | null;
  errorMessage: string | null;
}>;

export type AuditExportDownloadAuthorization = Readonly<{
  jobId: string;
  downloadUrl: string;
  expiresAt: string;
  artifact: NonNullable<AuditExportJob["artifact"]>;
}>;
