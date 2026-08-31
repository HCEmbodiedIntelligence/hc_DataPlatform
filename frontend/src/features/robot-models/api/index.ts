import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import type {
  RobotModel,
  RobotModelVersion,
} from "../../../entities/robot-model";
import { request } from "../../../shared/api/http-client";
import type {
  components,
  operations,
} from "../../../shared/api/generated/platform";
import { makeQueryKey } from "../../../shared/api/query-keys";
import { parseWire } from "../../../shared/api/validate";
import { useShellStore } from "../../../shared/scope/shell-store";
import { getRobotBootstrap } from "../../robots/api";

const id = z.string().min(1).max(128);
const blockedReasonSchema = z
  .object({ code: z.string(), message: z.string() })
  .strict();
const scopeSchema = z
  .object({ organization_id: id, project_id: id.optional() })
  .strict();
const modelWireSchema = z
  .object({
    id,
    manufacturer: z.string(),
    model_code: z.string(),
    display_name: z.string(),
    current_published_version_id: id.nullable(),
  })
  .strict();
const versionWireSchema = z
  .object({
    id,
    robot_model_id: id,
    version_label: z.string(),
    lifecycle: z.string(),
    asset_availability: z.string(),
    publish_readiness: z.string(),
    asset_manifest_hash: z.string().nullable(),
    validation_input_hash: z.string().nullable(),
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
const assetRoleSchema = z.enum([
  "URDF",
  "MESH",
  "TEXTURE",
  "CONFIG",
  "DOCUMENTATION",
]);
const assetUploadStatusSchema = z.enum([
  "UPLOADING",
  "COMPLETED",
  "CANCELLED",
  "FAILED",
]);
const assetSchema = z
  .object({
    asset_id: z.string().min(1),
    relative_path: z.string().min(1).max(1024),
    role: assetRoleSchema,
    media_type: z.string().min(1).max(128),
    size_bytes: z.number().int().positive(),
    sha256: z.string().regex(/^[0-9a-f]{64}$/u),
    created_at: z.string().datetime({ offset: true }),
  })
  .strict();
const assetPageWireSchema = z
  .object({
    items: z.array(assetSchema),
    scope: scopeSchema,
    request_id: id,
  })
  .strict();
const assetDownloadWireSchema = z
  .object({
    asset_id: id,
    download_url: z.string().min(1),
    expires_at: z.string().datetime({ offset: true }),
    sha256: z.string().regex(/^[0-9a-f]{64}$/u),
    media_type: z.string().min(1),
  })
  .strict();
const assetPartAuthorizationWireSchema = z
  .object({
    part_number: z.number().int().min(1).max(10_000),
    url: z.string().min(1),
    expires_at: z.string().datetime({ offset: true }),
  })
  .strict();
const assetPartAuthorizationPageWireSchema = z
  .object({ items: z.array(assetPartAuthorizationWireSchema) })
  .strict();
const assetUploadFileWireSchema = z
  .object({
    relative_path: z.string().min(1).max(1024),
    role: assetRoleSchema,
    media_type: z.string().min(1).max(128),
    size_bytes: z.number().int().positive(),
    sha256: z.string().regex(/^[0-9a-f]{64}$/u),
    status: assetUploadStatusSchema,
    part_size_bytes: z.number().int().positive(),
    total_parts: z.number().int().min(1).max(10_000),
    part_authorizations: z.array(assetPartAuthorizationWireSchema),
  })
  .strict();
const robotModelAssetUploadEnvelopeWireSchema = z
  .object({
    data: z
      .object({
        upload_id: z.string().min(1),
        version_id: id,
        status: assetUploadStatusSchema,
        files: z.array(assetUploadFileWireSchema),
        created_at: z.string().datetime({ offset: true }),
        updated_at: z.string().datetime({ offset: true }),
        completed_at: z
          .string()
          .datetime({ offset: true })
          .nullable()
          .optional(),
      })
      .strict(),
    scope: scopeSchema,
    request_id: id,
  })
  .strict();

export type RobotModelAsset = components["schemas"]["RobotModelAsset"];
export type RobotModelAssetDownloadAuthorization =
  components["schemas"]["RobotModelAssetDownloadAuthorization"];
type CreateRobotModelAssetUploadRequest =
  operations["createRobotModelAssetUploadSession"]["requestBody"]["content"]["application/json"];
type AuthorizeRobotModelAssetPartsRequest =
  operations["authorizeRobotModelAssetParts"]["requestBody"]["content"]["application/json"];
type CompleteRobotModelAssetFileRequest =
  operations["completeRobotModelAssetUploadFile"]["requestBody"]["content"]["application/json"];
type ReplaceRobotModelJointMappingsRequest =
  operations["replaceRobotModelJointMappings"]["requestBody"]["content"]["application/json"];
type PublishRobotModelVersionRequest =
  operations["publishRobotModelVersion"]["requestBody"]["content"]["application/json"];

export type RobotModelJointMapping =
  components["schemas"]["RobotModelJointMapping"];
export type RobotModelPublishPreflight =
  components["schemas"]["RobotModelPublishPreflight"];
export interface RobotModelBinding {
  readonly binding_id: string;
  readonly robot_id: string;
  readonly version_id: string;
  readonly status: "ACTIVE" | "SUPERSEDED" | "REVOKED";
  readonly bound_at: string;
  readonly unbound_at: string | null;
}

export interface RobotModelAssetUploadInput {
  readonly file: File;
  readonly relativePath: string;
  readonly role: components["schemas"]["RobotAssetRole"];
  readonly mediaType: string;
  readonly sha256: string;
}

export const robotModelsPageWireSchema = z
  .object({
    items: z.array(modelWireSchema),
    page_info: pageInfoSchema,
    snapshot_at: z.string().datetime({ offset: true }),
    scope: scopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();
export const robotModelVersionEnvelopeWireSchema = z
  .object({
    data: versionWireSchema,
    scope: scopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();

const lifecycles = ["DRAFT", "PUBLISHED", "DISABLED"] as const;
const availability = ["UNKNOWN", "AVAILABLE", "PARTIAL", "MISSING"] as const;
const readiness = [
  "CONFIGURATION_REQUIRED",
  "MAPPING_REQUIRED",
  "SAMPLE_VALIDATION_REQUIRED",
  "READY",
  "BLOCKED",
] as const;

export function adaptRobotModel(
  wire: z.infer<typeof modelWireSchema>,
): RobotModel {
  return {
    id: wire.id,
    manufacturer: wire.manufacturer,
    modelCode: wire.model_code,
    displayName: wire.display_name,
    currentPublishedVersionId: wire.current_published_version_id,
  };
}

export function adaptRobotModelVersion(
  wire: z.infer<typeof versionWireSchema>,
): RobotModelVersion {
  return {
    id: wire.id,
    robotModelId: wire.robot_model_id,
    versionLabel: wire.version_label,
    lifecycle: lifecycles.includes(
      wire.lifecycle as (typeof lifecycles)[number],
    )
      ? (wire.lifecycle as (typeof lifecycles)[number])
      : "UNKNOWN",
    assetAvailability: availability.includes(
      wire.asset_availability as (typeof availability)[number],
    )
      ? (wire.asset_availability as (typeof availability)[number])
      : "UNKNOWN",
    publishReadiness: readiness.includes(
      wire.publish_readiness as (typeof readiness)[number],
    )
      ? (wire.publish_readiness as (typeof readiness)[number])
      : "UNKNOWN",
    assetManifestHash: wire.asset_manifest_hash,
    validationInputHash: wire.validation_input_hash,
    etag: wire.etag,
    allowedActions: wire.allowed_actions,
    blockedReasons: wire.blocked_reasons,
  };
}

function useOrganizationId(): string | null {
  return useShellStore(
    (state) =>
      state.scope?.organizationId ??
      state.sessionOrganizations[0]?.organizationId ??
      state.sessionScopes[0]?.organizationId ??
      null,
  );
}

export function useRobotModels(
  filters: Readonly<Record<string, unknown>> = {},
) {
  const organizationId = useOrganizationId();
  return useQuery({
    queryKey: makeQueryKey("robot-models", "list", filters),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: "GET",
        path: `/organizations/${encodeURIComponent(organizationId ?? "")}/robot-models`,
        query: filters as Readonly<Record<string, string>>,
        signal,
      });
      const page = parseWire(robotModelsPageWireSchema, raw, {
        endpoint: "listRobotModels",
      });
      return {
        items: page.items.map(adaptRobotModel),
        pageInfo: page.page_info,
        snapshotAt: page.snapshot_at,
        requestId: page.request_id,
      };
    },
  });
}

export function useRobotModelVersion(versionId: string | null) {
  const organizationId = useOrganizationId();
  return useQuery({
    queryKey: makeQueryKey("robot-models", "version", versionId),
    enabled: Boolean(organizationId && versionId),
    queryFn: ({ signal }) =>
      getRobotModelVersion(organizationId!, versionId!, signal),
  });
}

export async function getRobotModelVersion(
  organizationId: string,
  versionId: string,
  signal?: AbortSignal,
): Promise<RobotModelVersion> {
  const raw = await request<unknown>({
    method: "GET",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-versions/${encodeURIComponent(versionId)}`,
    ...(signal ? { signal } : {}),
  });
  return adaptRobotModelVersion(
    parseWire(robotModelVersionEnvelopeWireSchema, raw, {
      endpoint: "getRobotModelVersion",
    }).data,
  );
}

export interface CreateRobotModelIntent {
  readonly manufacturer: string;
  readonly modelCode: string;
  readonly displayName: string;
  readonly versionLabel: string;
  readonly idempotencyKey: string;
}

export async function createRobotModel(
  organizationId: string,
  intent: CreateRobotModelIntent,
): Promise<RobotModelVersion> {
  const raw = await request<unknown>({
    method: "POST",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-models`,
    body: {
      manufacturer: intent.manufacturer,
      model_code: intent.modelCode,
      display_name: intent.displayName,
      version_label: intent.versionLabel,
    },
    idempotencyKey: intent.idempotencyKey,
    cache: "no-store",
  });
  return adaptRobotModelVersion(
    parseWire(robotModelVersionEnvelopeWireSchema, raw, {
      endpoint: "createRobotModel",
    }).data,
  );
}

export function useCreateRobotModel() {
  const organizationId = useOrganizationId();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: CreateRobotModelIntent) =>
      createRobotModel(organizationId!, intent),
    onSuccess: (version) => {
      client.setQueryData(
        makeQueryKey("robot-models", "version", version.id),
        version,
      );
      void client.invalidateQueries({ queryKey: ["robot-models"] });
    },
  });
}

