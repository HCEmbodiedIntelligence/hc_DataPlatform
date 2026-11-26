import { BatchQCDownload } from "../../../features/exports/BatchQCDownload";
import { Alert, Progress } from "antd";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import type { IngestScope } from "../../../entities/data-source";
import { request } from "../../../shared/api/http-client";
import styles from "../../../features/ingest/upload/upload.module.css";

const batchWire = z.object({
  batch_id: z.string(),
  batch_number: z.number().int().positive().nullish(),
  status: z.string(),
  source_name: z.string(),
  total: z.number(),
  submitted: z.number(),
  failed: z.number(),
  first_failure: z.string().nullable().optional(),
});
export type OssBatch = z.infer<typeof batchWire>;
export const batchKey = (scope: IngestScope) =>
  [
    "oss-import-batches",
    scope.organizationId,
    scope.projectId,
    scope.regionCode,
  ] as const;

export function OssBatchRecords({ scope }: { scope: IngestScope }) {
  const key = batchKey(scope);
  const batches = useQuery({
    queryKey: key,
    queryFn: async ({ signal }) => {
      return z.array(batchWire).parse(
        await request({
          method: "GET",
          scope,
          scopeMode: "explicit",
          signal,
          cache: "no-store",
          path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/lerobot-imports/oss:batches`,
        }),
      );
    },
    refetchInterval: (query) =>
      query.state.data?.some((row) =>
        ["PENDING", "RUNNING"].includes(row.status),
      )
        ? 3000
        : 15000,
  });
  if (batches.isError)
    return <Alert type="warning" showIcon title="后台导入记录暂时无法加载" />;
  if (!batches.data?.length) return null;
  return (
    <section className={styles.recordsPanel} aria-label="OSS 后台导入">
      <h2>OSS 后台导入</h2>
      {batches.data.map((batch) => {
        const done = batch.submitted + batch.failed;
        const state =
          batch.status === "PENDING"
            ? "已排队"
            : batch.status === "RUNNING"
              ? "正在提交"
              : batch.status === "SUCCEEDED"
                ? "提交完成"
                : batch.status === "CANCELLED"
                  ? "已取消"
                  : "提交异常";
        return (
          <div key={batch.batch_id}>
            <strong>{batch.batch_number ? `第 ${batch.batch_number} 次上传 · ` : ""}{batch.source_name}</strong>
            <div>
              {state} · 已提交 {batch.submitted} / {batch.total} 条
              {batch.failed > 0 ? ` · 失败 ${batch.failed} 条` : ""}
            </div>
            {batch.batch_number && batch.status !== "PENDING" && batch.status !== "RUNNING" && <BatchQCDownload scope={scope} number={batch.batch_number} />}
            <Progress
              percent={Math.round((100 * done) / batch.total)}
              size="small"
              showInfo={false}
              status={
                batch.failed
                  ? "exception"
                  : batch.status === "SUCCEEDED"
                    ? "success"
                    : "normal"
              }
            />
            {batch.first_failure && (
              <small>首条失败记录 · {batch.first_failure}</small>
            )}
          </div>
        );
      })}
    </section>
  );
}
