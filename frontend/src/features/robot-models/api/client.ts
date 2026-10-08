import { z } from "zod";
import type {
  RobotModel,
  RobotModelVersion,
} from "../../../entities/robot-model";
import { request } from "../../../shared/api/http-client";
import { createDomainError } from "../../../shared/api/domain-error";
import { createSessionScopeLease } from "../../../shared/api/session-scope-lease";
import type {
  components,
  operations,
} from "../../../shared/api/generated/platform";
import { parseWire, parseOrganizationWire } from "../../../shared/api/validate";
import { getRobotBootstrap } from "../../robots/api/client";

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

export async function getRobotModelVersion(
  organizationId: string,
  versionId: string,
  signal?: AbortSignal,
): Promise<RobotModelVersion> {
  const raw = await request<unknown>({
    method: "GET",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-versions/${encodeURIComponent(versionId)}`,
    scopeMode: "explicit",
    scope: { organizationId },
    ...(signal ? { signal } : {}),
  });
  return adaptRobotModelVersion(
    parseOrganizationWire(
      robotModelVersionEnvelopeWireSchema,
      raw,
      {
        endpoint: "getRobotModelVersion",
      },
      organizationId,
    ).data,
  );
}

export async function discardRobotModelImport(
  organizationId: string,
  versionId: string,
  fallbackVersionId?: string,
): Promise<void> {
  await request<void>({
    method: "DELETE",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-versions/${encodeURIComponent(versionId)}:discard-import`,
    scopeMode: "explicit",
    scope: { organizationId },
    query: fallbackVersionId
      ? { fallback_version_id: fallbackVersionId }
      : undefined,
    cache: "no-store",
  });
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
    scopeMode: "explicit",
    scope: { organizationId },
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
    parseOrganizationWire(
      robotModelVersionEnvelopeWireSchema,
      raw,
      {
        endpoint: "createRobotModel",
      },
      organizationId,
    ).data,
  );
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
    scopeMode: "explicit",
    scope: { organizationId },
    body: {
      version_label: intent.versionLabel,
      update_scope: intent.updateScope,
    },
    idempotencyKey: intent.idempotencyKey,
    cache: "no-store",
  });
  return adaptRobotModelVersion(
    parseOrganizationWire(
      robotModelVersionEnvelopeWireSchema,
      raw,
      {
        endpoint: "createRobotModelDraft",
      },
      organizationId,
    ).data,
  );
}

