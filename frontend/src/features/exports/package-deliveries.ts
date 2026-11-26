import { deliveryUrl, waitForDeliveryCache } from "../../shared/lib/delivery-cache";
import { localObjectStorageUrl } from "../../shared/lib/object-storage-url";
import { z } from "zod";
import type { ExportDestination } from "./oss-export";
import { exportArchiveName } from "./archive-name";
import { MAX_EXPORT_ITEMS, transferWidth } from "../../shared/lib/ordered-transfer";
import { downloadArchive, fetchArchivePart } from "./raw-download";
import type { Scope } from "../../shared/scope/model";
import { episodeDataReferenceWireSchema as dataWire } from "../datasets/api/episode-package-wire";
import { request } from "../../shared/api/http-client";
import { isDomainError } from "../../shared/api/domain-error";
import { collectionRecordWire, completeDeliveryJobWire } from "../datasets/episodes/complete-deliveries";
import { annotationDeliveryPlanWire } from "../annotation/runtime/annotation-deliveries";

export type PackageData = z.infer<typeof dataWire>;
export type BatchMode = string;
export type PackageExportFilter = {
  stage: "annotated" | "dataset" | "qc_passed";
  uploaded_from?: string;
  uploaded_before?: string;
  batch_numbers?: number[];
  all_batches?: boolean;
};
export type AnnotationSelection = {
  task_id: string;
  revision: number;
  allow_unreviewed: boolean;
  track: {
    format: "hc-subtask-track/1";
    track_id: string;
    label: string;
    annotation_ids: string[];
  };
};
export type PackageSelection = {
  data: PackageData;
  annotation?: AnnotationSelection | null;
};
const digest = z.string().regex(/^[a-f0-9]{64}$/u);
const count = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const collectionPlan = z
  .object({
    format: z.literal("hc-collection-delivery-plan/1"),
    members: z
      .array(
        z
          .object({
            data: dataWire,
            manifest_sha256: digest,
            collection_record: collectionRecordWire.nullish(),
            annotation: annotationDeliveryPlanWire.nullable(),
            selected_depth_profiles: z.record(z.string(), digest),
            excluded_depth_features: z.array(z.string()),
            camera_calibration: z.record(z.string(), z.unknown()).optional(),
          })
          .strict(),
      )
      .min(1),
    modalities: z.enum(["rgb", "rgbd"]),
    archive_format: z
      .string()
      .regex(/^[a-z][a-z0-9_]*_collection_zip\/[1-9][0-9]*$/u),
    annotation_selection: z.literal("per_episode_fixed_selection"),
    contract_sha256: digest,
    processor_version: z.enum(["hc-lerobot-collection/1", "hc-lerobot-collection/2", "hc-lerobot-collection/3", "hc-lerobot-collection/4", "hc-lerobot-collection/5", "hc-export/1"]),
    archive_version: z.literal("hc-collection-archive/1"),
    reader_version: z.string().min(1),
  })
  .strict();
const jobWire = z.union([
  completeDeliveryJobWire,
  completeDeliveryJobWire.extend({
    plan: collectionPlan,
    total_files: count.positive().nullable(),
    total_bytes: count.positive().nullable(),
    phase: z.enum([
      "queued",
      "verifying",
      "projecting",
      "archiving",
      "uploading",
      "completed",
      "failed",
      "cancelled",
    ]),
  }),
]);
export type PackageJob = z.infer<typeof jobWire>;

