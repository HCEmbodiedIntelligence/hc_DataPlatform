import { z } from "zod";
import type { components } from "../../shared/api/generated/platform";
import { request } from "../../shared/api/http-client";
import { parseWire } from "../../shared/api/validate";

export type PlatformOperationsOverview =
  components["schemas"]["PlatformOperationsOverview"];
export type PlatformOperationsNode =
  components["schemas"]["PlatformOperationsNode"];
export type PlatformOperationsBackup =
  components["schemas"]["PlatformOperationsBackup"];
export type PlatformLogPage = components["schemas"]["PlatformLogPage"];
export type PlatformLogEvent = components["schemas"]["PlatformLogEvent"];
export type PlatformReleaseRun = components["schemas"]["ReleaseRun"];
export type PlatformReleaseHistory =
  components["schemas"]["ReleaseHistoryPage"];
export type PlatformObjectStoreConfig = Readonly<{
  format_version: "hc-object-store-config/v1";
  environment_id: string;
  revision: number;
  source: "unconfigured" | "environment" | "database";
  configured: boolean;
  provider: "oss" | "s3";
  endpoint: string;
  public_endpoint: string;
  bucket: string;
  region: string;
  access_key_configured: boolean;
  access_key_hint: string | null;
  activation_required: boolean;
  updated_at: string | null;
}>;

export type PlatformObjectStoreConfigUpdate = Readonly<{
  expected_revision: number;
  provider: "oss";
  endpoint: string;
  public_endpoint: string;
  bucket: string;
  region: string;
  access_key?: string;
  secret_key?: string;
}>;

export type PlatformObjectStoreLocation = Readonly<{
  format_version: "hc-object-store-location/v1";
  configured: boolean;
  provider: "oss" | "s3";
  public_endpoint: string;
  region: string;
}>;

export type PlatformProject = Readonly<{
  organization_id: string;
  organization_name: string;
  project_id: string;
  project_name: string;
}>;

export type PlatformOrganization = Readonly<{
  organization_id: string;
  organization_name: string;
}>;

export type PlatformOrganizationPage = Readonly<{
  format_version: "hc-platform-organization-directory/v1";
  count: number;
  items: readonly PlatformOrganization[];
}>;

export type PlatformOrganizationCreate = Readonly<{
  organization_id: string;
  organization_name: string;
}>;

export type PlatformProjectPage = Readonly<{
  format_version: "hc-platform-project-directory/v1";
  count: number;
  items: readonly PlatformProject[];
}>;

export type PlatformProjectCreate = Readonly<{
  organization_id: string;
  project_id: string;
  project_name: string;
}>;

export type PlatformLogFilters = Readonly<{
  occurredFrom: string;
  occurredTo: string;
  service?: PlatformLogEvent["service"];
  severity?: PlatformLogEvent["severity"];
  eventCode?: string;
  requestId?: string;
  operationId?: string;
  workflowId?: string;
  limit?: number;
}>;

const instant = z.string().datetime({ offset: true });
const digest = z.union([
  z.string().regex(/^sha256:[0-9a-f]{64}$/u),
  z.literal("unreleased"),
]);
const reference = z.string().regex(/^id-hmac-sha256:[0-9a-f]{64}$/u);
const correlation = z
  .string()
  .min(1)
  .max(253)
  .regex(/^[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,251}[A-Za-z0-9])?$/u);
const service = z.enum([
  "hc-data-platform-api",
  "hc-data-platform-worker",
  "hc-data-platform-media-worker",
  "hc-data-platform-frontend",
  "hc-data-platform-gateway",
]);
const severity = z.enum(["TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"]);

export const platformObjectStoreConfigWireSchema = z
  .object({
    format_version: z.literal("hc-object-store-config/v1"),
    environment_id: z.string().min(1).max(128),
    revision: z.number().int().nonnegative(),
    source: z.enum(["unconfigured", "environment", "database"]),
    configured: z.boolean(),
    provider: z.enum(["oss", "s3"]),
    endpoint: z.string().max(2048),
    public_endpoint: z.string().max(2048),
    bucket: z.string().max(63),
    region: z.string().max(63),
    access_key_configured: z.boolean(),
    access_key_hint: z.string().max(64).nullable(),
    activation_required: z.boolean(),
    updated_at: instant.nullable(),
  })
  .strict();