export async function listRobotModelAssets(
  organizationId: string,
  versionId: string,
  signal?: AbortSignal,
): Promise<readonly RobotModelAsset[]> {
  const raw = await request<unknown>({
    method: "GET",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-versions/${encodeURIComponent(versionId)}/assets`,
    scopeMode: "explicit",
    scope: { organizationId },
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseOrganizationWire(
    assetPageWireSchema,
    raw,
    {
      endpoint: "listRobotModelAssets",
    },
    organizationId,
  ).items;
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
    scopeMode: "explicit",
    scope: { organizationId },
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
  signal: AbortSignal,
): Promise<readonly z.infer<typeof assetPartAuthorizationWireSchema>[]> {
  const body = {
    relative_path: relativePath,
    part_numbers: [...partNumbers],
  } satisfies AuthorizeRobotModelAssetPartsRequest;
  const raw = await request<unknown>({
    method: "POST",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-asset-uploads/${encodeURIComponent(uploadId)}:authorize-parts`,
    scopeMode: "explicit",
    scope: { organizationId },
    body,
    cache: "no-store",
    signal,
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
  signal: AbortSignal,
) {
  const body = {
    relative_path: relativePath,
    parts: [...parts],
  } satisfies CompleteRobotModelAssetFileRequest;
  const raw = await request<unknown>({
    method: "POST",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-asset-uploads/${encodeURIComponent(uploadId)}:complete-file`,
    scopeMode: "explicit",
    scope: { organizationId },
    body,
    cache: "no-store",
    signal,
  });
  return parseOrganizationWire(
    robotModelAssetUploadEnvelopeWireSchema,
    raw,
    {
      endpoint: "completeRobotModelAssetUploadFile",
    },
    organizationId,
  );
}

async function putAuthorizedAssetPart(
  url: string,
  body: Blob,
  signal: AbortSignal,
): Promise<string> {
  assertAssetTransferActive(signal);
  let response: Response;
  try {
    response = await fetch(url, {
      method: "PUT",
      body,
      cache: "no-store",
      credentials: "omit",
      signal,
    });
  } catch (error) {
    assertAssetTransferActive(signal);
    throw error;
  }
  assertAssetTransferActive(signal);
  if (!response.ok) {
    throw createAssetTransferError("ROBOT_MODEL_ASSET_PART_UPLOAD_FAILED");
  }
  const etag = response.headers.get("ETag");
  if (!etag) {
    throw createAssetTransferError("ROBOT_MODEL_ASSET_PART_ETAG_UNAVAILABLE");
  }
  return etag.replace(/^"|"$/gu, "");
}

function assertAssetTransferActive(signal: AbortSignal): void {
  if (signal.aborted)
    throw createDomainError({
      code: "NETWORK_ERROR",
      message: "请求已取消",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: null,
      retryable: false,
      httpStatus: null,
    });
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
  signal?: AbortSignal,
) {
  const lease = createSessionScopeLease({ organizationId }, signal);
  try {
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
      scopeMode: "explicit",
      scope: { organizationId },
      body,
      idempotencyKey,
      cache: "no-store",
      signal: lease.signal,
    });
    const session = parseOrganizationWire(
      robotModelAssetUploadEnvelopeWireSchema,
      raw,
      {
        endpoint: "createRobotModelAssetUploadSession",
      },
      organizationId,
    );
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
        uploadedFile.part_authorizations.map((item) => [
          item.part_number,
          item,
        ]),
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
            lease.signal,
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
          lease.signal,
        );
        completedParts.push({ part_number: partNumber, etag });
      }
      await completeRobotModelAssetFile(
        organizationId,
        session.data.upload_id,
        input.relativePath,
        completedParts,
        lease.signal,
      );
    }
    return session.data.upload_id;
  } finally {
    lease.release();
  }
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
    scopeMode: "explicit",
    scope: { organizationId },
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseOrganizationWire(
    jointMappingPageWireSchema,
    raw,
    {
      endpoint: "listRobotModelJointMappings",
    },
    organizationId,
  ).items;
}

export async function listRobotModelBindings(
  organizationId: string,
  versionId: string,
  signal?: AbortSignal,
): Promise<readonly RobotModelBinding[]> {
  const raw = await request<unknown>({
    method: "GET",
    path: `/organizations/${encodeURIComponent(organizationId)}/robots/model-bindings`,
    scopeMode: "explicit",
    scope: { organizationId },
    query: { version_id: versionId },
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseOrganizationWire(
    bindingPageWireSchema,
    raw,
    {
      endpoint: "listRobotModelBindings",
    },
    organizationId,
  ).items;
}

export interface RobotBindingTarget {
  readonly robotId: string;
}

export async function loadRobotBindingTarget(
  organizationId: string,
  target: RobotBindingTarget,
): Promise<Readonly<{ robotId: string; displayName: string; etag: string }>> {
  const robot = await getRobotBootstrap(organizationId, target.robotId);
  return {
    robotId: robot.id,
    displayName: robot.displayName,
    etag: robot.etag,
  };
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
    scopeMode: "explicit",
    scope: { organizationId },
    body,
    idempotencyKey: intent.idempotencyKey,
    cache: "no-store",
  });
  return parseWire(bindingWireSchema, raw, {
    endpoint: "bindOrganizationRobotModel",
  });
}

export interface ReplaceRobotModelJointMappingsIntent {
  readonly versionId: string;
  readonly etag: string;
  readonly mappings: readonly RobotModelJointMapping[];
  readonly idempotencyKey: string;
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
    scopeMode: "explicit",
    scope: { organizationId },
    ifMatch: intent.etag,
    idempotencyKey: intent.idempotencyKey,
    cache: "no-store",
  });
  return parseOrganizationWire(
    preflightWireSchema,
    raw,
    {
      endpoint: "preflightRobotModelPublish",
    },
    organizationId,
  ).data;
}

export interface RobotModelDangerousIntent {
  readonly versionId: string;
  readonly etag: string;
  readonly idempotencyKey: string;
  readonly preflightToken: string;
}

export interface UploadSessionIntent {
  readonly versionId: string;
  readonly files: CreateRobotModelAssetUploadRequest["files"];
  readonly idempotencyKey: string;
}

export async function listRobotModels(
  organizationId: string,
  filters: Readonly<Record<string, unknown>> = {},
  signal?: AbortSignal,
) {
  const raw = await request<unknown>({
    method: "GET",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-models`,
    scopeMode: "explicit",
    scope: { organizationId },
    query: filters,
    ...(signal ? { signal } : {}),
  });
  const page = parseOrganizationWire(
    robotModelsPageWireSchema,
    raw,
    { endpoint: "listRobotModels" },
    organizationId,
  );
  return {
    items: page.items.map(adaptRobotModel),
    pageInfo: page.page_info,
    snapshotAt: page.snapshot_at,
    requestId: page.request_id,
  };
}

export async function replaceRobotModelJointMappings(
  organizationId: string,
  intent: ReplaceRobotModelJointMappingsIntent,
): Promise<RobotModelVersion> {
  const body = {
    mappings: [...intent.mappings],
  } satisfies ReplaceRobotModelJointMappingsRequest;
  const raw = await request<unknown>({
    method: "PUT",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-versions/${encodeURIComponent(intent.versionId)}/joint-mappings`,
    scopeMode: "explicit",
    scope: { organizationId },
    body,
    ifMatch: intent.etag,
    idempotencyKey: intent.idempotencyKey,
    cache: "no-store",
  });
  return adaptRobotModelVersion(
    parseOrganizationWire(
      robotModelVersionEnvelopeWireSchema,
      raw,
      { endpoint: "replaceRobotModelJointMappings" },
      organizationId,
    ).data,
  );
}

export async function publishRobotModelVersion(
  organizationId: string,
  intent: RobotModelDangerousIntent,
): Promise<RobotModelVersion> {
  const body = {
    preflight_token: intent.preflightToken,
  } satisfies PublishRobotModelVersionRequest;
  const raw = await request<unknown>({
    method: "POST",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-versions/${encodeURIComponent(intent.versionId)}:publish`,
    scopeMode: "explicit",
    scope: { organizationId },
    body,
    idempotencyKey: intent.idempotencyKey,
    ifMatch: intent.etag,
    cache: "no-store",
  });
  return adaptRobotModelVersion(
    parseOrganizationWire(
      robotModelVersionEnvelopeWireSchema,
      raw,
      { endpoint: "publishRobotModelVersion" },
      organizationId,
    ).data,
  );
}

/** Ephemeral grants are returned directly to the caller and never enter Query Cache. */
export async function createRobotAssetUploadSession(
  organizationId: string,
  intent: UploadSessionIntent,
) {
  const raw = await request<unknown>({
    method: "POST",
    path: `/organizations/${encodeURIComponent(organizationId)}/robot-model-versions/${encodeURIComponent(intent.versionId)}/upload-sessions`,
    scopeMode: "explicit",
    scope: { organizationId },
    body: { files: intent.files } satisfies CreateRobotModelAssetUploadRequest,
    idempotencyKey: intent.idempotencyKey,
    cache: "no-store",
  });
  return parseOrganizationWire(
    robotModelAssetUploadEnvelopeWireSchema,
    raw,
    { endpoint: "createRobotModelAssetUploadSession" },
    organizationId,
  );
}
