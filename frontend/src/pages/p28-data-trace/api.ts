import { z } from "zod";
import { episodeDataReferenceWireSchema } from "../../features/datasets/api/episode-package-wire";
import { request } from "../../shared/api/http-client";
import type { Scope } from "../../shared/scope/model";

const sample = z.object({
  data: episodeDataReferenceWireSchema.nullable(),
  viewer_version_id: z.string().nullable(),
});
const origin = sample.extend({
  identifier: z.string(),
  batches: z.array(
    z.object({
      batch_number: z.number().int().positive(),
      channel: z.enum(["oss", "web", "historical"]),
      created_at: z.string(),
    }),
  ),
});
const member = sample.extend({
  upload_id: z.string(),
  raw_source_ids: z.array(z.string()),
  collection_description: z.string(),
  upload_note: z.string(),
});
const page = z.object({
  items: z.array(member),
  total: z.number(),
  upload_count: z.number(),
});
export type Sample = z.infer<typeof sample>;
export type Member = z.infer<typeof member>;
export const pageSize = 20;
function base(scope: Scope) {
  return `/projects/${encodeURIComponent(scope.projectId!)}/regions/${encodeURIComponent(scope.regionCode!)}/data-trace`;
}
export async function suggestNames(scope: Scope, query: string, signal: AbortSignal) {
  return z.array(z.string()).parse(await request({
    method: "GET", path: base(scope) + "/suggestions", query: { query },
    scope, signal, cache: "no-store",
  }));
}

export async function findOrigins(
  scope: Scope,
  identifier: string,
  signal: AbortSignal,
) {
  return origin.array().parse(
    await request({
      method: "GET",
      path: base(scope) + "/origins",
      query: { identifier },
      scope,
      signal,
      cache: "no-store",
    }),
  );
}
export async function loadMembers(
  scope: Scope,
  batch: number,
  pageNumber: number,
  signal: AbortSignal,
) {
  return page.parse(
    await request({
      method: "GET",
      path: base(scope) + "/batches/" + batch,
      query: { offset: (pageNumber - 1) * pageSize, limit: pageSize },
      scope,
      signal,
      cache: "no-store",
    }),
  );
}
