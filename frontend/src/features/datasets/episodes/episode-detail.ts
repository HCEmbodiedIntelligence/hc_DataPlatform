import { z } from "zod";
import type { Scope } from "../../../entities/scope";
import { episodeDataReferenceWireSchema as dataWire } from "../api/episode-package-wire";
import { request } from "../../../shared/api/http-client";
import type { ReviewDataRef } from "./data-reviews";

const digest = z.string().regex(/^[0-9a-f]{64}$/u);
const count = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const extension = z.record(z.string(), z.unknown());
const action = z
  .object({
    action: z.string(),
    allowed: z.boolean(),
    required_capabilities: z.array(z.string()),
    denied_reason: z.string().nullable(),
  })
  .refine((value) => value.allowed === (value.denied_reason === null));
const fileWire = z.object({
  file_id: digest,
  path: z.string().min(1),
  size: count,
  sha256: digest,
  media_type: z.string(),
  category: z.enum([
    "NUMERIC",
    "RGB",
    "DEPTH",
    "ANNOTATION",
    "METADATA",
    "REPORT",
  ]),
  availability: z.enum(["AVAILABLE", "MISSING", "SIZE_MISMATCH"]),
  integrity: z.literal("MANIFEST_BOUND"),
  download: action,
});
const filePage = z.object({
  data: dataWire,
  items: z.array(fileWire).max(100),
  total: count,
  next_after: digest.nullable(),
});
const revision = z.object({
  task_id: z.string(),
  revision: count,
  content_hash: digest,
  author_id: z.string(),
  created_at: z.iso.datetime({ offset: true }),
});
const submitted = z
  .object({
    task_id: z.string(),
    data_ref: dataWire,
    revision: count,
    revision_content_hash: digest,
    submission_id: z.string(),
  })
  .passthrough();
const reviewed = submitted.extend({
  review_id: z.string(),
  decision: z.string(),
});
const annotation = z.object({
  availability: z.enum([
    "AVAILABLE",
    "NOT_CREATED",
    "FORBIDDEN",
    "SELECTION_REQUIRED",
  ]),
  task_options: z.array(
    z.object({
      task_id: z.string(),
      dataset_version: count,
      status: z.string(),
      current_revision: count,
      state_version: count,
    }),
  ),
  task_options_has_more: z.boolean(),
  task_id: z.string().nullable(),
  status: z.string().nullable(),
  state_version: count.nullable(),
  current_revision: revision.nullable(),
  latest_submission: submitted.nullable(),
  latest_approval: reviewed.nullable(),
  latest_review: reviewed.nullable(),
  current_submission_id: z.string().nullable(),
  current_approved_review_id: z.string().nullable(),
  has_unsubmitted_changes: z.boolean().nullable(),
  revision_files: z.array(
    z
      .object({
        revision: count,
        revision_content_hash: digest,
        status: z.string(),
        error_code: z.string().nullable(),
        download: action,
      })
      .passthrough(),
  ),
});
const track = z
  .object({
    track_id: z.string(),
    label: z.string(),
    annotation_ids: z.array(z.string()),
  })
  .passthrough();
const delivery = z
  .object({
    job_id: z.string(),
    task_id: z.string(),
    revision: count,
    revision_content_hash: digest,
    status: z.string(),
    phase: z.string(),
    segment_count: count,
    track,
    review: z
      .object({ status: z.enum(["APPROVED", "UNREVIEWED"]) })
      .passthrough(),
  })
  .passthrough();
