import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import type { DataSchemaVersion } from "../../../entities/data-schema";
import type {
  components,
  operations,
} from "../../../shared/api/generated/platform";
import { request } from "../../../shared/api/http-client";
import { makeQueryKey } from "../../../shared/api/query-keys";
import { parseWire } from "../../../shared/api/validate";
import { useShellStore } from "../../../shared/scope/shell-store";
import { projectCompatibilityResult } from "../registry-rules";
import type { DataSchemaRouteParams } from "../routing";

const id = z.string().min(1).max(128);
const version = z.string().regex(/^[1-9]\d*$/u);
const blockedReasonSchema = z
  .object({ code: z.string(), message: z.string() })
  .strict();
const scopeSchema = z.object({ organization_id: id }).strict();
export const dataSchemaVersionWireSchema = z
  .object({
    schema_id: id,
    family_id: id,
    schema_version: version,
    display_name: z.string(),
    logical_type: z.string(),
    status: z.string(),
    compatibility_mode: z.string(),
    compatibility_result: z.string().nullable(),
    schema_hash: z
      .object({
        algorithm: z.string(),
        canonicalization_version: z.string(),
        value: z.string(),
      })
      .nullable(),
    schema_definition: z.record(z.string(), z.unknown()),
    etag: z.string(),
    allowed_actions: z.array(z.string()),
    blocked_reasons: z.array(blockedReasonSchema),
  })
  .strict();
const pageInfoSchema = z
  .object({
    has_next_page: z.boolean(),
    has_previous_page: z.boolean(),
    start_cursor: z.string().nullable(),
    end_cursor: z.string().nullable(),
  })
  .strict();