export interface CreateRobotModelDraftIntent {
  readonly sourceVersionId: string;
  readonly versionLabel: string;
  readonly updateScope: "ASSETS" | "MAPPINGS";
  readonly idempotencyKey: string;
}

export async function createRobotModelDraft(
  organizationId: string,
  intent: CreateRobotModelDraftIntent,
): Promise<RobotModelVersion> {
  const raw = await request<unknown>({
    method: "POST",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-versions/${encodeURIComponent(intent.sourceVersionId)}:create-draft`,
    body: {
      version_label: intent.versionLabel,
      update_scope: intent.updateScope,
    },
    idempotencyKey: intent.idempotencyKey,
    cache: "no-store",
  });
  return adaptRobotModelVersion(
    parseWire(robotModelVersionEnvelopeWireSchema, raw, {
      endpoint: "createRobotModelDraft",
    }).data,
  );
}

export function useCreateRobotModelDraft() {
  const organizationId = useOrganizationId();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: CreateRobotModelDraftIntent) =>
      createRobotModelDraft(organizationId!, intent),
    onSuccess: (version) => {
      client.setQueryData(
        makeQueryKey("robot-models", "version", version.id),
        version,
      );
      void client.invalidateQueries({ queryKey: ["robot-models"] });
    },
  });
}

export function useRobotModelAssets(versionId: string | null) {
  const organizationId = useOrganizationId();
  return useQuery({
    queryKey: makeQueryKey("robot-models", "assets", versionId),
    enabled: Boolean(organizationId && versionId),
    staleTime: 30_000,
    queryFn: ({ signal }) =>
      listRobotModelAssets(organizationId!, versionId!, signal),
  });
}

export async function listRobotModelAssets(
  organizationId: string,
  versionId: string,
  signal?: AbortSignal,
): Promise<readonly RobotModelAsset[]> {
  const raw = await request<unknown>({
    method: "GET",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-versions/${encodeURIComponent(versionId)}/assets`,
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseWire(assetPageWireSchema, raw, {
    endpoint: "listRobotModelAssets",
  }).items;
}

export function useRobotModelAssetDownload() {
  const organizationId = useOrganizationId();
  return useMutation({
    mutationFn: (intent: Readonly<{ versionId: string; assetId: string }>) =>
      authorizeRobotModelAssetDownload(
        organizationId!,
        intent.versionId,
        intent.assetId,
      ),
    gcTime: 0,
  });
}

export async function authorizeRobotModelAssetDownload(
  organizationId: string,
  versionId: string,
  assetId: string,
  signal?: AbortSignal,
): Promise<RobotModelAssetDownloadAuthorization> {
  const raw = await request<unknown>({
    method: "GET",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-versions/${encodeURIComponent(versionId)}/assets/${encodeURIComponent(assetId)}/download`,
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseWire(assetDownloadWireSchema, raw, {
    endpoint: "authorizeRobotModelAssetDownload",
  });
}

export interface RobotModelViewerAssets {
  readonly urdfUrl: string;
  readonly urdfPath: string;
  readonly assetUrls: Readonly<Record<string, string>>;
}

/**
 * Authorize every object a URDF viewer may fetch. A signed URDF URL cannot be
 * used as a relative base for separately stored meshes or textures because
 * each object requires its own short-lived signature.
 */
export async function authorizeRobotModelViewerAssets(
  organizationId: string,
  versionId: string,
  assets: readonly RobotModelAsset[],
  signal?: AbortSignal,
): Promise<RobotModelViewerAssets> {
  const urdfAsset = assets.find((asset) => asset.role === "URDF");
  if (!urdfAsset) throw new Error("Robot model viewer requires one URDF asset");

  const viewerAssets = assets.filter(
    (asset) =>
      asset.asset_id === urdfAsset.asset_id ||
      asset.role === "MESH" ||
      asset.role === "TEXTURE",
  );
  const authorizations = await Promise.all(
    viewerAssets.map(async (asset) => ({
      asset,
      authorization: await authorizeRobotModelAssetDownload(
        organizationId,
        versionId,
        asset.asset_id,
        signal,
      ),
    })),
  );
  const urdfAuthorization = authorizations.find(
    ({ asset }) => asset.asset_id === urdfAsset.asset_id,
  );
  if (!urdfAuthorization)
    throw new Error("Robot model viewer could not authorize its URDF asset");

  return {
    urdfUrl: urdfAuthorization.authorization.download_url,
    urdfPath: urdfAsset.relative_path,
    assetUrls: Object.fromEntries(
      authorizations
        .filter(({ asset }) => asset.asset_id !== urdfAsset.asset_id)
        .map(({ asset, authorization }) => [
          asset.relative_path,
          authorization.download_url,
        ]),
    ),
  };
}

async function authorizeRobotModelAssetParts(
  organizationId: string,
  uploadId: string,
  relativePath: string,
  partNumbers: readonly number[],
): Promise<readonly z.infer<typeof assetPartAuthorizationWireSchema>[]> {
  const body = {
    relative_path: relativePath,
    part_numbers: [...partNumbers],
  } satisfies AuthorizeRobotModelAssetPartsRequest;
  const raw = await request<unknown>({
    method: "POST",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-asset-uploads/${encodeURIComponent(uploadId)}:authorize-parts`,
    body,
    cache: "no-store",
  });
  return parseWire(assetPartAuthorizationPageWireSchema, raw, {
    endpoint: "authorizeRobotModelAssetParts",
  }).items;
}

async function completeRobotModelAssetFile(
  organizationId: string,
  uploadId: string,
  relativePath: string,
  parts: readonly { readonly part_number: number; readonly etag: string }[],
) {
  const body = {
    relative_path: relativePath,
    parts: [...parts],
  } satisfies CompleteRobotModelAssetFileRequest;
  const raw = await request<unknown>({
    method: "POST",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-asset-uploads/${encodeURIComponent(uploadId)}:complete-file`,
    body,
    cache: "no-store",
  });
  return parseWire(robotModelAssetUploadEnvelopeWireSchema, raw, {
    endpoint: "completeRobotModelAssetUploadFile",
  });
}

async function putAuthorizedAssetPart(
  url: string,
  body: Blob,
): Promise<string> {
  const response = await fetch(url, {
    method: "PUT",
    body,
    cache: "no-store",
    credentials: "omit",
  });
  if (!response.ok) {
    throw createAssetTransferError("ROBOT_MODEL_ASSET_PART_UPLOAD_FAILED");
  }
  const etag = response.headers.get("ETag");
  if (!etag) {
    throw createAssetTransferError("ROBOT_MODEL_ASSET_PART_ETAG_UNAVAILABLE");
  }
  return etag.replace(/^"|"$/gu, "");
}

function createAssetTransferError(problemCode: string): Error {
  const error = new Error("资产分片传输未完成，请使用同一上传会话重试。");
  error.name = problemCode;
  return error;
}

/**
 * Ephemeral upload grants live only in this call stack.  The mutation returns
 * the completed ledger envelope, never a presigned URL or multipart id.
 */
export async function uploadRobotModelAssets(
  organizationId: string,
  versionId: string,
  files: readonly RobotModelAssetUploadInput[],
  idempotencyKey: string,
) {
  const body = {
    files: files.map((item) => ({
      relative_path: item.relativePath,
      role: item.role,
      media_type: item.mediaType,
      size_bytes: item.file.size,
      sha256: item.sha256,
    })),
  } satisfies CreateRobotModelAssetUploadRequest;
  const raw = await request<unknown>({
    method: "POST",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-versions/${encodeURIComponent(versionId)}/upload-sessions`,
    body,
    idempotencyKey,
    cache: "no-store",
  });
  const session = parseWire(robotModelAssetUploadEnvelopeWireSchema, raw, {
    endpoint: "createRobotModelAssetUploadSession",
  });
  for (const input of files) {
    const uploadedFile = session.data.files.find(
      (item) => item.relative_path === input.relativePath,
    );
    if (!uploadedFile) {
      throw createAssetTransferError(
        "ROBOT_MODEL_ASSET_UPLOAD_CONTRACT_MISMATCH",
      );
    }
    const authorizations = new Map(
      uploadedFile.part_authorizations.map((item) => [item.part_number, item]),
    );
    const completedParts: { part_number: number; etag: string }[] = [];
    for (
      let partNumber = 1;
      partNumber <= uploadedFile.total_parts;
      partNumber += 1
    ) {
      let authorization = authorizations.get(partNumber);
      if (!authorization) {
        const numbers = Array.from(
          {
            length: Math.min(256, uploadedFile.total_parts - partNumber + 1),
          },
          (_, index) => partNumber + index,
        );
        const refreshed = await authorizeRobotModelAssetParts(
          organizationId,
          session.data.upload_id,
          input.relativePath,
          numbers,
        );
        for (const item of refreshed)
          authorizations.set(item.part_number, item);
        authorization = authorizations.get(partNumber);
      }
      if (!authorization) {
        throw createAssetTransferError(
          "ROBOT_MODEL_ASSET_PART_AUTHORIZATION_MISSING",
        );
      }
      const start = (partNumber - 1) * uploadedFile.part_size_bytes;
      const etag = await putAuthorizedAssetPart(
        authorization.url,
        input.file.slice(
          start,
          Math.min(start + uploadedFile.part_size_bytes, input.file.size),
        ),
      );
      completedParts.push({ part_number: partNumber, etag });
    }
    await completeRobotModelAssetFile(
      organizationId,
      session.data.upload_id,
      input.relativePath,
      completedParts,
    );
  }
  return session.data.upload_id;
}

export function useUploadRobotModelAssets() {
  const organizationId = useOrganizationId();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (
      intent: Readonly<{
        versionId: string;
        files: readonly RobotModelAssetUploadInput[];
        idempotencyKey: string;
      }>,
    ) =>
      uploadRobotModelAssets(
        organizationId!,
        intent.versionId,
        intent.files,
        intent.idempotencyKey,
      ),
    onSuccess: (_uploadId, intent) => {
      void client.invalidateQueries({
        queryKey: makeQueryKey("robot-models", "assets", intent.versionId),
      });
      void client.invalidateQueries({
        queryKey: makeQueryKey("robot-models", "version", intent.versionId),
      });
    },
    gcTime: 0,
  });
}