const overview = z.object({
  data: dataWire,
  display_name: z.string(),
  display_name_origin: z.string(),
  task: z.object({
    task_id: z.string(),
    instructions: z.array(z.string()),
    requirements: z.unknown(),
    requirements_status: z.string(),
  }),
  robot_id: z.string(),
  recording_date: z.string(),
  duration_seconds: z.number().positive(),
  frequency_hz: z.number().positive(),
  camera_stream_count: count,
  physical_camera_count: count.nullable(),
  physical_camera_count_reason: z.string().nullable(),
  source: z
    .object({
      document: extension,
      parent_recording_id: z.string().nullable(),
      parent_duration_ns: z.string().regex(/^\d+$/u).nullable(),
      window: z.record(z.string(), z.string()),
      window_clock: z.string(),
      frame_mapping_files: z.array(digest),
    })
    .passthrough(),
  quality: z
    .object({
      data: dataWire,
      status: z.enum(["PASS", "RISK", "REJECT", "UNKNOWN"]),
    })
    .passthrough(),
  data_review_status: z.enum(["NOT_REVIEWED", "APPROVED", "RETURNED"]),
  latest_data_review: z
    .object({ data: dataWire, decision: z.enum(["APPROVED", "RETURNED"]) })
    .passthrough()
    .nullable(),
  annotation,
  subtasks: z.object({
    status: z.enum(["SELECTED", "NOT_SPECIFIED"]),
    delivery_id: z.string().nullable(),
    revision: count.nullable(),
    track: track.nullable(),
    segment_count: count.nullable(),
  }),
  cameras: z.array(
    z.object({
      feature: z.string(),
      kind: z.enum(["RGB", "DEPTH"]),
      shape: z.array(count),
      video_file_id: digest,
      preview_status: z.enum([
        "NOT_GENERATED",
        "PREPARING",
        "READY",
        "FAILED",
        "INTEGRITY_FAILED",
      ]),
      preview_reason: z.string().nullable(),
      depth: extension.nullable(),
    }),
  ),
  files: filePage,
  deliveries: z.array(delivery),
  deliveries_has_more: z.boolean(),
  package_status: z.literal("COMMITTED"),
  allowed_actions: z.array(action),
  observed_at: z.iso.datetime({ offset: true }),
});
export type EpisodeOverview = z.infer<typeof overview>;
export type EpisodeFile = z.infer<typeof fileWire>;
export type EpisodeFilePage = z.infer<typeof filePage>;
export type DetailSelection = {
  annotationTaskId?: string;
  deliveryId?: string;
};

