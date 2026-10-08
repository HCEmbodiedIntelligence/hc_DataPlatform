import { z } from "zod";
import type { components } from "../../../shared/api/generated/platform";
import { request } from "../../../shared/api/http-client";
import { createSessionScopeLease } from "../../../shared/api/session-scope-lease";
import { parseWire } from "../../../shared/api/validate";
import { getShellState } from "../../../shared/scope/shell-store";
import { getRuntimeConfig } from "../../../shared/config/runtime";
import {
  robotModelAssetWire,
  robotModelAssetAuthorizationWire,
} from "../../robot-models/api/client";
import {
  logicalDataEndpoint,
  type LogicalSelection,
  type LogicalSnapshot,
} from "./logical-data";

type Schemas = components["schemas"];
export type LogicalRobotScene = z.infer<typeof sceneViewWire>;
const id = z.string().min(1);
const digest = z.string().regex(/^[a-f0-9]{64}$/u);
const sceneWire = z
  .object({
    format: z.literal("hc-robot-scene/1"),
    captured_at_stage: z.literal("PACKAGE_PLANNING"),
    robot_id: z.string(),
    status: z.enum(["FROZEN", "NO_MODEL_BINDING"]),
    binding_id: id.nullish(),
    model_id: id.nullish(),
    model_version_id: id.nullish(),
    asset_manifest_sha256: digest.nullish(),
    assets: z.array(robotModelAssetWire),
    joint_mapping_sha256: digest.nullish(),
    joint_mappings: z.array(
      z.object({
        source_joint_name: id,
        target_joint_name: id,
        direction: z.enum(["SAME", "INVERTED"]),
      }),
    ),
    calibration: z.json(),
    calibration_sha256: digest,
  })
  .superRefine((scene, ctx) => {
    const hasBinding = Boolean(
      scene.binding_id &&
        scene.model_id &&
        scene.model_version_id &&
        scene.asset_manifest_sha256 &&
        scene.joint_mapping_sha256 &&
        scene.assets.length,
    );
    if (
      (scene.status === "FROZEN"
        ? !hasBinding
        : Boolean(
            scene.binding_id ||
              scene.model_id ||
              scene.model_version_id ||
              scene.assets.length ||
              scene.joint_mappings.length ||
              scene.asset_manifest_sha256 ||
              scene.joint_mapping_sha256,
          )) ||
      new Set(scene.assets.map((a) => a.asset_id)).size !==
        scene.assets.length ||
      new Set(scene.assets.map((a) => a.relative_path)).size !==
        scene.assets.length ||
      new Set(scene.joint_mappings.map((m) => m.source_joint_name)).size !==
        scene.joint_mappings.length
    )
      ctx.addIssue({
        code: "custom",
        message: "固定模型来源、资产或映射不一致",
      });
  }) satisfies z.ZodType<Schemas["FrozenRobotScene"]>;
const sceneViewWire = z
  .object({
    snapshot_id: id,
    provenance: z.enum(["FROZEN", "MISSING_HISTORICAL"]),
    scene_sha256: digest.nullish(),
    scene: sceneWire.nullish(),
  })
  .refine((v) =>
    v.provenance === "FROZEN"
      ? Boolean(v.scene && v.scene_sha256)
      : v.scene == null && v.scene_sha256 == null,
  ) satisfies z.ZodType<Schemas["LogicalRobotScene"]>;
const assetGrantWire = z.object({
  snapshot_id: id,
  scene_sha256: digest,
  asset: robotModelAssetWire,
  authorization: robotModelAssetAuthorizationWire,
}) satisfies z.ZodType<Schemas["LogicalRobotAssetGrant"]>;

async function sceneRequest(
  selection: LogicalSelection,
  snapshotId: string,
  suffix: string,
  method: "GET" | "POST",
  signal?: AbortSignal,
) {
  const lease = createSessionScopeLease(selection.scope, signal);
  const revision = getShellState().capabilityRevision;
  const endpoint = `${logicalDataEndpoint(selection)}/robot-scene${suffix}`;
  try {
    lease.signal.throwIfAborted();
    const raw = await request<unknown>({
      method,
      path: endpoint,
      scope: selection.scope,
      scopeMode: "explicit",
      signal: lease.signal,
      cache: "no-store",
      query: {
        snapshot_token: selection.snapshotToken,
        snapshot_id: snapshotId,
      },
    });
    lease.signal.throwIfAborted();
    if (getShellState().capabilityRevision !== revision)
      throw new DOMException("Robot scene authorization changed", "AbortError");
    return { raw, endpoint };
  } finally {
    lease.release();
  }
}

export async function loadLogicalRobotScene(
  selection: LogicalSelection,
  snapshot: LogicalSnapshot,
  signal?: AbortSignal,
) {
  const { raw, endpoint } = await sceneRequest(
    selection,
    snapshot.snapshot_id,
    "",
    "GET",
    signal,
  );
  return parseWire(
    sceneViewWire.refine((v) => v.snapshot_id === snapshot.snapshot_id),
    raw,
    { endpoint },
  );
}

export async function authorizeLogicalRobotAsset(
  selection: LogicalSelection,
  view: LogicalRobotScene,
  asset: Schemas["RobotModelAsset"],
  signal?: AbortSignal,
) {
  if (
    !view.scene?.assets.some(
      (a) => a.asset_id === asset.asset_id && a.sha256 === asset.sha256,
    ) ||
    view.provenance !== "FROZEN"
  )
    throw new Error("资产不属于固定机器人场景");
  const { raw, endpoint } = await sceneRequest(
    selection,
    view.snapshot_id,
    `/assets/${encodeURIComponent(asset.asset_id)}/authorize`,
    "POST",
    signal,
  );
  const grant = parseWire(
    assetGrantWire.refine(
      (value) =>
        value.snapshot_id === view.snapshot_id &&
        value.scene_sha256 === view.scene_sha256 &&
        value.asset.asset_id === asset.asset_id &&
        value.asset.sha256 === asset.sha256 &&
        value.asset.relative_path === asset.relative_path &&
        value.asset.role === asset.role &&
        value.asset.size_bytes === asset.size_bytes &&
        value.asset.media_type === asset.media_type &&
        value.authorization.asset_id === asset.asset_id &&
        value.authorization.sha256 === asset.sha256 &&
        value.authorization.media_type === asset.media_type &&
        Date.parse(value.authorization.expires_at) > Date.now(),
    ),
    raw,
    { endpoint },
  );
  const api = new URL(
    getRuntimeConfig().apiBaseUrl,
    globalThis.location?.origin ?? "http://localhost",
  );
  const url = new URL(grant.authorization.download_url, api.origin);
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password
  )
    throw new Error("固定模型资产下载地址无效");
  return { ...grant.authorization, download_url: url.toString() };
}