export const platformObjectStoreLocationWireSchema: z.ZodType<PlatformObjectStoreLocation> =
  z
    .object({
      format_version: z.literal("hc-object-store-location/v1"),
      configured: z.boolean(),
      provider: z.enum(["oss", "s3"]),
      public_endpoint: z.string().max(2048),
      region: z.string().max(63),
    })
    .strict()
    .superRefine((value, context) => {
      if (
        value.configured !==
        Boolean(value.public_endpoint.trim() && value.region.trim())
      ) {
        context.addIssue({
          code: "custom",
          path: ["configured"],
          message: "object-store location completeness mismatch",
        });
      }
    });

const stableScopeId = z
  .string()
  .min(1)
  .max(128)
  .regex(/^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$/u);

export const platformProjectWireSchema: z.ZodType<PlatformProject> = z
  .object({
    organization_id: stableScopeId,
    organization_name: z.string().min(1).max(256),
    project_id: stableScopeId,
    project_name: z.string().min(1).max(256),
  })
  .strict();

export const platformOrganizationWireSchema: z.ZodType<PlatformOrganization> = z
  .object({
    organization_id: stableScopeId,
    organization_name: z.string().min(1).max(256),
  })
  .strict();

export const platformOrganizationPageWireSchema: z.ZodType<PlatformOrganizationPage> =
  z
    .object({
      format_version: z.literal("hc-platform-organization-directory/v1"),
      count: z.number().int().nonnegative(),
      items: z.array(platformOrganizationWireSchema),
    })
    .strict()
    .superRefine((value, context) => {
      if (value.count !== value.items.length) {
        context.addIssue({
          code: "custom",
          path: ["count"],
          message: "platform organization count mismatch",
        });
      }
    });

export const platformProjectPageWireSchema: z.ZodType<PlatformProjectPage> = z
  .object({
    format_version: z.literal("hc-platform-project-directory/v1"),
    count: z.number().int().nonnegative(),
    items: z.array(platformProjectWireSchema),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.count !== value.items.length) {
      context.addIssue({
        code: "custom",
        path: ["count"],
        message: "platform project count mismatch",
      });
    }
  });

const releaseSchema = z
  .object({
    format_version: z.literal("hc-platform-release-identity/v1"),
    release_id: z
      .string()
      .regex(/^(?:unreleased|platform-v[A-Za-z0-9][A-Za-z0-9._-]{0,119})$/u),
    semantic_version: z
      .string()
      .regex(/^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$/u),
    git_commit: z.union([
      z.string().regex(/^[0-9a-f]{40}$/u),
      z.literal("unknown"),
    ]),
    chart_version: z
      .string()
      .regex(/^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$/u),
    release_manifest_digest: digest,
    migration_manifest_digest: digest,
    component: z.enum([
      "api",
      "worker",
      "media-worker",
      "migration",
      "frontend",
    ]),
    component_image_digest: digest,
  })
  .strict();

const nodeSchema = z
  .object({
    node_ref: reference,
    role: z.enum([
      "frontend",
      "api",
      "worker",
      "media-worker",
      "maintenance-controller",
    ]),
    release_id: z.string().min(1).max(128),
    release_manifest_digest: digest,
    started_at: instant,
    last_heartbeat_at: instant,
    readiness: z.enum(["starting", "ready", "not_ready", "draining"]),
    failed_checks: z.array(z.string().regex(/^[a-z][a-z0-9_-]{0,63}$/u)),
    applied_config_revision: z.number().int().nonnegative(),
    stale: z.boolean(),
  })
  .strict();

const backupSchema = z
  .object({
    backup_ref: reference,
    format_version: z.literal("hc-platform-backup/v1"),
    mode: z.enum(["portable", "snapshot"]),
    status: z.enum([
      "CREATING",
      "CREATED",
      "INTEGRITY_VERIFIED",
      "RESTORE_VERIFIED",
      "FAILED",
      "CORRUPT",
      "EXPIRED",
    ]),
    release_manifest_sha256: z.string().regex(/^[0-9a-f]{64}$/u),
    backup_created_at: instant,
    backup_completed_at: instant,
    status_occurred_at: instant,
  })
  .strict();