const jointMappingWireSchema = z
  .object({
    source_joint_name: z.string().min(1).max(256),
    target_joint_name: z.string().min(1).max(256),
    direction: z.enum(["SAME", "INVERTED"]),
  })
  .strict();
const jointMappingPageWireSchema = z
  .object({
    items: z.array(jointMappingWireSchema),
    mapping_hash: z
      .string()
      .regex(/^[0-9a-f]{64}$/u)
      .nullable(),
    scope: scopeSchema,
    request_id: id,
  })
  .strict();
const preflightWireSchema = z
  .object({
    data: z
      .object({
        allowed: z.boolean(),
        preflight_token: z.string().min(1).nullable(),
        expires_at: z.string().datetime({ offset: true }).nullable(),
        expected_etag: z.string().min(1),
        asset_manifest_hash: z
          .string()
          .regex(/^[0-9a-f]{64}$/u)
          .nullable(),
        mapping_hash: z
          .string()
          .regex(/^[0-9a-f]{64}$/u)
          .nullable(),
        checks: z.array(
          z
            .object({
              code: z.string().min(1),
              passed: z.boolean(),
              message: z.string().min(1),
            })
            .strict(),
        ),
        blockers: z.array(blockedReasonSchema),
      })
      .strict(),
    scope: scopeSchema,
    request_id: id,
  })
  .strict();
