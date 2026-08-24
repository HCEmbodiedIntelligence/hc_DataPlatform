import { z } from "zod";

const id = z.string().min(1).max(256);
const instant = z.string().datetime({ offset: true });
const decimal = z.string().regex(/^(0|[1-9]\d*)$/);
const safeInteger = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const auditScope = z
  .object({
    organization_id: id,
    project_id: id.nullable(),
    region_code: z.string().min(1).max(64).nullable(),
  })
  .strict();

const blockedReason = z
  .object({
    action: z.string().min(1).optional(),
    code: z.string().min(1),
    message: z.string().optional(),
    message_key: z.string().optional(),
  })
  .strict();

const safeScalar = z.union([
  z.string().max(512),
  z.number().finite(),
  z.boolean(),
  z.null(),
]);
const safeValue = z.union([safeScalar, z.array(safeScalar).max(100)]);
const forbiddenKey =
  /token|cookie|session|sts|secret|password|credential|authorization|signed[_-]?url|sql|stack|host[_-]?path/i;
const safeObject = z
  .record(z.string().min(1).max(128), safeValue)
  .superRefine((value, context) => {
    if (Object.keys(value).length > 100)
      context.addIssue({ code: "custom", message: "too many audit fields" });
    for (const [key, item] of Object.entries(value)) {
      if (forbiddenKey.test(key))
        context.addIssue({ code: "custom", message: "forbidden audit field" });
      const values = Array.isArray(item) ? item : [item];
      if (
        values.some(
          (entry) =>
            typeof entry === "string" && /^(?:https?|s3|oss):\/\//i.test(entry),
        )
      ) {
        context.addIssue({
          code: "custom",
          message: "URL-like audit value is forbidden",
        });
      }
    }
  });

export const auditEventWireSchema = z
  .object({
    schema_version: z.literal(1),
    event_id: id,
    event_name: z.string().min(1).max(160),
    occurred_at: instant,
    recorded_at: instant,
    actor: z
      .object({
        type: z.string().min(1).max(64),
        principal_id: id.nullable(),
        display_name: z.string().min(1).max(256),
        role_ids: z.array(z.string().min(1).max(128)),
        delegated_by_principal_id: id.nullable().optional(),
      })
      .strict(),
    scope: auditScope,
    resource: z
      .object({
        type: z.string().min(1).max(128),
        id,
        display_name: z.string().min(1).max(256),
        parent_refs: z
          .array(z.object({ type: z.string().min(1).max(128), id }).strict())
          .max(20),
      })
      .strict(),
    request: z
      .object({
        request_id: id,
        job_id: id.nullable(),
        client_type: z.string().min(1).max(64),
        ip_address: z.string().max(64).nullable(),
        device_summary: z.string().max(256).nullable(),
      })
      .strict(),
    outcome: z
      .object({
        status: z.string().min(1).max(64),
        reason_code: z.string().max(128).nullable(),
        http_status: z.number().int().min(100).max(599).nullable(),
      })
      .strict(),
    risk: z
      .object({
        level: z.string().min(1).max(64),
        signal_codes: z.array(z.string().min(1).max(128)).max(100),
      })
      .strict(),
    change: z
      .object({
        summary_code: z.string().min(1).max(128),
        changed_fields: z.array(z.string().min(1).max(128)).max(100),
        before: safeObject,
        after: safeObject,
        omitted_field_classes: z.array(z.string().min(1).max(128)).max(100),
      })
      .strict()
      .nullable(),
    relationships: z
      .object({
        parent_event_id: id.nullable(),
        related_event_ids: z.array(id).max(100),
        resource_refs: z
          .array(
            z
              .object({
                type: z.string().min(1),
                id,
                display_name: z.string().min(1),
                parent_refs: z.array(
                  z.object({ type: z.string(), id }).strict(),
                ),
              })
              .strict(),
          )
          .max(100),
      })
      .strict(),
    retention: z
      .object({
        class: z.string().min(1),
        policy_version: z.string().min(1),
        retain_until: instant.nullable(),
        legal_hold: z.boolean(),
      })
      .strict(),
    integrity: z
      .object({
        status: z.string().min(1),
        version: z.string().min(1),
        record_digest: z.string().max(512).nullable(),
        checkpoint_id: id.nullable(),
      })
      .strict(),
    producer: z
      .object({
        service: z.string().min(1).max(128),
        producer_event_id: id,
        contract_version: z.string().min(1),
      })
      .strict(),
    allowed_actions: z.array(z.string().min(1)),
    blocked_reasons: z.array(blockedReason),
  })
  .strict();

export const auditBootstrapWireSchema = z
  .object({
    data: z
      .object({
        scope: auditScope,
        metrics: z
          .object({
            today: decimal,
            high_risk: decimal,
            failed: decimal,
            active_actors: decimal,
          })
          .strict(),
        as_of: instant,
        catalog_version: z.string().min(1),
        policy_version: z.string().min(1),
        integrity: z.enum(["PASSED", "FAILED", "UNKNOWN"]),
        allowed_actions: z.array(z.string().min(1)),
        blocked_reasons: z.array(blockedReason),
      })
      .strict(),
    scope: auditScope,
    request_id: id,
    contract_version: z.literal("v1"),
  })
  .strict();

