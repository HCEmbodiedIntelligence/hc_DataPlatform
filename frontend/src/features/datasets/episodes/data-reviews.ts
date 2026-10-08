import { z } from "zod";
import type { Scope } from "../../../shared/scope/model";
import { episodeDataReferenceWireSchema } from "../api/episode-package-wire";
import type { components } from "../../../shared/api/generated/platform";
import { request } from "../../../shared/api/http-client";

export type ReviewDataRef = z.infer<typeof episodeDataReferenceWireSchema>;
export type DataReviewCommand =
  components["schemas"]["SubmitEpisodeDataReview"];
const sequence = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const location = z
  .object({
    start_step: sequence.nullable(),
    end_step: sequence.positive().nullable(),
    tag: z
      .object({
        task_id: z.string().min(1),
        revision: sequence,
        revision_content_hash: z.string().regex(/^[0-9a-f]{64}$/u),
        annotation_id: z.string().min(1),
      })
      .strict()
      .nullable(),
    comment: z.string().min(1),
  })
  .strict();
const review = z
  .object({
    format: z.literal("hc-episode-data-review/1"),
    review_id: z.string().min(1),
    data: episodeDataReferenceWireSchema,
    sequence: sequence.positive(),
    previous_review_id: z.string().min(1).nullable(),
    decision: z.enum(["APPROVED", "RETURNED"]),
    actor_id: z.string().min(1),
    created_at: z.iso.datetime({ offset: true }),
    comment: z.string().min(1),
    locations: z.array(location).max(100),
  })
  .strict();
const pageWire = z
  .object({
    data: episodeDataReferenceWireSchema,
    status: z.enum(["NOT_REVIEWED", "APPROVED", "RETURNED"]),
    current_sequence: sequence,
    latest: review.nullable(),
    items: z.array(review),
    next_before: sequence.positive().nullable(),
    allowed_actions: z.array(
      z
        .object({
          action: z.enum(["SUBMIT_DATA_REVIEW", "LINK_ANNOTATION_TAG"]),
          allowed: z.boolean(),
          required_capabilities: z
            .array(z.enum(["dataset_version.review", "annotation_task.read"]))
            .min(1),
          denied_reason: z.literal("MISSING_CAPABILITY").nullable(),
        })
        .strict(),
    ),
  })
  .strict();
export type DataReview = z.infer<typeof review>;
export type DataReviewPage = z.infer<typeof pageWire>;

function sameRef(left: ReviewDataRef, right: ReviewDataRef) {
  return (
    JSON.stringify(episodeDataReferenceWireSchema.parse(left)) ===
    JSON.stringify(episodeDataReferenceWireSchema.parse(right))
  );
}

/** Refuse to combine streams from different fixed data versions in one review. */
export function reviewDataReference(
  refs: readonly ReviewDataRef[],
): ReviewDataRef | null {
  const first = refs[0];
  if (!first) return null;
  if (refs.some((ref) => !sameRef(ref, first))) {
    throw new Error("数据通道绑定了不同版本，无法确定人工审核对象。");
  }
  return first;
}

function endpoint(scope: Scope, data: ReviewDataRef) {
  const identity = data.identity;
  if (
    identity.organization_id !== scope.organizationId ||
    identity.project_id !== scope.projectId ||
    identity.region_code !== scope.regionCode
  ) {
    throw new Error("人工数据审核超出当前组织、项目或区域。");
  }
  return `/projects/${encodeURIComponent(identity.project_id)}/regions/${encodeURIComponent(identity.region_code)}/datasets/${encodeURIComponent(identity.dataset_id)}/episodes/${encodeURIComponent(identity.episode_id)}/data-versions/${encodeURIComponent(data.data_version_id)}/data-reviews`;
}

function checkedReview(raw: unknown, data: ReviewDataRef): DataReview {
  const value = review.parse(raw);
  if (
    !sameRef(value.data, data) ||
    (value.sequence === 1) !== (value.previous_review_id === null) ||
    value.locations.some(
      (item) =>
        (item.start_step === null) !== (item.end_step === null) ||
        (item.start_step === null && item.tag === null) ||
        (item.start_step !== null &&
          item.end_step !== null &&
          (item.end_step <= item.start_step ||
            item.end_step > data.frame_count)),
    )
  ) {
    throw new Error("人工审核记录的数据版本或定位不一致。");
  }
  return value;
}

export async function loadDataReviews(
  scope: Scope,
  data: ReviewDataRef,
  signal?: AbortSignal,
  before?: number,
) {
  const page = pageWire.parse(
    await request<unknown>({
      method: "GET",
      path: endpoint(scope, data),
      scope,
      signal,
      cache: "no-store",
      query: { limit: 10, ...(before !== undefined ? { before } : {}) },
    }),
  );
  if (
    !sameRef(page.data, data) ||
    (page.latest === null
      ? page.current_sequence !== 0 || page.status !== "NOT_REVIEWED"
      : page.latest.sequence !== page.current_sequence ||
        page.status !== page.latest.decision) ||
    page.items.some(
      (item, index) =>
        item.sequence > page.current_sequence ||
        (before !== undefined && item.sequence >= before) ||
        (index > 0 && item.sequence >= page.items[index - 1]!.sequence),
    ) ||
    (page.next_before !== null &&
      page.next_before !== page.items.at(-1)?.sequence) ||
    page.allowed_actions.some(
      (item) => item.allowed !== (item.denied_reason === null),
    ) ||
    new Set(page.allowed_actions.map((item) => item.action)).size !==
      page.allowed_actions.length
  ) {
    throw new Error("人工审核历史与固定数据版本或当前状态不一致。");
  }
  if (page.latest) checkedReview(page.latest, data);
  page.items.forEach((item) => checkedReview(item, data));
  return page;
}

export async function recordDataReview(
  scope: Scope,
  data: ReviewDataRef,
  body: DataReviewCommand,
  idempotencyKey: string,
  signal?: AbortSignal,
) {
  const value = checkedReview(
    await request<unknown>({
      method: "POST",
      path: endpoint(scope, data),
      scope,
      body,
      idempotencyKey,
      signal,
      cache: "no-store",
    }),
    data,
  );
  if (
    value.sequence !== body.expected_sequence + 1 ||
    value.decision !== body.decision ||
    value.comment !== body.comment ||
    JSON.stringify(value.locations) !==
      JSON.stringify(
        (body.locations ?? []).map((item) =>
          location.parse({
            ...item,
            start_step: item.start_step ?? null,
            end_step: item.end_step ?? null,
            tag: item.tag ?? null,
          }),
        ),
      )
  ) {
    throw new Error("人工审核响应与提交内容不一致，请刷新审核状态。");
  }
  return value;
}