export const dataSchemasPageWireSchema = z
  .object({
    items: z.array(dataSchemaVersionWireSchema),
    page_info: pageInfoSchema,
    snapshot_at: z.string().datetime({ offset: true }),
    scope: scopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();
export const dataSchemaEnvelopeWireSchema = z
  .object({
    data: dataSchemaVersionWireSchema,
    scope: scopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();
const dataSchemaDatasetReferenceScopeWireSchema = z
  .object({
    organization_id: id,
    project_id: id,
    region_code: z.string().min(1).max(64),
  })
  .strict();
export const dataSchemaDatasetReferenceWireSchema = z
  .object({
    schema_id: id,
    schema_version: version,
    dataset_id: z.string().regex(/^dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/u),
    dataset_version_id: z
      .string()
      .regex(/^version_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/u),
    associated_by: id,
    associated_at: z.string().datetime({ offset: true }),
  })
  .strict();
const dataSchemaDatasetReferencePageWireSchema = z
  .object({
    items: z.array(dataSchemaDatasetReferenceWireSchema),
    page_info: pageInfoSchema,
    scope: dataSchemaDatasetReferenceScopeWireSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();
const dataSchemaDatasetReferenceEnvelopeWireSchema = z
  .object({
    data: dataSchemaDatasetReferenceWireSchema,
    scope: dataSchemaDatasetReferenceScopeWireSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();

const validationFindingWireSchema = z
  .object({
    code: z.string().min(1),
    severity: z.enum(["ERROR", "WARNING"]),
    message: z.string().min(1),
    path: z.string().min(1),
  })
  .strict();
export const dataSchemaValidationReportWireSchema = z
  .object({
    id,
    schema_id: id,
    schema_version: version,
    content_hash: z.string().regex(/^[0-9a-f]{64}$/u),
    compatibility_check_id: id,
    compatibility_result: z.enum(["PASSED", "FAILED"]),
    status: z.enum(["PASSED", "FAILED"]),
    findings: z.array(validationFindingWireSchema),
    checked_by: id,
    checked_at: z.string().datetime({ offset: true }),
  })
  .strict();
const validationReportEnvelopeWireSchema = z
  .object({
    data: dataSchemaValidationReportWireSchema,
    scope: scopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();

const statuses = ["DRAFT", "VALIDATING", "PUBLISHED"] as const;
const modes = ["STRICT", "BACKWARD", "FORWARD", "FULL", "MANUAL"] as const;
type RuntimeListStreamSchemasQuery = NonNullable<
  operations["listStreamSchemas"]["parameters"]["query"]
>;

/**
 * UI-safe camelCase view of the generated list query contract. The transport
 * turns keys into the formal snake_case parameter names at the boundary.
 */
export interface DataSchemaListFilters {
  readonly q?: RuntimeListStreamSchemasQuery["q"];
  readonly status?: RuntimeListStreamSchemasQuery["status"];
  readonly logicalType?: RuntimeListStreamSchemasQuery["logical_type"];
  readonly after?: RuntimeListStreamSchemasQuery["after"];
  readonly before?: RuntimeListStreamSchemasQuery["before"];
  readonly limit?: RuntimeListStreamSchemasQuery["limit"];
}

export function adaptDataSchemaVersion(
  wire: z.infer<typeof dataSchemaVersionWireSchema>,
): DataSchemaVersion {
  return {
    schemaId: wire.schema_id,
    familyId: wire.family_id,
    version: wire.schema_version as `${bigint}`,
    displayName: wire.display_name,
    logicalType: wire.logical_type,
    status: statuses.includes(wire.status as (typeof statuses)[number])
      ? (wire.status as (typeof statuses)[number])
      : "UNKNOWN",
    compatibilityMode: modes.includes(
      wire.compatibility_mode as (typeof modes)[number],
    )
      ? (wire.compatibility_mode as (typeof modes)[number])
      : "UNKNOWN",
    compatibilityResult:
      wire.compatibility_result === null
        ? null
        : projectCompatibilityResult(wire.compatibility_result),
    hash: wire.schema_hash
      ? {
          algorithm:
            wire.schema_hash.algorithm === "SHA-256" ? "SHA-256" : "UNKNOWN",
          canonicalizationVersion: wire.schema_hash.canonicalization_version,
          value: wire.schema_hash.value,
        }
      : null,
    definition: wire.schema_definition,
    etag: wire.etag,
    allowedActions: wire.allowed_actions,
    blockedReasons: wire.blocked_reasons,
  };
}

function useOrganizationId(): string | null {
  return useShellStore((state) => state.scope?.organizationId ?? null);
}

const routeResolutionEnvelopeSchema = z
  .object({
    data: z
      .object({
        canonical_query: z
          .object({
            schema_id: id,
            schema_version: version,
            component_id: id,
            detail_tab: z.enum([
              "fields",
              "encoding",
              "compatibility",
              "references",
            ]),
          })
          .strict(),
        scope: z
          .object({
            organization_id: id,
            project_id: id.optional(),
            region_code: z.string().optional(),
          })
          .strict(),
        relation_revision: z.string().min(1),
        allowed_actions: z.tuple([z.literal("VIEW")]),
        blocked_reasons: z.array(blockedReasonSchema),
      })
      .strict(),
    scope: z
      .object({
        organization_id: id,
        project_id: id.optional(),
        region_code: z.string().optional(),
      })
      .strict(),
    request_id: id,
    contract_version: z.string(),
  })
  .strict();

export function useResolveDataSchemaRoute(
  params: DataSchemaRouteParams | null,
) {
  const organizationId = useOrganizationId();
  const scope = useShellStore((state) => state.scope);
  return useQuery({
    queryKey: makeQueryKey("data-schemas", "route-resolution", params),
    enabled: Boolean(
      params && organizationId && scope?.projectId && scope.regionCode,
    ),
    retry: false,
    queryFn: async ({ signal }) => {
      if (!params || !scope?.projectId || !scope.regionCode)
        throw new Error("Data schema route scope is unavailable");
      const raw = await request<unknown>({
        method: "GET",
        path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/route-resolutions/p15-to-p17`,
        query: {
          schemaId: params.schemaId,
          schemaVersion: params.schemaVersion,
          componentId: params.componentId,
          detailTab: params.detailTab,
        },
        signal,
      });
      return parseWire(routeResolutionEnvelopeSchema, raw, {
        endpoint: "resolveP15DataSchemaRoute",
      }).data;
    },
  });
}

export function useDataSchemas(filters: DataSchemaListFilters = {}) {
  const organizationId = useOrganizationId();
  return useQuery({
    queryKey: makeQueryKey("data-schemas", "list", { ...filters }),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: "GET",
        path: `/organizations/${encodeURIComponent(organizationId ?? "")}/stream-schemas`,
        query: { ...filters },
        signal,
      });
      const page = parseWire(dataSchemasPageWireSchema, raw, {
        endpoint: "dataSchemaListStreamSchemas",
      });
      return {
        items: page.items.map(adaptDataSchemaVersion),
        pageInfo: page.page_info,
        snapshotAt: page.snapshot_at,
      };
    },
  });
}

export function useDataSchemaVersion(
  schemaId: string | null,
  schemaVersion: string | null,
) {
  const organizationId = useOrganizationId();
  return useQuery({
    queryKey: makeQueryKey("data-schemas", "version", {
      schemaId,
      schemaVersion,
    }),
    enabled: Boolean(organizationId && schemaId && schemaVersion),
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: "GET",
        path: `/organizations/${encodeURIComponent(organizationId ?? "")}/stream-schemas/${encodeURIComponent(schemaId ?? "")}/versions/${encodeURIComponent(schemaVersion ?? "")}`,
        signal,
      });
      return adaptDataSchemaVersion(
        parseWire(dataSchemaEnvelopeWireSchema, raw, {
          endpoint: "dataSchemaGetVersion",
        }).data,
      );
    },
  });
}

export function useDataSchemaDatasetReferences(
  schemaId: string | null,
  schemaVersion: string | null,
  enabled = true,
) {
  const organizationId = useOrganizationId();
  const scope = useShellStore((state) => state.scope);
  const referenceScope =
    organizationId && scope?.projectId && scope.regionCode
      ? {
          organizationId,
          projectId: scope.projectId,
          regionCode: scope.regionCode,
        }
      : null;
  const referenceIdentity = {
    ...(referenceScope ?? {}),
    ...(schemaId ? { schemaId } : {}),
    ...(schemaVersion ? { schemaVersion } : {}),
  };
  return useQuery({
    queryKey: makeQueryKey(
      "data-schemas",
      "dataset-references",
      referenceIdentity,
    ),
    enabled: Boolean(enabled && referenceScope && schemaId && schemaVersion),
    staleTime: 30_000,
    queryFn: ({ signal }) => {
      if (!referenceScope || !schemaId || !schemaVersion) {
        throw new Error("Data schema dataset reference scope is unavailable");
      }
      return listDataSchemaDatasetReferences(
        referenceScope,
        schemaId,
        schemaVersion,
        signal,
      );
    },
  });
}

export interface DataSchemaPublishIntent {
  readonly schemaId: string;
  readonly schemaVersion: string;
  readonly etag: string;
  readonly idempotencyKey: string;
  readonly preflightToken: string;
}

export type CreateStreamSchemaInput =
  components["schemas"]["CreateStreamSchemaRequest"];
export type UpdateStreamSchemaDraftInput =
  components["schemas"]["UpdateStreamSchemaDraftRequest"];
export type DataSchemaDatasetReferenceInput =
  components["schemas"]["DataSchemaDatasetReferenceRequest"];
export type DataSchemaDatasetReference = z.infer<
  typeof dataSchemaDatasetReferenceWireSchema
>;
export type DataSchemaValidationReport = z.infer<
  typeof dataSchemaValidationReportWireSchema
>;

const preflightWireSchema = z
  .object({
    data: z
      .object({
        allowed: z.boolean(),
        preflight_token: z.string().min(1).nullable(),
        expires_at: z.string().datetime({ offset: true }).nullable(),
        resource_revision: z.string(),
        impacts: z.array(
          z.object({ code: z.string(), message: z.string() }).strict(),
        ),
        warnings: z.array(blockedReasonSchema),
        blockers: z.array(blockedReasonSchema),
      })
      .strict(),
    scope: scopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();

export interface DataSchemaPreflightIntent {
  readonly schemaId: string;
  readonly schemaVersion: string;
  readonly etag: string;
  readonly expectedHash: string;
  readonly validationReportId: string;
  readonly compatibilityCheckId: string;
  readonly changeSummary: string;
  readonly idempotencyKey: string;
}

function dataSchemaPath(
  organizationId: string,
  schemaId: string,
  schemaVersion: string,
): string {
  return `/organizations/${encodeURIComponent(organizationId)}/stream-schemas/${encodeURIComponent(schemaId)}/versions/${encodeURIComponent(schemaVersion)}`;
}

interface DataSchemaDatasetReferenceScope {
  readonly organizationId: string;
  readonly projectId: string;
  readonly regionCode: string;
}

function dataSchemaDatasetReferencePath(
  scope: DataSchemaDatasetReferenceScope,
  schemaId: string,
  schemaVersion: string,
): string {
  return `/organizations/${encodeURIComponent(scope.organizationId)}/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/stream-schemas/${encodeURIComponent(schemaId)}/versions/${encodeURIComponent(schemaVersion)}/dataset-references`;
}

export async function listDataSchemaDatasetReferences(
  scope: DataSchemaDatasetReferenceScope,
  schemaId: string,
  schemaVersion: string,
  signal?: AbortSignal,
): Promise<readonly DataSchemaDatasetReference[]> {
  const raw = await request<unknown>({
    method: "GET",
    path: dataSchemaDatasetReferencePath(scope, schemaId, schemaVersion),
    signal,
  });
  return parseWire(dataSchemaDatasetReferencePageWireSchema, raw, {
    endpoint: "dataSchemaListDatasetReferences",
  }).items;
}

export async function associateDataSchemaDatasetReference(
  scope: DataSchemaDatasetReferenceScope,
  intent: {
    readonly schemaId: string;
    readonly schemaVersion: string;
    readonly etag: string;
    readonly input: DataSchemaDatasetReferenceInput;
    readonly idempotencyKey: string;
  },
): Promise<DataSchemaDatasetReference> {
  const raw = await request<unknown>({
    method: "POST",
    path: dataSchemaDatasetReferencePath(
      scope,
      intent.schemaId,
      intent.schemaVersion,
    ),
    body: intent.input,
    ifMatch: intent.etag,
    idempotencyKey: intent.idempotencyKey,
  });
  return parseWire(dataSchemaDatasetReferenceEnvelopeWireSchema, raw, {
    endpoint: "dataSchemaAssociateDatasetReference",
  }).data;
}

export async function createStreamSchema(
  organizationId: string,
  input: CreateStreamSchemaInput,
  idempotencyKey: string,
  source: "MANUAL" | "IMPORT" = "MANUAL",
): Promise<DataSchemaVersion> {
  const raw = await request<unknown>({
    method: "POST",
    path:
      source === "IMPORT"
        ? `/organizations/${encodeURIComponent(organizationId)}/stream-schemas:import`
        : `/organizations/${encodeURIComponent(organizationId)}/stream-schemas`,
    body: input,
    idempotencyKey,
  });
  return adaptDataSchemaVersion(
    parseWire(dataSchemaEnvelopeWireSchema, raw, {
      endpoint:
        source === "IMPORT" ? "importStreamSchema" : "createStreamSchema",
    }).data,
  );
}

export async function updateStreamSchemaDraft(
  organizationId: string,
  schemaId: string,
  schemaVersion: string,
  etag: string,
  input: UpdateStreamSchemaDraftInput,
  idempotencyKey: string,
): Promise<DataSchemaVersion> {
  const raw = await request<unknown>({
    method: "PATCH",
    path: dataSchemaPath(organizationId, schemaId, schemaVersion),
    body: input,
    ifMatch: etag,
    idempotencyKey,
  });
  return adaptDataSchemaVersion(
    parseWire(dataSchemaEnvelopeWireSchema, raw, {
      endpoint: "updateStreamSchemaDraft",
    }).data,
  );
}

export async function validateStreamSchemaVersion(
  organizationId: string,
  intent: Pick<
    DataSchemaPublishIntent,
    "schemaId" | "schemaVersion" | "etag" | "idempotencyKey"
  >,
): Promise<DataSchemaValidationReport> {
  const raw = await request<unknown>({
    method: "POST",
    path: `${dataSchemaPath(organizationId, intent.schemaId, intent.schemaVersion)}:validate`,
    ifMatch: intent.etag,
    idempotencyKey: intent.idempotencyKey,
  });
  return parseWire(validationReportEnvelopeWireSchema, raw, {
    endpoint: "validateStreamSchemaVersion",
  }).data;
}

export async function preflightDataSchemaPublish(
  organizationId: string,
  intent: DataSchemaPreflightIntent,
) {
  const raw = await request<unknown>({
    method: "POST",
    path: `${dataSchemaPath(organizationId, intent.schemaId, intent.schemaVersion)}:preflight-publish`,
    body: {
      expected_hash: intent.expectedHash,
      expected_etag: intent.etag,
      validation_report_id: intent.validationReportId,
      compatibility_check_id: intent.compatibilityCheckId,
      change_summary: intent.changeSummary,
      acknowledge_warning_codes: [],
    },
    ifMatch: intent.etag,
    idempotencyKey: intent.idempotencyKey,
  });
  return parseWire(preflightWireSchema, raw, {
    endpoint: "preflightDataSchemaPublish",
  }).data;
}

export async function publishDataSchema(
  organizationId: string,
  intent: DataSchemaPublishIntent,
): Promise<DataSchemaVersion> {
  const raw = await request<unknown>({
    method: "POST",
    path: `${dataSchemaPath(organizationId, intent.schemaId, intent.schemaVersion)}:publish`,
    body: { preflight_token: intent.preflightToken },
    ifMatch: intent.etag,
    idempotencyKey: intent.idempotencyKey,
  });
  return adaptDataSchemaVersion(
    parseWire(dataSchemaEnvelopeWireSchema, raw, {
      endpoint: "publishDataSchemaVersion",
    }).data,
  );
}

export function usePreflightDataSchemaPublish() {
  const organizationId = useOrganizationId();
  return useMutation({
    mutationFn: (intent: DataSchemaPreflightIntent) =>
      preflightDataSchemaPublish(organizationId ?? "", intent),
    gcTime: 0,
  });
}

export function useCreateStreamSchema() {
  const organizationId = useOrganizationId();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      input,
      idempotencyKey,
      source,
    }: {
      input: CreateStreamSchemaInput;
      idempotencyKey: string;
      source: "MANUAL" | "IMPORT";
    }) =>
      createStreamSchema(organizationId ?? "", input, idempotencyKey, source),
    onSuccess: (created) => {
      client.setQueryData(
        makeQueryKey("data-schemas", "version", {
          schemaId: created.schemaId,
          schemaVersion: created.version,
        }),
        created,
      );
      void client.invalidateQueries({ queryKey: ["data-schemas"] });
    },
  });
}

export function useUpdateStreamSchemaDraft() {
  const organizationId = useOrganizationId();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      schemaId,
      schemaVersion,
      etag,
      input,
      idempotencyKey,
    }: {
      schemaId: string;
      schemaVersion: string;
      etag: string;
      input: UpdateStreamSchemaDraftInput;
      idempotencyKey: string;
    }) =>
      updateStreamSchemaDraft(
        organizationId ?? "",
        schemaId,
        schemaVersion,
        etag,
        input,
        idempotencyKey,
      ),
    onSuccess: (updated) => {
      client.setQueryData(
        makeQueryKey("data-schemas", "version", {
          schemaId: updated.schemaId,
          schemaVersion: updated.version,
        }),
        updated,
      );
      void client.invalidateQueries({ queryKey: ["data-schemas"] });
    },
  });
}

export function useValidateStreamSchemaVersion() {
  const organizationId = useOrganizationId();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (
      intent: Pick<
        DataSchemaPublishIntent,
        "schemaId" | "schemaVersion" | "etag" | "idempotencyKey"
      >,
    ) => validateStreamSchemaVersion(organizationId ?? "", intent),
    onSuccess: (report, intent) => {
      client.setQueryData(
        makeQueryKey("data-schemas", "validation-report", report.id),
        report,
      );
      void client.invalidateQueries({
        queryKey: makeQueryKey("data-schemas", "version", {
          schemaId: intent.schemaId,
          schemaVersion: intent.schemaVersion,
        }),
      });
    },
  });
}

export function usePublishDataSchema() {
  const organizationId = useOrganizationId();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: DataSchemaPublishIntent) =>
      publishDataSchema(organizationId ?? "", intent),
    onSuccess: (_data, intent) => {
      void client.invalidateQueries({
        queryKey: makeQueryKey("data-schemas", "version", {
          schemaId: intent.schemaId,
          schemaVersion: intent.schemaVersion,
        }),
      });
      void client.invalidateQueries({ queryKey: ["data-schemas"] });
    },
  });
}

export function useAssociateDataSchemaDatasetReference() {
  const organizationId = useOrganizationId();
  const scope = useShellStore((state) => state.scope);
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: {
      schemaId: string;
      schemaVersion: string;
      etag: string;
      input: DataSchemaDatasetReferenceInput;
      idempotencyKey: string;
    }) => {
      if (!organizationId || !scope?.projectId || !scope.regionCode) {
        throw new Error("Data schema dataset reference scope is unavailable");
      }
      return associateDataSchemaDatasetReference(
        {
          organizationId,
          projectId: scope.projectId,
          regionCode: scope.regionCode,
        },
        intent,
      );
    },
    onSuccess: (_reference, intent) => {
      void client.invalidateQueries({
        queryKey: makeQueryKey("data-schemas", "dataset-references", {
          organizationId,
          projectId: scope?.projectId,
          regionCode: scope?.regionCode,
          schemaId: intent.schemaId,
          schemaVersion: intent.schemaVersion,
        }),
      });
    },
  });
}