function base(scope: Scope) {
  if (!scope.organizationId || !scope.projectId || !scope.regionCode)
    throw new Error("请选择组织、项目和区域。");
  return `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/episode-deliveries`;
}
export function sameData(left: PackageData, right: PackageData) {
  return (
    JSON.stringify(dataWire.parse(left)) ===
    JSON.stringify(dataWire.parse(right))
  );
}
function scopeData(data: PackageData, scope: Scope) {
  if (
    data.identity.organization_id !== scope.organizationId ||
    data.identity.project_id !== scope.projectId ||
    data.identity.region_code !== scope.regionCode
  )
    throw new Error("数据引用超出当前范围。");
}
export function checkedPackageJob(raw: unknown, scope: Scope) {
  const job = jobWire.parse(raw),
    { plan } = job;
  plan.members.forEach((m) => scopeData(m.data, scope));
  if (
    new Set(plan.members.map((m) => m.data.data_version_id)).size !==
      plan.members.length ||
    new Set(plan.members.map((m) => JSON.stringify(m.data.identity))).size !==
      plan.members.length ||
    job.processed_files > (job.total_files ?? 0) ||
    job.processed_bytes > (job.total_bytes ?? 0) ||
    (job.total_files === null) !== (job.total_bytes === null) ||
    job.uploaded_bytes > (job.size_bytes ?? 0) ||
    (job.status === "READY" &&
      (!job.artifact_sha256 ||
        !job.size_bytes ||
        !job.total_files ||
        !job.total_bytes ||
        job.phase !== "completed" ||
        job.processed_files !== job.total_files ||
        job.processed_bytes !== job.total_bytes ||
        job.uploaded_bytes !== job.size_bytes)) ||
    (job.status === "FAILED") !== (job.error_code !== null)
  )
    throw new Error("任务的固定选择或归档状态不一致。");
  if (plan.format === "hc-collection-delivery-plan/1") {
    if (plan.members.length > MAX_EXPORT_ITEMS && !plan.archive_format.startsWith("holobrain_collection_zip/"))
      throw new Error("集合的 Episode 数量超出格式限制。");
    for (const member of plan.members) {
      const a = member.annotation;
      if (
        (plan.modalities === "rgb" &&
          Object.keys(member.selected_depth_profiles).length > 0) ||
        (plan.modalities === "rgbd" &&
          member.excluded_depth_features.length > 0) ||
        (a &&
          (!sameData(a.binding.data, member.data) ||
            a.parent_manifest_sha256 !== member.manifest_sha256 ||
            (a.review.status === "APPROVED" &&
              (!a.review.review_id || !a.review.submission_id))))
      )
        throw new Error("集合的深度选择或标注绑定不一致。");
    }
  } else if (
    (plan.archive_format === "independent_episode_zip/1" &&
      plan.members.length !== 1) ||
    plan.modalities !== "all_collected" ||
    plan.members.some(
      (m, i) =>
        m.root !==
        (plan.archive_format === "independent_episode_zip/1"
          ? "episode"
          : `episodes/${i.toString().padStart(6, "0")}`),
    )
  )
    throw new Error("独立包归档布局不一致。");
  return job;
}

export async function loadPackageCandidates(
  scope: Scope,
  datasetId: string,
  signal?: AbortSignal,
  page?: { collectionId: string; offset: number },
  dataStage: "dataset" | "annotated" = "dataset",
) {
  const wire = z
    .object({
      dataset_id: z.string(),
      collection_version_id: z.string().nullable(),
      version: count.positive().nullable(),
      items: z.array(dataWire),
      total: count,
      legacy_episode_count: count,
      next_offset: count.nullable(),
    })
    .strict();
  const value = wire.parse(
    await request({
      method: "GET",
      path: base(scope) + "/candidates",
      scope,
      signal,
      cache: "no-store",
      query: {
        dataset_id: datasetId,
        data_stage: dataStage,
        limit: 25,
        ...(page
          ? { collection_version_id: page.collectionId, offset: page.offset }
          : {}),
      },
    }),
  );
  if (
    value.dataset_id !== datasetId ||
    (page && value.collection_version_id !== page.collectionId) ||
    value.items.some((d) => d.identity.dataset_id !== datasetId) ||
    (value.next_offset !== null && value.next_offset <= (page?.offset ?? 0))
  )
    throw new Error("候选数据与固定集合快照不一致。");
  value.items.forEach((d) => scopeData(d, scope));
  return value;
}
export async function loadUploadBatches(scope: Scope, signal?: AbortSignal) {
  const row = z.object({
    batch_number: count.positive(), channel: z.enum(["web", "oss", "historical"]), created_at: z.string(),
  });
  const batches: z.infer<typeof row>[] = [];
  let before: number | undefined;
  for (;;) {
    const page = z.array(row).parse(await request({
      method: "GET", path: base(scope) + "/upload-batches", scope, signal,
      query: { before, limit: 100 }, cache: "no-store",
    }));
    batches.push(...page);
    if (page.length < 100) return batches;
    const last = page[page.length - 1]!.batch_number;
    if (before !== undefined && last >= before) throw new Error("上传批次分页没有前进。");
    before = last;
  }
}