const preflightCheckSchema = z
  .object({
    code: z.enum([
      "RELEASE_IDENTITY",
      "NODE_CONVERGENCE",
      "NODE_READINESS",
      "CONFIG_CONVERGENCE",
      "VERIFIED_BACKUP",
      "VERIFIED_RESTORE",
      "CENTRAL_LOG_SEARCH",
    ]),
    status: z.enum(["PASS", "BLOCKED"]),
    reason_code: z.string().regex(/^[A-Z][A-Z0-9_]{0,127}$/u),
  })
  .strict();

export const platformOperationsOverviewWireSchema = z
  .object({
    format_version: z.literal("hc-platform-operations-overview/v1"),
    observed_at: instant,
    environment_ref: reference,
    release: releaseSchema,
    node_count: z.number().int().nonnegative(),
    nodes: z.array(nodeSchema),
    backup_count: z.number().int().min(0).max(20),
    backups: z.array(backupSchema).max(20),
    upgrade_preflight: z
      .object({
        status: z.enum(["READY", "BLOCKED"]),
        checks: z.array(preflightCheckSchema).length(7),
      })
      .strict(),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.node_count !== value.nodes.length)
      context.addIssue({
        code: "custom",
        path: ["node_count"],
        message: "node count mismatch",
      });
    if (value.backup_count !== value.backups.length)
      context.addIssue({
        code: "custom",
        path: ["backup_count"],
        message: "backup count mismatch",
      });
  });

const nullableCorrelation = correlation.nullable();
export const platformLogPageWireSchema = z
  .object({
    format_version: z.literal("hc-platform-log-page/v1"),
    observed_at: instant,
    occurred_from: instant,
    occurred_to: instant,
    count: z.number().int().min(0).max(200),
    truncated: z.boolean(),
    items: z
      .array(
        z
          .object({
            schema_version: z.literal("hc-platform-log-event/v1"),
            timestamp: instant,
            severity,
            service,
            role: z.enum([
              "api",
              "worker",
              "media-worker",
              "frontend",
              "gateway",
            ]),
            release_id: correlation,
            event_code: z.string().regex(/^[A-Z][A-Z0-9_.-]{0,127}$/u),
            request_id: nullableCorrelation,
            operation_id: nullableCorrelation,
            workflow_id: nullableCorrelation,
            duration_ms: z.number().nonnegative().nullable().optional(),
            retry_count: z.number().int().nonnegative().nullable().optional(),
            error_type: z
              .string()
              .regex(/^[A-Za-z][A-Za-z0-9_.]{0,127}$/u)
              .nullable(),
            route: z
              .string()
              .min(1)
              .max(255)
              .regex(/^\/[A-Za-z0-9{}_.:/-]*$/u)
              .nullable()
              .optional(),
            http_method: z
              .enum([
                "GET",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
                "HEAD",
                "OPTIONS",
              ])
              .nullable()
              .optional(),
            status_code: z
              .number()
              .int()
              .min(100)
              .max(599)
              .nullable()
              .optional(),
          })
          .strict(),
      )
      .max(200),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.count !== value.items.length)
      context.addIssue({
        code: "custom",
        path: ["count"],
        message: "log count mismatch",
      });
  });

const releaseState = z.enum([
  "PREFLIGHT_BLOCKED",
  "AWAITING_APPROVAL",
  "APPROVED",
  "EXPAND",
  "CANARY",
  "ROLLOUT",
  "CONTRACT_PENDING",
  "COMPLETED",
  "ROLLED_BACK",
  "FAILED",
]);
const releaseEventSchema = z
  .object({
    state_version: z.number().int().positive(),
    event_kind: z.string().regex(/^[A-Z][A-Z0-9_]{0,127}$/u),
    state: releaseState,
    actor_ref: reference,
    reason_code: z.string().regex(/^[A-Z][A-Z0-9_]{0,127}$/u),
    occurred_at: instant,
  })
  .strict();