const bindingWireSchema = z
  .object({
    binding_id: z.string().min(1),
    robot_id: id,
    version_id: id,
    status: z.enum(["ACTIVE", "SUPERSEDED", "REVOKED"]),
    bound_at: z.string().datetime({ offset: true }),
    unbound_at: z.string().datetime({ offset: true }).nullable(),
  })
  .strict();
const bindingPageWireSchema = z
  .object({
    items: z.array(bindingWireSchema),
    scope: z.object({ organization_id: id }).strict(),
    request_id: id,
  })
  .strict();
export async function listRobotModelJointMappings(
  organizationId: string,
  versionId: string,
  signal?: AbortSignal,
): Promise<readonly RobotModelJointMapping[]> {
  const raw = await request<unknown>({
    method: "GET",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-versions/${encodeURIComponent(versionId)}/joint-mappings`,
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseWire(jointMappingPageWireSchema, raw, {
    endpoint: "listRobotModelJointMappings",
  }).items;
}

export function useRobotModelJointMappings(versionId: string | null) {
  const organizationId = useOrganizationId();
  return useQuery({
    queryKey: makeQueryKey("robot-models", "joint-mappings", versionId),
    enabled: Boolean(organizationId && versionId),
    staleTime: 30_000,
    queryFn: ({ signal }) =>
      listRobotModelJointMappings(organizationId!, versionId!, signal),
  });
}

export async function listRobotModelBindings(
  organizationId: string,
  versionId: string,
  signal?: AbortSignal,
): Promise<readonly RobotModelBinding[]> {
  const raw = await request<unknown>({
    method: "GET",
    path: `/organizations/${encodeURIComponent(organizationId)}/robots/model-bindings`,
    scopeMode: "organization",
    scope: { organizationId },
    query: { version_id: versionId },
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseWire(bindingPageWireSchema, raw, {
    endpoint: "listRobotModelBindings",
  }).items;
}

export function useRobotModelBindings(versionId: string | null) {
  const organizationId = useOrganizationId();
  return useQuery({
    queryKey: makeQueryKey("robot-models", "bindings", versionId),
    enabled: Boolean(organizationId && versionId),
    staleTime: 30_000,
    queryFn: ({ signal }) =>
      listRobotModelBindings(organizationId!, versionId!, signal),
  });
}

export interface RobotBindingTarget {
  readonly robotId: string;
}

export async function loadRobotBindingTarget(
  target: RobotBindingTarget,
): Promise<Readonly<{ robotId: string; displayName: string; etag: string }>> {
  const robot = await getRobotBootstrap(target.robotId);
  return {
    robotId: robot.id,
    displayName: robot.displayName,
    etag: robot.etag,
  };
}

export function useLoadRobotBindingTarget() {
  return useMutation({
    mutationFn: loadRobotBindingTarget,
    gcTime: 0,
  });
}

export interface BindRobotModelVersionIntent {
  readonly versionId: string;
  readonly robotId: string;
  readonly robotEtag: string;
  readonly idempotencyKey: string;
}

export async function bindRobotModelVersion(
  organizationId: string,
  intent: BindRobotModelVersionIntent,
): Promise<RobotModelBinding> {
  const body = { version_id: intent.versionId, robot_etag: intent.robotEtag };
  const raw = await request<unknown>({
    method: "POST",
    path: `/organizations/${encodeURIComponent(organizationId)}/robots/${encodeURIComponent(intent.robotId)}/model-bindings`,
    scopeMode: "organization",
    scope: { organizationId },
    body,
    idempotencyKey: intent.idempotencyKey,
    cache: "no-store",
  });
  return parseWire(bindingWireSchema, raw, {
    endpoint: "bindOrganizationRobotModel",
  });
}

export function useBindRobotModelVersion() {
  const organizationId = useOrganizationId();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: BindRobotModelVersionIntent) =>
      organizationId
        ? bindRobotModelVersion(organizationId, intent)
        : Promise.reject(new Error("当前会话没有可用的组织范围。")),
    onSuccess: (_binding, intent) => {
      void client.invalidateQueries({
        queryKey: makeQueryKey("robot-models", "bindings", intent.versionId),
      });
    },
    gcTime: 0,
  });
}

export interface ReplaceRobotModelJointMappingsIntent {
  readonly versionId: string;
  readonly etag: string;
  readonly mappings: readonly RobotModelJointMapping[];
  readonly idempotencyKey: string;
}

export function useReplaceRobotModelJointMappings() {
  const organizationId = useOrganizationId();
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (intent: ReplaceRobotModelJointMappingsIntent) => {
      const body = {
        mappings: [...intent.mappings],
      } satisfies ReplaceRobotModelJointMappingsRequest;
      const raw = await request<unknown>({
        method: "PUT",
        path: `/organizations/${encodeURIComponent(organizationId ?? "")}/robot-model-versions/${encodeURIComponent(intent.versionId)}/joint-mappings`,
        body,
        ifMatch: intent.etag,
        idempotencyKey: intent.idempotencyKey,
        cache: "no-store",
      });
      return adaptRobotModelVersion(
        parseWire(robotModelVersionEnvelopeWireSchema, raw, {
          endpoint: "replaceRobotModelJointMappings",
        }).data,
      );
    },
    onSuccess: (version, intent) => {
      client.setQueryData(
        makeQueryKey("robot-models", "version", intent.versionId),
        version,
      );
      void client.invalidateQueries({
        queryKey: makeQueryKey(
          "robot-models",
          "joint-mappings",
          intent.versionId,
        ),
      });
    },
    gcTime: 0,
  });
}

export interface RobotModelPublishPreflightIntent {
  readonly versionId: string;
  readonly etag: string;
  readonly idempotencyKey: string;
}

export async function preflightRobotModelPublish(
  organizationId: string,
  intent: RobotModelPublishPreflightIntent,
): Promise<RobotModelPublishPreflight> {
  const raw = await request<unknown>({
    method: "POST",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-versions/${encodeURIComponent(intent.versionId)}:preflight-publish`,
    ifMatch: intent.etag,
    idempotencyKey: intent.idempotencyKey,
    cache: "no-store",
  });
  return parseWire(preflightWireSchema, raw, {
    endpoint: "preflightRobotModelPublish",
  }).data;
}