export async function loadUploadCandidates(scope: Scope, filter: PackageExportFilter,
  datasetIds: readonly string[], taskIds: readonly string[], signal?: AbortSignal, after?: string) {
  const page = z.object({
    items: z.array(z.object({data: dataWire, batch_numbers: z.array(count.positive())})),
    total: count, next_after: z.string().nullable(),
  }).parse(await request({
    method: "GET", path: base(scope) + "/upload-candidates", scope, signal, cache: "no-store",
    query: { stage: filter.stage, uploaded_from: filter.uploaded_from, uploaded_before: filter.uploaded_before,
      batch_number: filter.batch_numbers, all_batches: filter.all_batches, dataset_id: datasetIds, task_id: taskIds, after, limit: 100 },
  }));
  page.items.forEach(({data, batch_numbers}) => {
    scopeData(data, scope);
    if ((datasetIds.length && !datasetIds.includes(data.identity.dataset_id)) ||
        (filter.batch_numbers?.length && !batch_numbers.some(n => filter.batch_numbers!.includes(n))) ||
        (filter.all_batches && !batch_numbers.length)) {
      throw new Error("候选数据不属于所选导出范围。");
    }
  });
  return page;
}

export async function listPackageJobs(
  scope: Scope,
  signal?: AbortSignal,
  before?: string,
) {
  const raw = await request({
    method: "GET",
    path: base(scope),
    scope,
    signal,
    cache: "no-store",
    query: { limit: 25, ...(before ? { before } : {}) },
  });
  return z
    .array(z.unknown())
    .parse(raw)
    .map((j) => checkedPackageJob(j, scope));
}
export async function loadPackageJob(
  scope: Scope,
  expected: PackageJob,
  signal?: AbortSignal,
) {
  const pinned = checkedPackageJob(expected, scope);
  const job = checkedPackageJob(
    await request({
      method: "GET",
      path: `${base(scope)}/${encodeURIComponent(expected.job_id)}`,
      scope,
      signal,
      cache: "no-store",
    }),
    scope,
  );
  if (
    job.job_id !== expected.job_id ||
    job.generation !== expected.generation ||
    JSON.stringify(job.plan) !== JSON.stringify(pinned.plan)
  )
    throw new Error("导出任务已变化，请刷新导出记录后重试。");
  return job;
}
export async function downloadEpisodeBundle(scope: Scope, members: PackageData[], mode: BatchMode,
  filter: PackageExportFilter, signal: AbortSignal, report: (text: string, completed?: number, total?: number) => void,
  destination: ExportDestination = "zip", datasetNumbers?: ReadonlyMap<string, string>, beforeStart?: () => Promise<void>, saving?: () => void) {
  if (!scope.projectId || !scope.regionCode || !scope.organizationId || !members.length || members.length > MAX_EXPORT_ITEMS)
    throw new Error("每个压缩包可选择 1–10000 条 Episode。");
  const checkedScope = { organizationId: scope.organizationId, projectId: scope.projectId, regionCode: scope.regionCode };
  const progress = z.union([
    z.object({ index: z.number().int().min(0).max(members.length - 1), part_id: digest, reused: z.boolean() }),
    z.object({ error: z.string() }),
  ]);
  return downloadArchive(checkedScope, signal, bytes => report(`${destination === "oss" ? "已上传到 OSS" : "已下载"} ${Math.floor(bytes/1048576)} MB`),
    exportArchiveName(mode, members.map(({ identity }) => datasetNumbers?.get(identity.dataset_id) ?? identity.dataset_id)),
    async (lease, suggestedName) => {
      const partIds = new Array<string>(members.length);
      members.forEach(data => scopeData(data, scope));
      if (new Set(members.map(d => JSON.stringify([d.identity.dataset_id, d.identity.episode_id]))).size !== members.length)
        throw new Error("同一 Episode 只能选择一个数据版本。");
      let ready = 0;
      let reused = 0;
      const maxAttempts = mode === "mcap" && destination === "zip" ? 3 : 1;
      report(`已准备 Episode 0/${members.length}，已复用 0 条`);
      // Bound the request body for large selections. Each page is prepared in parallel server-side.
      for (let start = 0; start < members.length; start += 256) {
        const end = Math.min(start + 256, members.length);
        for (let attempt = 1; ready < end; attempt++) {
          await beforeStart?.();
          const remaining = Array.from({ length: end - start }, (_, i) => start + i).filter(i => partIds[i] === undefined);
          let pending = "";
          let eventError: unknown;
          const decoder = new TextDecoder();
          function line(value: string) {
            if (!value.trim()) return;
            try {
              const event = progress.parse(JSON.parse(value));
              if ("error" in event) throw new Error(event.error);
              const index = remaining[event.index];
              if (index === undefined || partIds[index] !== undefined) throw new Error("Episode 准备结果与请求不符。");
              partIds[index] = event.part_id;
              ready++;
              if (event.reused) reused++;
              report(`已准备 Episode ${ready}/${members.length}，已复用 ${reused} 条`, ready, members.length);
            } catch (error) {
              eventError = error;
              throw error;
            }
          }
          try {
            await request({
              method: "POST", path: base(scope) + "/bundle-parts", scope, scopeMode: "explicit",
              signal: lease, cache: "no-store", body: { data: remaining.map(i => members[i]), mode, export_filter: filter },
              downloadTo: new WritableStream<Uint8Array>({
                async write(chunk) {
                  await beforeStart?.();
                  const lines = (pending + decoder.decode(chunk, { stream: true })).split("\n");
                  pending = lines.pop()!;
                  lines.forEach(line);
                },
                close() { line(pending + decoder.decode()); },
              }),
            });
            if (ready !== end && attempt === maxAttempts) throw new Error("准备进度中断，请重新导出。");
          } catch (error) {
            lease.throwIfAborted();
            if (eventError) throw eventError;
            if (attempt === maxAttempts || !isDomainError(error) ||
                (error.code !== "NETWORK_ERROR" && ![502, 503, 504].includes(error.httpStatus ?? 0))) throw error;
          }
          if (ready < end) {
            report(`准备连接中断，正在重试剩余 ${members.length - ready} 条 Episode`);
            await new Promise(resolve => setTimeout(resolve, 500 * attempt));
            lease.throwIfAborted();
          }
        }
      }
      report(`正在合并 ${members.length} 条 Episode，已复用 ${reused} 条`);
      const path = base(scope) + "/bundle";
      const body = { part_ids: partIds, export_filter: filter, download_name: suggestedName };
      if (destination !== "zip" || (mode !== "mcap" && transferWidth(partIds.length) === 1)) return { path, body };
      return { path, body, async writeLocal(sink: WritableStream<Uint8Array>) {
        const { writeSelectionBundle } = await import("./mcap-bundle");
        await writeSelectionBundle(partIds, (id, active) =>
          fetchArchivePart(checkedScope, path, { ...body, part_ids: [id] }, active, beforeStart),
          sink, lease, report, mode === "mcap" ? "mcap" : "nested");
      } };
    }, "bundle", destination, beforeStart, bytes => report(`正在保存 ZIP（${Math.floor(bytes / 1048576)} MiB），完成后文件将出现在所选文件夹。`), saving);
}

