import { z } from "zod";
import type { components } from "../../shared/api/generated/platform";
import { request } from "../../shared/api/http-client";
import type {
  RuntimeAnnotationRevision,
  RuntimeAnnotationScope,
} from "./runtime-annotation-adapter";

export type RevisionFile = components["schemas"]["AnnotationRevisionFile"];
const digest = z.string().regex(/^[0-9a-f]{64}$/u);
const wire = z
  .object({
    task_id: z.string().min(1),
    revision: z.number().int().nonnegative(),
    revision_content_hash: digest,
    format_version: z.literal("hc-annotation/1"),
    status: z.enum([
      "NOT_REQUESTED",
      "PENDING",
      "GENERATING",
      "READY",
      "FAILED",
    ]),
    generation: z.number().int().nonnegative(),
    attempts: z.number().int().nonnegative(),
    content_sha256: digest.nullable(),
    size_bytes: z.number().int().positive().nullable(),
    error_code: z.string().nullable(),
    updated_at: z.iso.datetime({ offset: true }).nullable(),
  })
  .strict();

function filePath(revision: RuntimeAnnotationRevision) {
  return `/annotation-tasks/${encodeURIComponent(revision.task_id)}/revisions/${revision.revision}/file`;
}

function checked(
  raw: unknown,
  revision: RuntimeAnnotationRevision,
): RevisionFile {
  const file = wire.parse(raw);
  if (
    file.task_id !== revision.task_id ||
    file.revision !== revision.revision ||
    file.revision_content_hash !== revision.content_hash ||
    (file.status === "READY" && (!file.content_sha256 || !file.size_bytes))
  ) {
    throw new Error("标注文件身份与选定修订不一致。");
  }
  return file;
}

export async function loadRevisionFile(
  scope: RuntimeAnnotationScope,
  revision: RuntimeAnnotationRevision,
  signal?: AbortSignal,
) {
  return checked(
    await request({
      method: "GET",
      path: filePath(revision),
      scope,
      signal,
      cache: "no-store",
    }),
    revision,
  );
}

export async function requestRevisionFile(
  scope: RuntimeAnnotationScope,
  revision: RuntimeAnnotationRevision,
  generation: number,
) {
  return checked(
    await request({
      method: "POST",
      path: filePath(revision),
      scope,
      body: { expected_generation: generation },
      cache: "no-store",
    }),
    revision,
  );
}

export async function downloadRevisionFile(
  scope: RuntimeAnnotationScope,
  revision: RuntimeAnnotationRevision,
  file: RevisionFile,
  allowUnreviewed: boolean,
  signal?: AbortSignal,
): Promise<Blob> {
  checked(file, revision);
  if (file.status !== "READY") throw new Error("标注文件尚未就绪。");
  const blob = await request<Blob>({
    method: "GET",
    path: `/annotation-tasks/${encodeURIComponent(revision.task_id)}/annotation-file`,
    scope,
    query: { revision: revision.revision, allow_unreviewed: allowUnreviewed },
    responseType: "blob",
    cache: "no-store",
    signal,
  });
  const hash = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  const hex = Array.from(new Uint8Array(hash), (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
  if (blob.size !== file.size_bytes || hex !== file.content_sha256) {
    throw new Error("标注文件校验失败，请刷新文件状态后重试。");
  }
  signal?.throwIfAborted();
  return blob;
}
