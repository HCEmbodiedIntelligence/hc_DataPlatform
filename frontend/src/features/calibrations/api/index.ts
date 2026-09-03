import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import type { CalibrationSet } from "../../../entities/calibration";
import type { components } from "../../../shared/api/generated/platform";
import { request } from "../../../shared/api/http-client";
import { makeQueryKey } from "../../../shared/api/query-keys";
import { parseWire } from "../../../shared/api/validate";
import { useShellStore } from "../../../shared/scope/shell-store";

const id = z.string().min(1).max(128);
const uint64 = z.string().regex(/^(0|[1-9]\d*)$/u);
const blockedReasonSchema = z
  .object({ code: z.string(), message: z.string() })
  .strict();
const scopeSchema = z
  .object({ project_id: id, region_code: z.string() })
  .strict();
export const calibrationSetWireSchema = z
  .object({
    id,
    robot_instance_id: id,
    component_id: id.nullable(),
    version: uint64,
    snapshot_status: z.string(),
    availability: z.string().nullable(),
    content_hash: z.string().nullable(),
    validation_context_hash: z.string().nullable(),
    validation: z
      .object({
        status: z.string(),
        content_hash: z.string(),
        validation_context_hash: z.string(),
        report_id: id,
      })
      .strict()
      .nullable(),
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
export const calibrationSetsPageWireSchema = z
  .object({
    items: z.array(calibrationSetWireSchema),
    page_info: pageInfoSchema,
    snapshot_at: z.string().datetime({ offset: true }),
    scope: scopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();
export const calibrationSetEnvelopeWireSchema = z
  .object({
    data: calibrationSetWireSchema,
    scope: scopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();

const finiteNumber = z.number().finite();
const transformWireSchema = z
  .object({
    parent_frame: id,
    child_frame: id,
    translation_m: z.tuple([finiteNumber, finiteNumber, finiteNumber]),
    quaternion_xyzw: z.tuple([
      finiteNumber,
      finiteNumber,
      finiteNumber,
      finiteNumber,
    ]),
    covariance: z.array(finiteNumber).length(36).nullable(),
  })
  .strict();
const cameraIntrinsicsWireSchema = z
  .object({
    frame_id: id,
    width_px: z.number().int().positive(),
    height_px: z.number().int().positive(),
    fx_px: finiteNumber.positive(),
    fy_px: finiteNumber.positive(),
    cx_px: finiteNumber.nonnegative(),
    cy_px: finiteNumber.nonnegative(),
    distortion: z.array(finiteNumber),
  })
  .strict();
export const calibrationDocumentWireSchema = z
  .object({
    frame_transforms: z.array(transformWireSchema).min(1),
    camera_intrinsics: z.array(cameraIntrinsicsWireSchema),
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
export const calibrationValidationReportWireSchema = z
  .object({
    id,
    set_id: id,
    version: uint64,
    content_hash: z.string().regex(/^[0-9a-f]{64}$/u),
    validation_context_hash: z.string().regex(/^[0-9a-f]{64}$/u),
    status: z.enum(["PASSED", "FAILED"]),
    findings: z.array(validationFindingWireSchema),
    checked_by: id,
    checked_at: z.string().datetime({ offset: true }),
  })
  .strict();
const documentEnvelopeWireSchema = z
  .object({
    data: z
      .object({
        set_id: id,
        version: uint64,
        source: z.enum(["MANUAL", "IMPORT", "RECALIBRATION"]),
        content_hash: z.string().regex(/^[0-9a-f]{64}$/u),
        document: calibrationDocumentWireSchema,
        created_by: id,
        created_at: z.string().datetime({ offset: true }),
      })
      .strict(),
    scope: scopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();
const validationReportEnvelopeWireSchema = z
  .object({
    data: calibrationValidationReportWireSchema,
    scope: scopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();
const calibrationDatasetAssociationWireSchema = z
  .object({
    set_id: id,
    calibration_version: uint64,
    dataset_id: z.string().regex(/^dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/u),
    dataset_version_id: z
      .string()
      .regex(/^version_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/u),
    associated_by: id,
    associated_at: z.string().datetime({ offset: true }),
  })
  .strict();
const calibrationDatasetAssociationEnvelopeWireSchema = z
  .object({
    data: calibrationDatasetAssociationWireSchema,
    scope: scopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();
const calibrationDatasetAssociationPageWireSchema = z
  .object({
    items: z.array(calibrationDatasetAssociationWireSchema),
    page_info: pageInfoSchema,
    scope: scopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();

export type CalibrationDocument = z.infer<typeof calibrationDocumentWireSchema>;
export type CalibrationValidationReport = z.infer<
  typeof calibrationValidationReportWireSchema
>;
export type CreateCalibrationSetInput =
  components["schemas"]["CreateCalibrationSetRequest"];
export type RecalibrateCalibrationSetInput =
  components["schemas"]["RecalibrateCalibrationSetRequest"];
export type CalibrationDatasetAssociationInput =
  components["schemas"]["CalibrationDatasetAssociationRequest"];
export type CalibrationDatasetAssociation = z.infer<
  typeof calibrationDatasetAssociationWireSchema
>;

export function parseCalibrationDocumentInput(
  value: unknown,
): CalibrationDocument | null {
  const parsed = calibrationDocumentWireSchema.safeParse(value);
  return parsed.success ? parsed.data : null;
}

const calibrationVersionSummaryWireSchema = z
  .object({
    version: uint64,
    source: z.enum(["MANUAL", "IMPORT", "RECALIBRATION"]),
    content_hash: z.string().regex(/^[0-9a-f]{64}$/u),
    created_by: id,
    created_at: z.string().datetime({ offset: true }),
  })
  .strict();
const calibrationVersionPageWireSchema = z
  .object({
    items: z.array(calibrationVersionSummaryWireSchema),
    page_info: pageInfoSchema,
    scope: scopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();
export type CalibrationVersionSummary = z.infer<
  typeof calibrationVersionSummaryWireSchema
>;

const snapshotStatuses = ["DRAFT", "READY"] as const;
const availabilityStatuses = [
  "SCHEDULED",
  "ACTIVE",
  "EXPIRED",
  "REVOKED",
] as const;

export function adaptCalibrationSet(
  wire: z.infer<typeof calibrationSetWireSchema>,
): CalibrationSet {
  return {
    id: wire.id,
    robotId: wire.robot_instance_id,
    componentId: wire.component_id,
    version: wire.version as `${bigint}`,
    snapshotStatus: snapshotStatuses.includes(
      wire.snapshot_status as (typeof snapshotStatuses)[number],
    )
      ? (wire.snapshot_status as (typeof snapshotStatuses)[number])
      : "UNKNOWN",
    availability:
      wire.availability === null
        ? null
        : availabilityStatuses.includes(
              wire.availability as (typeof availabilityStatuses)[number],
            )
          ? (wire.availability as (typeof availabilityStatuses)[number])
          : "UNKNOWN",
    contentHash: wire.content_hash,
    validationContextHash: wire.validation_context_hash,
    validation: wire.validation
      ? {
          status: ["PASSED", "FAILED", "STALE"].includes(wire.validation.status)
            ? (wire.validation.status as "PASSED" | "FAILED" | "STALE")
            : "UNKNOWN",
          contentHash: wire.validation.content_hash,
          validationContextHash: wire.validation.validation_context_hash,
          reportId: wire.validation.report_id,
        }
      : null,
    etag: wire.etag,
    allowedActions: wire.allowed_actions,
    blockedReasons: wire.blocked_reasons,
  };
}

function useCalibrationScope(): {
  readonly projectId: string | null;
  readonly regionCode: string | null;
} {
  const scope = useShellStore((state) => state.scope);
  return {
    projectId: scope?.projectId ?? null,
    regionCode: scope?.regionCode ?? null,
  };
}

export function useCalibrationSets(
  filters: Readonly<Record<string, string>> = {},
) {
  const { projectId, regionCode } = useCalibrationScope();
  return useQuery({
    queryKey: makeQueryKey("calibrations", "sets", filters),
    enabled: Boolean(projectId && regionCode),
    staleTime: 30_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: "GET",
        path: `/projects/${encodeURIComponent(projectId ?? "")}/regions/${encodeURIComponent(regionCode ?? "")}/calibration-sets`,
        query: filters,
        signal,
      });
      const page = parseWire(calibrationSetsPageWireSchema, raw, {
        endpoint: "calibrationListSets",
      });
      return {
        items: page.items.map(adaptCalibrationSet),
        pageInfo: page.page_info,
        snapshotAt: page.snapshot_at,
      };
    },
  });
}

export function useCalibrationSet(setId: string | null) {
  const { projectId, regionCode } = useCalibrationScope();
  return useQuery({
    queryKey: makeQueryKey("calibrations", "set", setId),
    enabled: Boolean(projectId && regionCode && setId),
    staleTime: 10_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: "GET",
        path: `/projects/${encodeURIComponent(projectId ?? "")}/regions/${encodeURIComponent(regionCode ?? "")}/calibration-sets/${encodeURIComponent(setId ?? "")}`,
        signal,
      });
      return adaptCalibrationSet(
        parseWire(calibrationSetEnvelopeWireSchema, raw, {
          endpoint: "calibrationGetSet",
        }).data,
      );
    },
  });
}

export function useCalibrationVersionDocument(
  setId: string | null,
  version: string | null,
) {
  const { projectId, regionCode } = useCalibrationScope();
  return useQuery({
    queryKey: makeQueryKey(
      "calibrations",
      "document",
      setId ?? undefined,
      version ?? undefined,
    ),
    enabled: Boolean(projectId && regionCode && setId && version),
    staleTime: 60_000,
    queryFn: ({ signal }) =>
      getCalibrationVersionDocument(
        { projectId: projectId ?? "", regionCode: regionCode ?? "" },
        setId ?? "",
        version ?? "",
        signal,
      ),
  });
}

export function useCalibrationVersions(setId: string | null) {
  const { projectId, regionCode } = useCalibrationScope();
  return useQuery({
    queryKey: makeQueryKey("calibrations", "versions", setId ?? undefined),
    enabled: Boolean(projectId && regionCode && setId),
    staleTime: 30_000,
    queryFn: ({ signal }) =>
      listCalibrationVersions(
        { projectId: projectId ?? "", regionCode: regionCode ?? "" },
        setId ?? "",
        signal,
      ),
  });
}

export function useCalibrationDatasetAssociations(
  setId: string | null,
  version: string | null,
) {
  const { projectId, regionCode } = useCalibrationScope();
  return useQuery({
    queryKey: makeQueryKey(
      "calibrations",
      "dataset-associations",
      setId ?? undefined,
      version ?? undefined,
    ),
    enabled: Boolean(projectId && regionCode && setId && version),
    staleTime: 30_000,
    queryFn: ({ signal }) =>
      listCalibrationDatasetAssociations(
        { projectId: projectId ?? "", regionCode: regionCode ?? "" },
        setId ?? "",
        version ?? "",
        signal,
      ),
  });
}

export function useCalibrationValidationReport(reportId: string | null) {
  const { projectId, regionCode } = useCalibrationScope();
  return useQuery({
    queryKey: makeQueryKey(
      "calibrations",
      "validation-report",
      reportId ?? undefined,
    ),
    enabled: Boolean(projectId && regionCode && reportId),
    staleTime: 60_000,
    queryFn: ({ signal }) =>
      getCalibrationValidationReport(
        { projectId: projectId ?? "", regionCode: regionCode ?? "" },
        reportId ?? "",
        signal,
      ),
  });
}

export interface CalibrationPublishIntent {
  readonly setId: string;
  readonly version: string;
  readonly etag: string;
  readonly idempotencyKey: string;
  readonly preflightToken: string;
}

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

export interface CalibrationPreflightIntent {
  readonly setId: string;
  readonly version: string;
  readonly etag: string;
  readonly expectedHash: string;
  readonly validationContextHash: string;
  readonly validationReportId: string;
  readonly changeSummary: string;
  readonly idempotencyKey: string;
}

export interface CalibrationScope {
  readonly projectId: string;
  readonly regionCode: string;
}

export async function createCalibrationSet(
  scope: CalibrationScope,
  input: CreateCalibrationSetInput,
  idempotencyKey: string,
): Promise<CalibrationSet> {
  const raw = await request<unknown>({
    method: "POST",
    path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/calibration-sets`,
    body: input,
    idempotencyKey,
  });
  return adaptCalibrationSet(
    parseWire(calibrationSetEnvelopeWireSchema, raw, {
      endpoint: "calibrationCreateSet",
    }).data,
  );
}

export async function getCalibrationVersionDocument(
  scope: CalibrationScope,
  setId: string,
  version: string,
  signal?: AbortSignal,
) {
  const raw = await request<unknown>({
    method: "GET",
    path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/calibration-sets/${encodeURIComponent(setId)}/versions/${encodeURIComponent(version)}/document`,
    signal,
  });
  return parseWire(documentEnvelopeWireSchema, raw, {
    endpoint: "calibrationGetVersionDocument",
  }).data;
}

export async function listCalibrationVersions(
  scope: CalibrationScope,
  setId: string,
  signal?: AbortSignal,
): Promise<readonly CalibrationVersionSummary[]> {
  const raw = await request<unknown>({
    method: "GET",
    path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/calibration-sets/${encodeURIComponent(setId)}/versions`,
    signal,
  });
  return parseWire(calibrationVersionPageWireSchema, raw, {
    endpoint: "calibrationListVersions",
  }).items;
}

export async function recalibrateCalibrationSet(
  scope: CalibrationScope,
  setId: string,
  etag: string,
  input: RecalibrateCalibrationSetInput,
  idempotencyKey: string,
): Promise<CalibrationSet> {
  const raw = await request<unknown>({
    method: "POST",
    path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/calibration-sets/${encodeURIComponent(setId)}/versions`,
    body: input,
    ifMatch: etag,
    idempotencyKey,
  });
  return adaptCalibrationSet(
    parseWire(calibrationSetEnvelopeWireSchema, raw, {
      endpoint: "calibrationRecalibrateSet",
    }).data,
  );
}

export async function listCalibrationDatasetAssociations(
  scope: CalibrationScope,
  setId: string,
  version: string,
  signal?: AbortSignal,
): Promise<readonly CalibrationDatasetAssociation[]> {
  const raw = await request<unknown>({
    method: "GET",
    path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/calibration-sets/${encodeURIComponent(setId)}/versions/${encodeURIComponent(version)}/dataset-associations`,
    signal,
  });
  return parseWire(calibrationDatasetAssociationPageWireSchema, raw, {
    endpoint: "calibrationListDatasetAssociations",
  }).items;
}

export async function associateCalibrationDatasetVersion(
  scope: CalibrationScope,
  setId: string,
  version: string,
  etag: string,
  input: CalibrationDatasetAssociationInput,
  idempotencyKey: string,
): Promise<CalibrationDatasetAssociation> {
  const raw = await request<unknown>({
    method: "POST",
    path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/calibration-sets/${encodeURIComponent(setId)}/versions/${encodeURIComponent(version)}/dataset-associations`,
    body: input,
    ifMatch: etag,
    idempotencyKey,
  });
  return parseWire(calibrationDatasetAssociationEnvelopeWireSchema, raw, {
    endpoint: "calibrationAssociateDatasetVersion",
  }).data;
}

export async function getCalibrationValidationReport(
  scope: CalibrationScope,
  reportId: string,
  signal?: AbortSignal,
) {
  const raw = await request<unknown>({
    method: "GET",
    path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/calibration-validation-reports/${encodeURIComponent(reportId)}`,
    signal,
  });
  return parseWire(validationReportEnvelopeWireSchema, raw, {
    endpoint: "calibrationGetValidationReport",
  }).data;
}

export async function validateCalibrationVersion(
  scope: CalibrationScope,
  intent: Pick<
    CalibrationPublishIntent,
    "setId" | "version" | "etag" | "idempotencyKey"
  >,
): Promise<CalibrationValidationReport> {
  const raw = await request<unknown>({
    method: "POST",
    path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/calibration-sets/${encodeURIComponent(intent.setId)}/versions/${encodeURIComponent(intent.version)}:validate`,
    ifMatch: intent.etag,
    idempotencyKey: intent.idempotencyKey,
  });
  return parseWire(validationReportEnvelopeWireSchema, raw, {
    endpoint: "calibrationValidateVersion",
  }).data;
}

export async function preflightCalibrationPublish(
  scope: CalibrationScope,
  intent: CalibrationPreflightIntent,
) {
  const raw = await request<unknown>({
    method: "POST",
    path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/calibration-sets/${encodeURIComponent(intent.setId)}/versions/${encodeURIComponent(intent.version)}:preflight-publish`,
    body: {
      expected_hash: intent.expectedHash,
      expected_etag: intent.etag,
      validation_report_id: intent.validationReportId,
      compatibility_check_id: null,
      change_summary: intent.changeSummary,
      acknowledge_warning_codes: [],
      validation_context_hash: intent.validationContextHash,
    },
    ifMatch: intent.etag,
    idempotencyKey: intent.idempotencyKey,
  });
  return parseWire(preflightWireSchema, raw, {
    endpoint: "calibrationPreflightPublish",
  }).data;
}

export async function publishCalibration(
  scope: CalibrationScope,
  intent: CalibrationPublishIntent,
): Promise<CalibrationSet> {
  const raw = await request<unknown>({
    method: "POST",
    path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/calibration-sets/${encodeURIComponent(intent.setId)}/versions/${encodeURIComponent(intent.version)}:publish`,
    body: { preflight_token: intent.preflightToken },
    ifMatch: intent.etag,
    idempotencyKey: intent.idempotencyKey,
  });
  return adaptCalibrationSet(
    parseWire(calibrationSetEnvelopeWireSchema, raw, {
      endpoint: "calibrationPublish",
    }).data,
  );
}

export function usePreflightCalibrationPublish() {
  const { projectId, regionCode } = useCalibrationScope();
  return useMutation({
    mutationFn: (intent: CalibrationPreflightIntent) =>
      preflightCalibrationPublish(
        { projectId: projectId ?? "", regionCode: regionCode ?? "" },
        intent,
      ),
    gcTime: 0,
  });
}

export function useCreateCalibrationSet() {
  const { projectId, regionCode } = useCalibrationScope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      input,
      idempotencyKey,
    }: {
      input: CreateCalibrationSetInput;
      idempotencyKey: string;
    }) =>
      createCalibrationSet(
        { projectId: projectId ?? "", regionCode: regionCode ?? "" },
        input,
        idempotencyKey,
      ),
    onSuccess: (created) => {
      client.setQueryData(
        makeQueryKey("calibrations", "set", created.id),
        created,
      );
      void client.invalidateQueries({
        queryKey: makeQueryKey("calibrations", "sets", {}),
      });
    },
  });
}

export function useValidateCalibrationVersion() {
  const { projectId, regionCode } = useCalibrationScope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (
      intent: Pick<
        CalibrationPublishIntent,
        "setId" | "version" | "etag" | "idempotencyKey"
      >,
    ) =>
      validateCalibrationVersion(
        { projectId: projectId ?? "", regionCode: regionCode ?? "" },
        intent,
      ),
    onSuccess: (report, intent) => {
      client.setQueryData(
        makeQueryKey("calibrations", "validation-report", report.id),
        report,
      );
      void client.invalidateQueries({
        queryKey: makeQueryKey("calibrations", "set", intent.setId),
      });
      void client.invalidateQueries({
        queryKey: makeQueryKey("calibrations", "sets", {}),
      });
    },
  });
}

export function useRecalibrateCalibrationSet() {
  const { projectId, regionCode } = useCalibrationScope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      setId,
      etag,
      input,
      idempotencyKey,
    }: {
      setId: string;
      etag: string;
      input: RecalibrateCalibrationSetInput;
      idempotencyKey: string;
    }) =>
      recalibrateCalibrationSet(
        { projectId: projectId ?? "", regionCode: regionCode ?? "" },
        setId,
        etag,
        input,
        idempotencyKey,
      ),
    onSuccess: (successor, intent) => {
      client.setQueryData(
        makeQueryKey("calibrations", "set", intent.setId),
        successor,
      );
      void client.invalidateQueries({
        queryKey: makeQueryKey("calibrations", "versions", intent.setId),
      });
      void client.invalidateQueries({
        queryKey: makeQueryKey("calibrations", "document", intent.setId),
      });
      void client.invalidateQueries({
        queryKey: makeQueryKey("calibrations", "sets", {}),
      });
    },
  });
}

export function useAssociateCalibrationDatasetVersion() {
  const { projectId, regionCode } = useCalibrationScope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      setId,
      version,
      etag,
      input,
      idempotencyKey,
    }: {
      setId: string;
      version: string;
      etag: string;
      input: CalibrationDatasetAssociationInput;
      idempotencyKey: string;
    }) =>
      associateCalibrationDatasetVersion(
        { projectId: projectId ?? "", regionCode: regionCode ?? "" },
        setId,
        version,
        etag,
        input,
        idempotencyKey,
      ),
    onSuccess: (_association, intent) => {
      void client.invalidateQueries({
        queryKey: makeQueryKey(
          "calibrations",
          "dataset-associations",
          intent.setId,
          intent.version,
        ),
      });
    },
  });
}

export function usePublishCalibration() {
  const { projectId, regionCode } = useCalibrationScope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: CalibrationPublishIntent) =>
      publishCalibration(
        { projectId: projectId ?? "", regionCode: regionCode ?? "" },
        intent,
      ),
    onSuccess: (published, intent) => {
      client.setQueryData(
        makeQueryKey("calibrations", "set", intent.setId),
        published,
      );
      void client.invalidateQueries({
        queryKey: makeQueryKey("calibrations", "set", intent.setId),
      });
      void client.invalidateQueries({
        queryKey: makeQueryKey("calibrations", "sets", {}),
      });
    },
  });
}