export async function createPackageBatch(
  scope: Scope,
  members: PackageSelection[],
  mode: BatchMode,
  key: string,
  signal?: AbortSignal,
  filter?: PackageExportFilter,
) {
  if (!members.length) throw new Error("请至少选择一条 Episode。");
  const limit = mode === "independent" ? 100 : MAX_EXPORT_ITEMS;
  if (mode !== "holobrain" && members.length > limit)
    throw new Error(`请选择 1–${limit} 条 Episode。`);
  members.forEach((m) => scopeData(dataWire.parse(m.data), scope));
  if (mode === "independent" && members.some((m) => m.annotation))
    throw new Error("带选定标注的批量交付请选择 LeRobot 集合。");
  const expectedArchive =
    mode === "rgb" || mode === "rgbd"
      ? "lerobot_collection_zip/1"
      : mode === "independent"
        ? "independent_episode_batch_zip/1"
        : (await loadExportFormats(scope, signal)).find(
            (f) => f.id === mode,
          )?.archive_format;
  if (!expectedArchive) throw new Error("当前平台不支持所选导出格式。");
  const body =
    mode === "independent"
      ? {
          data: members.map((m) => m.data),
          archive_format: "independent_episode_batch_zip/1",
        }
      : {
          members,
          modalities: mode === "rgb" || mode === "rgbd" ? mode : "rgbd",
          ...(mode !== "rgb" && mode !== "rgbd" ? { output_format: mode } : {}),
        };
  const job = checkedPackageJob(
    await request({
      method: "POST",
      path: base(scope) + (mode === "independent" ? "" : "/collections"),
      scope,
      signal,
      cache: "no-store",
      idempotencyKey: key,
      body: filter ? { ...body, export_filter: filter } : body,
    }),
    scope,
  );
  const byVersion = new Map(members.map((member) => [member.data.data_version_id, member]));
  if (
    job.plan.members.length !== members.length ||
    job.plan.members.some(({ data }) => {
      const selected = byVersion.get(data.data_version_id);
      return !selected || !sameData(selected.data, data);
    })
  )
    throw new Error("交付任务与所选数据版本不一致。");
  if (mode === "independent") {
    if (job.plan.archive_format !== "independent_episode_batch_zip/1")
      throw new Error("交付类型与批量独立包不一致。");
  } else {
    if (
      job.plan.format !== "hc-collection-delivery-plan/1" ||
      job.plan.modalities !==
        (mode === "rgb" || mode === "rgbd" ? mode : "rgbd") ||
      job.plan.archive_format !== expectedArchive
    )
      throw new Error("集合模态与所选格式不一致。");
    for (const m of job.plan.members) {
      const wanted = byVersion.get(m.data.data_version_id)!.annotation;
      const actual = m.annotation;
      if (
        Boolean(wanted) !== Boolean(actual) ||
        (wanted &&
          actual &&
          (wanted.task_id !== actual.binding.task_id ||
            wanted.revision !== actual.binding.revision ||
            wanted.track.track_id !== actual.binding.track.track_id ||
            wanted.track.label !== actual.binding.track.label ||
            JSON.stringify([...wanted.track.annotation_ids].sort()) !==
              JSON.stringify([...actual.binding.track.annotation_ids].sort()) ||
            (!wanted.allow_unreviewed && actual.review.status !== "APPROVED")))
      )
        throw new Error("集合标注与所选固定修订/轨道不一致。");
    }
  }
  return job;
}
export async function commandPackageJob(
  scope: Scope,
  job: PackageJob,
  command: "cancel" | "retry",
  signal?: AbortSignal,
) {
  checkedPackageJob(job, scope);
  const result = checkedPackageJob(
    await request({
      method: "POST",
      path: `${base(scope)}/${job.job_id}/${command}`,
      scope,
      signal,
      cache: "no-store",
      body: { expected_generation: job.generation },
    }),
    scope,
  );
  if (
    result.job_id !== job.job_id ||
    JSON.stringify(result.plan) !== JSON.stringify(job.plan) ||
    result.generation !== job.generation + (command === "retry" ? 1 : 0) ||
    result.status !== (command === "retry" ? "PENDING" : "CANCELLED")
  )
    throw new Error("任务操作的代次或状态不一致。");
  return result;
}
export async function downloadPackageJob(
  scope: Scope,
  job: PackageJob,
  signal?: AbortSignal,
  filter?: PackageExportFilter,
) {
  checkedPackageJob(job, scope);
  if (job.status !== "READY") throw new Error("交付尚未就绪。");
  const grant = z
    .object({
      job_id: z.string(),
      artifact_sha256: digest,
      size_bytes: count.positive(),
      url: deliveryUrl,
      expires_seconds: z.literal(300),
    })
    .strict()
    .parse(
      await waitForDeliveryCache(() => request({
        method: "POST",
        path: `${base(scope)}/${job.job_id}/download`,
        ...(filter ? { body: filter } : {}),
        scope,
        signal,
        cache: "no-store",
      }), signal),
    );
  const url = new URL(grant.url, typeof window === "undefined" ? "http://localhost" : window.location.origin);
  if (
    grant.job_id !== job.job_id ||
    grant.artifact_sha256 !== job.artifact_sha256 ||
    grant.size_bytes !== job.size_bytes ||
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password
  )
    throw new Error("下载授权与归档身份不一致。");
  return { ...grant, url: localObjectStorageUrl(grant.url) };
}