function same(left: ReviewDataRef, right: ReviewDataRef) {
  return (
    JSON.stringify(dataWire.parse(left)) ===
    JSON.stringify(dataWire.parse(right))
  );
}
export function episodeEndpoint(scope: Scope, data: ReviewDataRef) {
  const id = dataWire.parse(data).identity;
  if (
    id.organization_id !== scope.organizationId ||
    id.project_id !== scope.projectId ||
    id.region_code !== scope.regionCode
  )
    throw new Error("Episode 超出当前组织、项目或区域。");
  return `/projects/${encodeURIComponent(id.project_id)}/regions/${encodeURIComponent(id.region_code)}/datasets/${encodeURIComponent(id.dataset_id)}/episodes/${encodeURIComponent(id.episode_id)}/data-versions/${encodeURIComponent(data.data_version_id)}`;
}
function checkFiles(raw: unknown, data: ReviewDataRef) {
  const value = filePage.parse(raw);
  if (
    !same(value.data, data) ||
    new Set(value.items.map((f) => f.file_id)).size !== value.items.length ||
    value.items.length > value.total ||
    (value.next_after !== null &&
      value.next_after !== value.items.at(-1)?.file_id) ||
    value.items.some(
      (f) => f.availability !== "AVAILABLE" && f.download.allowed,
    )
  )
    throw new Error("文件清单与固定数据版本或状态不一致。");
  return value;
}
export async function loadEpisodeOverview(
  scope: Scope,
  data: ReviewDataRef,
  selection: DetailSelection = {},
  signal?: AbortSignal,
) {
  const value = overview.parse(
    await request({
      method: "GET",
      path: `${episodeEndpoint(scope, data)}/overview`,
      scope,
      signal,
      cache: "no-store",
      query: {
        annotation_task_id: selection.annotationTaskId,
        delivery_id: selection.deliveryId,
      },
    }),
  );
  const ann = value.annotation;
  const references = [
    value.data,
    value.quality.data,
    ...(value.latest_data_review ? [value.latest_data_review.data] : []),
    ...[ann.latest_submission, ann.latest_approval, ann.latest_review].flatMap(
      (r) => (r ? [r.data_ref] : []),
    ),
  ];
  if (
    references.some((r) => !same(r, data)) ||
    value.camera_stream_count !== value.cameras.length ||
    (selection.annotationTaskId &&
      selection.annotationTaskId !== ann.task_id) ||
    (selection.deliveryId ?? null) !== value.subtasks.delivery_id ||
    (value.latest_data_review?.decision ?? "NOT_REVIEWED") !==
      value.data_review_status ||
    (ann.availability === "AVAILABLE"
      ? !ann.task_id || !ann.current_revision
      : ann.task_id !== null || ann.current_revision !== null) ||
    [
      ann.current_revision,
      ann.latest_submission,
      ann.latest_approval,
      ann.latest_review,
    ].some((r) => r && r.task_id !== ann.task_id) ||
    (ann.current_submission_id !== null &&
      ann.current_submission_id !== ann.latest_submission?.submission_id) ||
    (ann.current_approved_review_id !== null &&
      ann.current_approved_review_id !== ann.latest_approval?.review_id) ||
    value.deliveries.some(
      (d) =>
        d.task_id !== ann.task_id ||
        d.segment_count !== d.track.annotation_ids.length,
    ) ||
    (ann.availability !== "AVAILABLE" &&
      (ann.latest_submission !== null ||
        ann.latest_approval !== null ||
        ann.latest_review !== null ||
        ann.revision_files.length > 0 ||
        value.deliveries.length > 0)) ||
    (value.subtasks.status === "SELECTED" &&
      !value.deliveries.some(
        (job) =>
          job.job_id === value.subtasks.delivery_id &&
          job.revision === value.subtasks.revision &&
          job.track.track_id === value.subtasks.track?.track_id &&
          job.segment_count === value.subtasks.segment_count,
      )) ||
    (value.subtasks.status === "NOT_SPECIFIED"
      ? value.subtasks.segment_count !== null
      : value.subtasks.segment_count !==
        value.subtasks.track?.annotation_ids.length)
  )
    throw new Error("Episode 详情的数据版本、标注任务或审核身份不一致。");
  checkFiles(value.files, data);
  return value;
}
export async function loadEpisodeFiles(
  scope: Scope,
  data: ReviewDataRef,
  after?: string,
  signal?: AbortSignal,
) {
  return checkFiles(
    await request({
      method: "GET",
      path: `${episodeEndpoint(scope, data)}/files`,
      scope,
      signal,
      cache: "no-store",
      query: { after, limit: 50 },
    }),
    data,
  );
}
export async function authorizeEpisodeFile(
  scope: Scope,
  data: ReviewDataRef,
  file: EpisodeFile,
  signal?: AbortSignal,
) {
  if (!file.download.allowed || file.availability !== "AVAILABLE")
    throw new Error("该文件当前不可下载。");
  const grant = z
    .object({
      data: dataWire,
      file_id: digest,
      sha256: digest,
      size: count,
      url: z.url(),
      expires_seconds: z.literal(300),
    })
    .parse(
      await request({
        method: "POST",
        path: `${episodeEndpoint(scope, data)}/files/${file.file_id}/authorize`,
        scope,
        signal,
        cache: "no-store",
      }),
    );
  const url = new URL(grant.url);
  if (
    !same(grant.data, data) ||
    grant.file_id !== file.file_id ||
    grant.sha256 !== file.sha256 ||
    grant.size !== file.size ||
    !["https:", "http:"].includes(url.protocol) ||
    url.username ||
    url.password
  )
    throw new Error("文件授权与选定文件不一致。");
  return grant;
}

export function secondsFromNs(value: string | null | undefined) {
  if (!value || !/^-?\d+$/u.test(value)) return "未记录";
  const n = BigInt(value),
    magnitude = n < 0n ? -n : n;
  const fraction = (magnitude % 1_000_000_000n)
    .toString()
    .padStart(9, "0")
    .replace(/0+$/u, "");
  return `${n < 0n ? "-" : ""}${magnitude / 1_000_000_000n}${fraction ? `.${fraction}` : ""} 秒`;
}