export function usePreflightRobotModelPublish() {
  const organizationId = useOrganizationId();
  return useMutation({
    mutationFn: (intent: RobotModelPublishPreflightIntent) =>
      preflightRobotModelPublish(organizationId!, intent),
    gcTime: 0,
  });
}

export interface RobotModelDangerousIntent {
  readonly versionId: string;
  readonly etag: string;
  readonly idempotencyKey: string;
  readonly preflightToken: string;
}

export function usePublishRobotModelVersion() {
  const organizationId = useOrganizationId();
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (intent: RobotModelDangerousIntent) => {
      const body = {
        preflight_token: intent.preflightToken,
      } satisfies PublishRobotModelVersionRequest;
      const raw = await request<unknown>({
        method: "POST",
        path: `/organizations/${encodeURIComponent(organizationId ?? "")}/robot-model-versions/${encodeURIComponent(intent.versionId)}:publish`,
        body,
        idempotencyKey: intent.idempotencyKey,
        ifMatch: intent.etag,
        cache: "no-store",
      });
      return adaptRobotModelVersion(
        parseWire(robotModelVersionEnvelopeWireSchema, raw, {
          endpoint: "publishRobotModelVersion",
        }).data,
      );
    },
    onSuccess: (version, intent) => {
      client.setQueryData(
        makeQueryKey("robot-models", "version", intent.versionId),
        version,
      );
      void client.invalidateQueries({
        queryKey: makeQueryKey("robot-models", "list", {}),
      });
    },
  });
}

export interface UploadSessionIntent {
  readonly versionId: string;
  readonly files: CreateRobotModelAssetUploadRequest["files"];
  readonly idempotencyKey: string;
}

/** Ephemeral grants are returned directly to the caller and never enter Query Cache. */
export function useCreateRobotAssetUploadSession() {
  const organizationId = useOrganizationId();
  return useMutation({
    mutationFn: async (intent: UploadSessionIntent) => {
      const raw = await request<unknown>({
        method: "POST",
        path: `/organizations/${encodeURIComponent(organizationId ?? "")}/robot-model-versions/${encodeURIComponent(intent.versionId)}/upload-sessions`,
        body: {
          files: intent.files,
        } satisfies CreateRobotModelAssetUploadRequest,
        idempotencyKey: intent.idempotencyKey,
        cache: "no-store",
      });
      return parseWire(robotModelAssetUploadEnvelopeWireSchema, raw, {
        endpoint: "createRobotModelAssetUploadSession",
      });
    },
    gcTime: 0,
  });
}