export const auditFacetsWireSchema = z
  .object({
    data: z
      .object({
        event_names: z.array(z.string().min(1)),
        actor_ids: z.array(id),
        resource_types: z.array(z.string().min(1).max(128)),
        outcomes: z.array(z.enum(["SUCCEEDED", "DENIED", "FAILED", "PARTIAL"])),
        risk_levels: z.array(z.enum(["LOW", "MEDIUM", "HIGH", "CRITICAL"])),
      })
      .strict(),
    scope: auditScope,
    request_id: id,
    contract_version: z.literal("v1"),
  })
  .strict();

export const auditEventPageWireSchema = z
  .object({
    items: z.array(auditEventWireSchema),
    page_info: z
      .object({
        has_next_page: z.boolean(),
        has_previous_page: z.boolean(),
        start_cursor: z.string().nullable(),
        end_cursor: z.string().nullable(),
      })
      .strict(),
    snapshot_at: instant,
    redaction: z
      .object({
        policy_version: z.string().min(1),
        omitted_field_classes: z.array(z.string().max(128)),
      })
      .strict(),
    scope: auditScope,
    request_id: id,
    contract_version: z.literal("v1"),
  })
  .strict();

export const auditEventEnvelopeWireSchema = z
  .object({
    data: auditEventWireSchema,
    scope: auditScope,
    request_id: id,
    contract_version: z.literal("v1"),
  })
  .strict();

export const auditIntegrityWireSchema = z
  .object({
    data: z
      .object({
        status: z.enum(["PASSED", "FAILED"]),
        version: z.string().min(1).max(128),
        checked_at: instant,
        checked_event_count: safeInteger,
        checked_chain_count: safeInteger,
        verified_through: instant.nullable(),
      })
      .strict(),
    scope: auditScope,
    request_id: id,
    contract_version: z.literal("v1"),
  })
  .strict();

export const auditRetentionPolicyWireSchema = z
  .object({
    scope: auditScope,
    policy_version: z.number().int().positive(),
    standard_days: z.number().int().min(30).max(3650),
    security_days: z.number().int().min(90).max(3650),
    etag: z.string().min(3).max(256),
    updated_by: id,
    updated_at: instant,
  })
  .strict();

export const auditLegalHoldWireSchema = z
  .object({
    hold_id: id,
    scope: auditScope,
    reason: z.string().min(3).max(2000),
    occurred_from: instant,
    occurred_to: instant,
    status: z.enum(["ACTIVE", "RELEASED"]),
    created_by: id,
    created_at: instant,
    released_by: id.nullable(),
    released_at: instant.nullable(),
  })
  .strict();

const auditExportArtifactWireSchema = z
  .object({
    media_type: z.literal("application/x-ndjson"),
    sha256: z.string().regex(/^[0-9a-f]{64}$/),
    size_bytes: decimal,
  })
  .strict();

export const auditExportJobWireSchema = z
  .object({
    job_id: id,
    scope: auditScope,
    status: z.enum(["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]),
    progress: z
      .object({
        exported_event_count: z.number().int().nonnegative(),
        scanned_page_count: z.number().int().nonnegative(),
      })
      .strict(),
    occurred_from: instant,
    occurred_to: instant,
    created_by: id,
    created_at: instant,
    updated_at: instant,
    artifact: auditExportArtifactWireSchema.nullable(),
    error_code: z.string().max(128).nullable(),
    error_message: z.string().max(512).nullable(),
  })
  .strict();

export const auditExportDownloadAuthorizationWireSchema = z
  .object({
    job_id: id,
    download_url: z.string().url().max(16_384),
    expires_at: instant,
    artifact: auditExportArtifactWireSchema,
  })
  .strict();

export type AuditEventWire = z.infer<typeof auditEventWireSchema>;
export type AuditBootstrapWire = z.infer<typeof auditBootstrapWireSchema>;
export type AuditFacetsWire = z.infer<typeof auditFacetsWireSchema>;
export type AuditEventPageWire = z.infer<typeof auditEventPageWireSchema>;
export type AuditEventEnvelopeWire = z.infer<
  typeof auditEventEnvelopeWireSchema
>;
export type AuditIntegrityWire = z.infer<typeof auditIntegrityWireSchema>;
export type AuditRetentionPolicyWire = z.infer<
  typeof auditRetentionPolicyWireSchema
>;
export type AuditLegalHoldWire = z.infer<typeof auditLegalHoldWireSchema>;
export type AuditExportJobWire = z.infer<typeof auditExportJobWireSchema>;
export type AuditExportDownloadAuthorizationWire = z.infer<
  typeof auditExportDownloadAuthorizationWireSchema
>;