const tagWire = z
  .object({
    annotation_id: z.string(),
    tag_id: z.string(),
    label: z.string().nullable().optional(),
    start_step: count,
    end_step: count.positive(),
  })
  .passthrough();
export async function loadCollectionAnnotationHistory(
  scope: Scope,
  data: PackageData,
  taskId: string,
  signal?: AbortSignal,
) {
  scopeData(data, scope);
  const history = z
    .object({
      task: z
        .object({
          task_id: z.string(),
          data_ref: dataWire,
          current_revision: count,
          approved_revision: count.nullable(),
          status: z.enum([
            "DRAFT", "SUBMITTED", "APPROVED", "NEEDS_REVISION", "REJECTED",
          ]),
        })
        .passthrough(),
      revisions: z.array(
        z
          .object({
            task_id: z.string(),
            data_ref: dataWire,
            revision: count,
            content_hash: digest,
            tags: z.array(tagWire),
          })
          .passthrough(),
      ),
    })
    .passthrough()
    .parse(
      await request({
        method: "GET",
        path: `/annotation-tasks/${encodeURIComponent(taskId)}/history`,
        scope,
        signal,
        cache: "no-store",
      }),
    );
  if (
    history.task.task_id !== taskId ||
    !sameData(history.task.data_ref, data) ||
    history.revisions.some(
      (r) =>
        r.task_id !== taskId ||
        !sameData(r.data_ref, data) ||
        r.tags.some(
          (t) => t.start_step >= t.end_step || t.end_step > data.frame_count,
        ),
    )
  )
    throw new Error("标注历史与固定数据版本不一致。");
  return history;
}

export async function loadExportFormats(scope: Scope, signal?: AbortSignal) {
  return z
    .array(
      z.object({
        id: z.string().min(1),
        title: z.string().min(1),
        archive_format: z
          .string()
          .regex(/^[a-z][a-z0-9_]*_collection_zip\/[1-9][0-9]*$/u),
        supports_annotations: z.boolean(),
        requires_originals: z.boolean(),
      }),
    )
    .parse(
      await request({
        method: "GET",
        path: base(scope) + "/formats",
        scope,
        signal,
        cache: "no-store",
      }),
    );
}