export const platformReleaseRunWireSchema = z
  .object({
    format_version: z.literal("hc-platform-release-run/v1"),
    release_id: z
      .string()
      .regex(/^platform-v[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/u),
    source_version: z.string().regex(/^[0-9]+\.[0-9]+\.[0-9]+$/u),
    target_version: z.string().regex(/^[0-9]+\.[0-9]+\.[0-9]+$/u),
    manifest_sha256: z.string().regex(/^[0-9a-f]{64}$/u),
    state: releaseState,
    state_version: z.number().int().positive(),
    approval_required: z.literal(true),
    approved: z.boolean(),
    created_at: instant,
    updated_at: instant,
    events: z.array(releaseEventSchema),
  })
  .strict();
export const platformReleaseHistoryWireSchema = z
  .object({
    format_version: z.literal("hc-platform-release-history/v1"),
    count: z.number().int().min(0).max(50),
    items: z.array(platformReleaseRunWireSchema).max(50),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.count !== value.items.length)
      context.addIssue({
        code: "custom",
        path: ["count"],
        message: "release history count mismatch",
      });
  });

export async function getPlatformOperationsOverview(
  signal?: AbortSignal,
): Promise<PlatformOperationsOverview> {
  const endpoint = "/platform/overview";
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scopeMode: "session",
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseWire(platformOperationsOverviewWireSchema, raw, { endpoint });
}

export async function getPlatformObjectStoreConfig(
  signal?: AbortSignal,
): Promise<PlatformObjectStoreConfig> {
  const endpoint = "/platform/object-store-config";
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scopeMode: "session",
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseWire(platformObjectStoreConfigWireSchema, raw, { endpoint });
}

export async function getPlatformObjectStoreLocation(
  signal?: AbortSignal,
): Promise<PlatformObjectStoreLocation> {
  const endpoint = "/platform/object-store-location";
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scopeMode: "session",
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseWire(platformObjectStoreLocationWireSchema, raw, { endpoint });
}

export async function updatePlatformObjectStoreConfig(
  body: PlatformObjectStoreConfigUpdate,
): Promise<PlatformObjectStoreConfig> {
  const endpoint = "/platform/object-store-config";
  const raw = await request<unknown>({
    method: "PUT",
    path: endpoint,
    scopeMode: "session",
    body,
  });
  return parseWire(platformObjectStoreConfigWireSchema, raw, { endpoint });
}

export async function listPlatformProjects(
  signal?: AbortSignal,
): Promise<PlatformProjectPage> {
  const endpoint = "/platform/projects";
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scopeMode: "session",
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseWire(platformProjectPageWireSchema, raw, { endpoint });
}

export async function listPlatformOrganizations(
  signal?: AbortSignal,
): Promise<PlatformOrganizationPage> {
  const endpoint = "/platform/organizations";
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scopeMode: "session",
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseWire(platformOrganizationPageWireSchema, raw, { endpoint });
}

export async function createPlatformOrganization(
  body: PlatformOrganizationCreate,
): Promise<PlatformOrganization> {
  const endpoint = "/platform/organizations";
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    scopeMode: "session",
    body,
  });
  return parseWire(platformOrganizationWireSchema, raw, { endpoint });
}

export async function createPlatformProject(
  body: PlatformProjectCreate,
): Promise<PlatformProject> {
  const endpoint = "/platform/projects";
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    scopeMode: "session",
    body,
  });
  return parseWire(platformProjectWireSchema, raw, { endpoint });
}

export async function queryPlatformRuntimeLogs(
  filters: PlatformLogFilters,
  signal?: AbortSignal,
): Promise<PlatformLogPage> {
  const endpoint = "/platform/logs";
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scopeMode: "session",
    cache: "no-store",
    query: filters,
    ...(signal ? { signal } : {}),
  });
  return parseWire(platformLogPageWireSchema, raw, { endpoint });
}

export async function getPlatformReleaseHistory(
  signal?: AbortSignal,
): Promise<PlatformReleaseHistory> {
  const endpoint = "/platform/releases";
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scopeMode: "session",
    cache: "no-store",
    query: { limit: 20 },
    ...(signal ? { signal } : {}),
  });
  return parseWire(platformReleaseHistoryWireSchema, raw, { endpoint });
}

export async function approvePlatformRelease(
  releaseId: string,
  expectedStateVersion: number,
  reason: string,
): Promise<PlatformReleaseRun> {
  const endpoint = `/platform/releases/${encodeURIComponent(releaseId)}:approve`;
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    scopeMode: "session",
    body: { expected_state_version: expectedStateVersion, reason },
  });
  return parseWire(platformReleaseRunWireSchema, raw, { endpoint });
}
