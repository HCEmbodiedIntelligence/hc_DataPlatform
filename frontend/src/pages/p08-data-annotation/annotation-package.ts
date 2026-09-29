import { z } from "zod";
import { episodeDataReferenceWireSchema } from "../../features/datasets/api/episode-package-wire";
import { request } from "../../shared/api/http-client";
import { parseWire } from "../../shared/api/validate";
import { p06DataError } from "../p06-dataset-detail/step-window";
import type {
  RuntimeAnnotationScope,
  RuntimeAnnotationTask,
  RuntimeManifestDiscovery,
} from "./runtime-annotation-adapter";

type Ref = z.infer<typeof episodeDataReferenceWireSchema>;
type Binding = {
  readonly base_lance_version?: number | null;
  readonly data_ref?: unknown;
};

export function sameAnnotationDataRef(left: unknown, right: unknown): boolean {
  if (left == null || right == null) return left == null && right == null;
  return (
    JSON.stringify(episodeDataReferenceWireSchema.parse(left)) ===
    JSON.stringify(episodeDataReferenceWireSchema.parse(right))
  );
}

export function sameAnnotationBinding(left: Binding, right: Binding): boolean {
  return (
    left.base_lance_version === right.base_lance_version &&
    sameAnnotationDataRef(left.data_ref, right.data_ref)
  );
}

export function annotationDataLabel(task: RuntimeAnnotationTask): string {
  return task.data_ref
    ? `Episode v${task.data_ref.version}`
    : `Lance v${task.base_lance_version}`;
}

export function checkedAnnotationRef(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
): Ref | null {
  const ref = task.data_ref
    ? episodeDataReferenceWireSchema.parse(task.data_ref)
    : null;
  const legacy =
    Number.isSafeInteger(task.base_lance_version) &&
    (task.base_lance_version ?? 0) > 0;
  if (
    (!ref && !legacy) ||
    (ref && task.base_lance_version != null) ||
    (ref &&
      (ref.identity.organization_id !== scope.organizationId ||
        ref.identity.project_id !== scope.projectId ||
        ref.identity.region_code !== scope.regionCode ||
        ref.identity.dataset_id !== task.dataset_id ||
        ref.identity.rollout_id !== task.rollout_id ||
        ref.frame_count !== task.base_step_count ||
        task.creation_source !== "SYSTEM_EPISODE"))
  )
    throw p06DataError(
      "ANNOTATION_DATA_BINDING_MISMATCH",
      "标注任务的数据版本或作用域不一致。",
    );
  return ref;
}

export function annotationPackageEndpoint(ref: Ref): string {
  const id = ref.identity;
  return `/projects/${encodeURIComponent(id.project_id)}/regions/${encodeURIComponent(id.region_code)}/datasets/${encodeURIComponent(id.dataset_id)}/episodes/${encodeURIComponent(id.episode_id)}/data-versions/${encodeURIComponent(ref.data_version_id)}`;
}

const metadataSchema = z.object({
  data: episodeDataReferenceWireSchema,
  info: z.object({
    fps: z.number().positive(),
    total_frames: z.number().int().positive(),
    features: z.record(z.string(), z.object({ dtype: z.string() })),
    "hc.feature_mapping": z.record(z.string(), z.string().min(1)),
  }),
  modalities: z.object({ depth: z.record(z.string(), z.unknown()) }),
});

export interface AnnotationPackageView {
  readonly storage_format: "lerobot_v3";
  readonly data: Ref;
  readonly frequency_hz: number;
  readonly manifest: RuntimeManifestDiscovery;
}

export async function loadAnnotationPackage(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
  signal?: AbortSignal,
): Promise<AnnotationPackageView> {
  const ref = checkedAnnotationRef(scope, task);
  if (!ref)
    throw p06DataError(
      "ANNOTATION_DATA_BINDING_MISSING",
      "未提供 Episode 数据版本。",
    );
  const endpoint = annotationPackageEndpoint(ref);
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scope,
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  const metadata = parseWire(metadataSchema, raw, { endpoint });
  const mapping = Object.entries(metadata.info["hc.feature_mapping"]);
  if (
    !sameAnnotationDataRef(ref, metadata.data) ||
    metadata.info.total_frames !== ref.frame_count ||
    mapping.some(([, feature]) => !metadata.info.features[feature])
  ) {
    throw p06DataError(
      "ANNOTATION_PACKAGE_MISMATCH",
      "标准包元数据与标注任务的固定数据版本不一致。",
    );
  }
  return {
    storage_format: "lerobot_v3",
    data: ref,
    frequency_hz: metadata.info.fps,
    manifest: {
      source: "MANIFEST",
      read_only: true,
      missing_expected_topics: [],
      cameras: mapping
        .filter(
          ([, feature]) => metadata.info.features[feature]?.dtype === "video",
        )
        .map(([topic, feature]) => ({
          camera_id: feature,
          topic,
          encoding: feature in metadata.modalities.depth ? "depth" : "rgb",
        })),
      topics: mapping.map(([name]) => ({ name, required: false })),
    },
  };
}
